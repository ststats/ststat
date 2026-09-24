from __future__ import annotations

import uuid
from datetime import datetime, timezone

from repositories.supabase import get_supabase

PAGE = 1000


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
        'matches': _paged('elo_matches', 'elo_match_id,match_date,winner_elo_id,loser_elo_id,map_id,category_id', 'elo_match_id'),
        'tier_members': _paged(
            'tier_members',
            'elo_id,nickname,name,soop_id,tier,affiliation,race,modified_at,'
            'promoted_tier_8,promoted_tier_7,promoted_tier_6,promoted_tier_5,'
            'promoted_tier_4,promoted_tier_3,promoted_tier_2,promoted_tier_1,promoted_tier_0',
            'id',
        ),
    }


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


def _insert(table: str, rows: list[dict], snapshot_id: str, chunk: int = 500):
    db = get_supabase()
    for i in range(0, len(rows), chunk):
        payload = [dict(r, snapshot_id=snapshot_id) for r in rows[i:i + chunk]]
        db.table(table).insert(payload).execute()
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


def cleanup_old_snapshots(keep: int = 3):
    db = get_supabase()
    rows = db.table('elo_derived_snapshots').select('snapshot_id,status,created_at').order('created_at', desc=True).execute().data or []
    retired = [r for r in rows if r.get('status') in ('retired', 'failed')]
    for row in retired[keep:]:
        db.table('elo_derived_snapshots').delete().eq('snapshot_id', row['snapshot_id']).execute()
