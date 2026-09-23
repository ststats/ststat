from __future__ import annotations

from pathlib import Path

from models.sync_job import JobResult
from processors.eloboard_derived import build_payload
from repositories.derived_stats import (
    activate_snapshot,
    cleanup_old_snapshots,
    create_snapshot,
    load_source_data,
    mark_failed,
    write_snapshot,
)


def run() -> JobResult:
    source = load_source_data()
    match_count = len(source['matches'])
    processor_dir = Path(__file__).resolve().parents[1] / 'processors'

    payload = build_payload(source, processor_dir)
    as_of = payload['ranking_meta']['as_of']
    snapshot_id = create_snapshot(
        as_of,
        match_count,
        {
            'source': 'elo_matches',
            'ranking_algorithm': 'staruniv_part4_exact_adapter',
            'safe_swap': True,
        },
    )
    try:
        counts = write_snapshot(snapshot_id, payload)
        activate_snapshot(snapshot_id)
    except Exception as exc:
        mark_failed(snapshot_id, str(exc))
        raise

    # Activation is the publish boundary. Cleanup is maintenance after a
    # successful publish and must never turn the only active snapshot into a
    # failed one. A later run can safely retry cleanup.
    cleanup_warning = None
    try:
        cleanup_old_snapshots(keep=3)
    except Exception as exc:
        cleanup_warning = f"{type(exc).__name__}: {exc}"[:3000]

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
            'h2h_rows': counts['h2h'],
            'race_rows': counts['race'],
            'ranked_players': counts['rankings'],
            'rating_history_rows': counts['history'],
            'safe_snapshot_swap': True,
            'cleanup_warning': cleanup_warning,
        },
    )
