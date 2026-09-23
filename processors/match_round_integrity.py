from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IntegrityIssue:
    code: str
    message: str
    round_id: int | None = None
    match_no: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrityReport:
    matches: int = 0
    rounds: int = 0
    expected_mirrors: int = 0
    issues: list[IntegrityIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.issues)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _result_ok(value: Any) -> bool:
    return _text(value) in {"승", "패", "무", "무승부", ""}


def audit_match_rounds(matches: list[dict], rounds: list[dict]) -> IntegrityReport:
    """Audit the normalized Supabase matches/rounds model.

    The modern Supabase schema already has rounds.match_no -> matches.match_no FK,
    so this function does NOT recreate the old heuristic linker and never mutates
    admin-owned match/round rows.
    """
    report = IntegrityReport(matches=len(matches), rounds=len(rounds))
    match_by_no = {
        int(row["match_no"]): row
        for row in matches
        if row.get("match_no") is not None
    }

    for row in rounds:
        rid = row.get("id")
        match_no = row.get("match_no")
        try:
            match_no_int = int(match_no) if match_no is not None else None
        except (TypeError, ValueError):
            match_no_int = None

        matched = match_by_no.get(match_no_int) if match_no_int is not None else None
        if matched is None:
            report.issues.append(IntegrityIssue(
                code="orphan_round",
                message="rounds.match_no에 대응하는 match가 없습니다.",
                round_id=rid,
                match_no=match_no_int,
            ))
            continue

        # These columns are intentionally NOT auto-fixed here. They are admin-owned.
        pairs = [
            ("match_date", "match_date"),
            ("opponent_team", "opponent_team"),
            ("match_format", "match_format"),
        ]
        for round_key, match_key in pairs:
            rv = _text(row.get(round_key))
            mv = _text(matched.get(match_key))
            if rv and mv and rv != mv:
                report.issues.append(IntegrityIssue(
                    code=f"{round_key}_mismatch",
                    message=f"round와 match의 {round_key} 값이 다릅니다.",
                    round_id=rid,
                    match_no=match_no_int,
                    details={"round": rv, "match": mv},
                ))

        our_player = _text(row.get("our_player"))
        opp_player = _text(row.get("opponent_player"))
        if _text(row.get("opponent_team")) == "내전":
            if our_player and opp_player:
                report.expected_mirrors += 1
            else:
                report.issues.append(IntegrityIssue(
                    code="invalid_internal_round",
                    message="내전 라운드에 우리 선수 또는 상대 선수가 비어 있습니다.",
                    round_id=rid,
                    match_no=match_no_int,
                ))

        if not _result_ok(row.get("result")):
            report.issues.append(IntegrityIssue(
                code="unknown_result",
                message="알 수 없는 경기 결과 값입니다.",
                round_id=rid,
                match_no=match_no_int,
                details={"result": row.get("result")},
            ))

    return report
