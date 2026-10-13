from __future__ import annotations

import hashlib
import time
from pathlib import Path

from models.sync_job import JobResult
from processors.eloboard_derived import build_payload, history_cache_metadata
from repositories.derived_stats import (
    activate_snapshot,
    cleanup_old_snapshots,
    create_snapshot,
    load_active_history_cache,
    load_active_snapshot,
    load_source_data,
    load_source_signature,
    mark_failed,
    snapshot_has_data,
    write_snapshot,
)


# 계산 코드가 바뀌면 입력이 같아도 다시 계산해야 하므로 지문에 코드도 넣는다(줄바꿈 차이는 무시).
_CODE_FILES = ('processors/eloboard_derived.py', 'processors/staruniv_ranking.py')


def _code_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for rel in _CODE_FILES:
        digest.update((root / rel).read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()[:16]


def run() -> JobResult:
    timings = {}
    started = time.monotonic()

    def lap(name):
        nonlocal started
        now = time.monotonic()
        timings[name] = round(now - started, 1)
        started = now

    # 게시가 계속 실패해도 실패·building 스냅샷이 쌓이지 않게 먼저 정리한다. 정리 실패는 계산을 막지 않는다.
    try:
        cleanup_old_snapshots(keep=1)
    except Exception as exc:
        print(f"pre-run snapshot cleanup skipped: {exc}")

    # 입력과 계산 코드가 활성 스냅샷 때와 같으면 건너뛴다. 백업 복구 뒤처럼 통계 표가 비었으면 다시 계산한다.
    db_signature = load_source_signature()
    source_signature = f'{db_signature}#{_code_fingerprint()}' if db_signature else None
    if source_signature:
        active = load_active_snapshot()
        if (active and (active.get('metadata') or {}).get('source_signature') == source_signature
                and snapshot_has_data(active['snapshot_id'])):
            lap('signature_seconds')
            return JobResult(
                records_skipped=1,
                source_cursor=str(active.get('as_of') or ''),
                metadata={'skipped': 'source unchanged', 'snapshot_id': active['snapshot_id'],
                          'timings': timings},
            )
    lap('signature_seconds')

    source = load_source_data()
    lap('load_seconds')
    match_count = len(source['matches'])

    cache_metadata = history_cache_metadata(source)
    history_cache = load_active_history_cache(cache_metadata)
    lap('history_cache_seconds')
    payload = build_payload(source, history_cache=history_cache)
    lap('compute_seconds')
    as_of = payload['ranking_meta']['as_of']
    snapshot_id = create_snapshot(
        as_of,
        match_count,
        {
            'source': 'elo_matches',
            'ranking_algorithm': 'staruniv_current_tier_delta_race_v4',
            'history_cache_reused': bool(history_cache),
            'source_signature': source_signature,
            **cache_metadata,
        },
    )
    try:
        counts = write_snapshot(snapshot_id, payload)
        lap('write_seconds')
        activate_snapshot(snapshot_id)
    except Exception as exc:
        mark_failed(snapshot_id, str(exc))
        raise

    # 게시는 활성화로 끝났다. 정리 실패가 하나뿐인 활성 스냅샷을 실패로 만들면 안 된다.
    cleanup_warning = None
    try:
        cleanup_old_snapshots(keep=1)
    except Exception as exc:
        cleanup_warning = f"{type(exc).__name__}: {exc}"[:3000]
    lap('activate_cleanup_seconds')

    written = sum(counts.values())
    return JobResult(
        records_read=match_count,
        records_written=written,
        records_skipped=0,
        source_cursor=as_of,
        metadata={
            'snapshot_id': snapshot_id,
            'as_of': as_of,
            'player_stats': counts['player_stats'],
            'ranked_players': counts['rankings'],
            'rated_players': counts['player_ratings'],
            'rating_history_rows': counts['history'],
            'history_cache_reused': bool(history_cache),
            'cleanup_warning': cleanup_warning,
            'timings': timings,
        },
    )
