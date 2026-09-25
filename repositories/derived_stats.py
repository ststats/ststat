from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from repositories.supabase import get_supabase, new_supabase

PAGE = 1000
MATCH_COLUMNS = 'elo_match_id,match_date,winner_elo_id,loser_elo_id,map_id,category_id'
# 경기 전체(수십만 행)를 ID 구간으로 나눠 동시에 읽는다. 구간을 일꾼 수보다 잘게 나눠
# ID가 고르게 퍼지지 않아도 한 일꾼에 일이 몰리지 않게 한다.
LOAD_WORKERS = 6
LOAD_RANGES = 24
LOAD_ATTEMPTS = 3
WRITE_WORKERS = 4
WRITE_CHUNK = 1000
# 러너가 죽어 활성화되지 못한 'building' 스냅샷은 이 시간이 지나면 지운다(작업 제한 45분).
ORPHAN_BUILDING_AFTER = timedelta(hours=3)

_local = threading.local()


def _thread_client():
    """일꾼 스레드마다 연결을 따로 둔다(한 연결을 여러 스레드가 나눠 쓰지 않게)."""
    client = getattr(_local, 'client', None)
    if client is None:
        client = _local.client = new_supabase()
    return client


def _paged(table: str, select: str = '*', order: str | None = None):
    db = get_supabase()
    out = []
    start = 0
    while True:
        q = db.table(table).select(select)
        if order:
            q = q.order(order)
        rows = q.range(start, start + PAGE - 1).execute().data or []
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        start += PAGE


def load_source_data():
    return {
        'categories': _paged('elo_categories', 'category_id,name', 'category_id'),
        'maps': _paged('elo_maps', 'map_id,name', 'map_id'),
        'players': _paged('elo_players', 'elo_id,name,race', 'elo_id'),
        'matches': load_matches(),
        'tier_members': _paged(
            'tier_members',
            'elo_id,nickname,name,soop_id,tier,affiliation,race,modified_at,'
            'promoted_tier_8,promoted_tier_7,promoted_tier_6,promoted_tier_5,'
            'promoted_tier_4,promoted_tier_3,promoted_tier_2,promoted_tier_1,promoted_tier_0',
            'id',
        ),
    }


def _load_match_range(after: int, upto: int) -> list[dict]:
    """(after, upto] 구간을 ID 순서대로 끝까지 읽는다. offset 대신 마지막 ID로 이어 읽어 깊은 페이지도 빠르다.
    빈 페이지가 나올 때까지 읽으므로 서버의 한 번에 주는 행 수 제한이 PAGE보다 작아도 빠뜨리지 않는다."""
    db = _thread_client()
    out = []
    while True:
        rows = db.table('elo_matches').select(MATCH_COLUMNS).gt('elo_match_id', after).lte(
            'elo_match_id', upto).order('elo_match_id').limit(PAGE).execute().data or []
        if not rows:
            return out
        out.extend(rows)
        after = rows[-1]['elo_match_id']


def _split_ranges(low: int, high: int, parts: int) -> list[tuple[int, int]]:
    """low..high(포함)를 겹치지 않는 (after, upto] 구간들로 나눈다."""
    span = high - low + 1
    step = max(1, -(-span // parts))
    ranges = []
    after = low - 1
    while after < high:
        upto = min(high, after + step)
        ranges.append((after, upto))
        after = upto
    return ranges


def _load_matches_once() -> list[dict]:
    db = get_supabase()
    first = db.table('elo_matches').select('elo_match_id', count='exact').order(
        'elo_match_id').limit(1).execute()
    total = first.count or 0
    if not first.data:
        if total:
            raise RuntimeError(f'elo_matches count={total} but no first row')
        return []
    low = first.data[0]['elo_match_id']
    high = db.table('elo_matches').select('elo_match_id').order(
        'elo_match_id', desc=True).limit(1).execute().data[0]['elo_match_id']

    with ThreadPoolExecutor(LOAD_WORKERS) as pool:
        parts = list(pool.map(lambda r: _load_match_range(*r), _split_ranges(low, high, LOAD_RANGES)))
    rows = [row for part in parts for row in part]  # 구간이 오름차순이라 합친 결과도 ID 순서다

    # 한 페이지라도 덜 받거나(서버 행 제한 등) 겹쳐 받으면 계산이 틀어지므로 정확한 행 수와 맞춰 본다.
    if len(rows) != total or len({row['elo_match_id'] for row in rows}) != len(rows):
        raise RuntimeError(f'elo_matches load mismatch: loaded={len(rows)} expected={total}')
    return rows


def load_matches() -> list[dict]:
    last_error = None
    for _ in range(LOAD_ATTEMPTS):
        try:
            return _load_matches_once()
        except RuntimeError as exc:
            last_error = exc
    raise last_error


def load_active_history_cache(expected_metadata: dict) -> dict | None:
    """입력 지문과 계산 버전이 같은 활성 스냅샷의 월별 이력을 복원한다."""
    db = get_supabase()
    active = db.table('elo_derived_snapshots').select('snapshot_id,metadata').eq(
        'status', 'active').limit(1).execute().data or []
    if not active:
        return None
    row = active[0]
    metadata = row.get('metadata') or {}
    required = ('history_cache_version', 'closed_history_fingerprint')
    if any(metadata.get(key) != expected_metadata.get(key) for key in required):
        return None

    snapshot_id = row['snapshot_id']
    history = []
    start = 0
    while True:
        batch = db.table('elo_rating_history').select('elo_id,month_end,rating').eq(
            'snapshot_id', snapshot_id).order('month_end').order('elo_id').range(
                start, start + PAGE - 1).execute().data or []
        history.extend(batch)
        if len(batch) < PAGE:
            break
        start += PAGE
    months = sorted({str(r['month_end'])[:7] for r in history})
    by_player = {}
    for item in history:
        pid = str(item['elo_id'])
        by_player.setdefault(pid, {})[str(item['month_end'])[:7]] = float(item['rating'])
    return {
        'months': months,
        'players': {pid: [values.get(month) for month in months] for pid, values in by_player.items()},
    }


def create_snapshot(as_of: str, source_match_count: int, metadata: dict) -> str:
    sid = str(uuid.uuid4())
    get_supabase().table('elo_derived_snapshots').insert({
        'snapshot_id': sid,
        'as_of': as_of,
        'status': 'building',
        'source_match_count': source_match_count,
        'metadata': metadata,
    }).execute()
    return sid


def mark_failed(snapshot_id: str, error: str):
    get_supabase().table('elo_derived_snapshots').update({
        'status': 'failed',
        'metadata': {'error': error[:3000]},
    }).eq('snapshot_id', snapshot_id).eq('status', 'building').execute()


def _insert_chunk(table: str, payload: list[dict]):
    _thread_client().table(table).insert(payload).execute()


def _insert(table: str, rows: list[dict], snapshot_id: str, chunk: int = WRITE_CHUNK):
    """'building' 스냅샷에 넣는 것이라 순서가 상관없어 조각들을 동시에 보낸다.
    하나라도 실패하면 예외가 올라가 스냅샷이 실패로 표시되고 활성화되지 않는다."""
    chunks = [
        [dict(r, snapshot_id=snapshot_id) for r in rows[i:i + chunk]]
        for i in range(0, len(rows), chunk)
    ]
    with ThreadPoolExecutor(WRITE_WORKERS) as pool:
        for future in [pool.submit(_insert_chunk, table, payload) for payload in chunks]:
            future.result()
    return len(rows)


def write_snapshot(snapshot_id: str, payload: dict) -> dict:
    counts = {
        'player_stats': _insert('elo_player_stats', payload['player_stats'], snapshot_id),
        'h2h': _insert('elo_h2h_stats', payload['h2h'], snapshot_id),
        'race': _insert('elo_race_stats', payload['race_stats'], snapshot_id),
        'rankings': _insert('elo_rankings', payload['rankings'], snapshot_id),
        'player_ratings': _insert('elo_player_ratings', payload['player_ratings'], snapshot_id),
        'history': _insert('elo_rating_history', payload['rating_history'], snapshot_id),
    }
    meta = dict(payload['ranking_meta'], snapshot_id=snapshot_id)
    get_supabase().table('elo_ranking_meta').insert(meta).execute()
    counts['meta'] = 1
    return counts


def activate_snapshot(snapshot_id: str):
    get_supabase().rpc('activate_elo_derived_snapshot', {'p_snapshot': snapshot_id}).execute()


def cleanup_old_snapshots(keep: int = 1):
    """활성 스냅샷 말고 지난 스냅샷은 keep개만 남긴다(되돌리기용 하나면 충분하고, 하나가 수만 행이라 DB 용량을 먹는다)."""
    db = get_supabase()
    rows = db.table('elo_derived_snapshots').select('snapshot_id,status,created_at').order('created_at', desc=True).execute().data or []
    retired = [r for r in rows if r.get('status') in ('retired', 'failed')]
    for row in retired[keep:]:
        db.table('elo_derived_snapshots').delete().eq('snapshot_id', row['snapshot_id']).execute()
    # 러너가 쓰는 도중 죽으면 'building' 스냅샷과 그 행들이 남는다. 충분히 오래된 것만 지운다
    # (파이프라인은 한 번에 하나만 돌아 지금 만드는 스냅샷은 이 시간보다 새롭다).
    cutoff = datetime.now(timezone.utc) - ORPHAN_BUILDING_AFTER
    for row in rows:
        if row.get('status') == 'building' and _parse_ts(row.get('created_at')) < cutoff:
            db.table('elo_derived_snapshots').delete().eq('snapshot_id', row['snapshot_id']).eq(
                'status', 'building').execute()


def _parse_ts(value) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    ts = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
