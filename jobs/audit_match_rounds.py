from __future__ import annotations

from models.sync_job import JobResult
from processors.match_round_integrity import audit_match_rounds
from repositories.match_rounds import load_matches, load_rounds

# Mismatches are reported but not auto-fixed because matches/rounds are admin-owned.
# Hard failure is reserved for structural problems that should be impossible with the FK.
HARD_ERROR_CODES = {"orphan_round"}


def run() -> JobResult:
    matches = load_matches()
    rounds = load_rounds()
    report = audit_match_rounds(matches, rounds)

    hard = [issue for issue in report.issues if issue.code in HARD_ERROR_CODES]
    if hard:
        sample = [
            {
                "code": i.code,
                "round_id": i.round_id,
                "match_no": i.match_no,
                "message": i.message,
            }
            for i in hard[:10]
        ]
        raise RuntimeError(
            f"match/round structural integrity failed: {len(hard)} hard issue(s); sample={sample}"
        )

    issue_counts: dict[str, int] = {}
    for issue in report.issues:
        issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1

    samples = [
        {
            "code": issue.code,
            "round_id": issue.round_id,
            "match_no": issue.match_no,
            "message": issue.message,
            "details": issue.details,
        }
        for issue in report.issues[:30]
    ]

    return JobResult(
        records_read=report.matches + report.rounds,
        records_written=0,
        records_skipped=report.error_count,
        metadata={
            "matches": report.matches,
            "rounds": report.rounds,
            "expected_internal_mirrors": report.expected_mirrors,
            "issue_counts": issue_counts,
            "issue_samples": samples,
            "base_rows_modified": False,
            "note": (
                "Modern Supabase already links rounds to matches by FK. "
                "This job audits only; admin-owned rows are never rewritten."
            ),
        },
    )
