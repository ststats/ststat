from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from repositories.synergy_stats import upsert_daily_snapshot


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _convert(doc: dict) -> list[dict]:
    stat_date = str(doc.get("date") or "")
    if len(stat_date) != 10:
        return []
    month_start = stat_date[:7] + "-01"
    updated_at = doc.get("updated_at")
    sponsor_updated_at = doc.get("sponsor_updated_at")
    rows = []
    for member in doc.get("members") or []:
        soop_id = str(member.get("id") or "").strip()
        nickname = str(member.get("nickname") or "").strip()
        if not soop_id or not nickname:
            continue
        rows.append({
            "stat_date": stat_date,
            "month_start": month_start,
            "soop_id": soop_id,
            "elo_id": member.get("elo_id"),
            "nickname": nickname,
            "role": str(member.get("role") or ""),
            "affiliation": member.get("team") or None,
            "race": member.get("race") or None,
            "tier": str(member.get("tier")) if member.get("tier") not in (None, "") else None,
            "balloons": int(member.get("balloons") or 0),
            "broadcast_seconds": int(member.get("broadcast_seconds") or 0),
            "cumulative_viewers": int(member.get("cumulative_viewers") or 0),
            "sponsor_wins": int(member.get("sponsor_wins") or 0),
            "sponsor_losses": int(member.get("sponsor_losses") or 0),
            "updated_at": updated_at,
            "sponsor_updated_at": sponsor_updated_at,
        })
    return rows


def _prefer_nonempty(old, new):
    """Prefer a non-empty value without erasing a previously known value."""
    if new is None:
        return old
    if isinstance(new, str):
        new = new.strip()
        return new if new else old
    return new


def merge_duplicate_archive_rows(rows: list[dict]) -> list[dict]:
    """Merge duplicate SOOP IDs inside one archive day.

    Some historical Synergy archives contain split duplicate rows for one SOOP ID
    (for example one row with Poonggo metrics and another with sponsor W/L).
    The DB key is (stat_date, soop_id), so raw upsert would make the last row win
    and lose data. Merge first, then upsert one consolidated row.

    This does NOT invent missing historical members and does not sum cumulative
    metrics blindly; it preserves whichever non-zero/non-empty value is present.
    """
    merged: dict[str, dict] = {}
    order: list[str] = []

    for row in rows:
        soop_id = str(row.get("soop_id") or "").strip()
        if not soop_id:
            continue

        if soop_id not in merged:
            merged[soop_id] = dict(row)
            order.append(soop_id)
            continue

        dst = merged[soop_id]

        # Metadata: keep any non-empty value.
        for key in ("elo_id", "nickname", "role", "affiliation", "race", "tier", "modified_at"):
            dst[key] = _prefer_nonempty(dst.get(key), row.get(key))

        # Metrics are monthly cumulative snapshots, so summing duplicate rows can
        # double-count. Prefer a meaningful non-zero value when the other side is
        # missing/zero; if both are non-zero, keep the larger observed value.
        for key in (
            "balloons",
            "broadcast_seconds",
            "cumulative_viewers",
            "sponsor_wins",
            "sponsor_losses",
        ):
            try:
                a = int(dst.get(key) or 0)
            except (TypeError, ValueError):
                a = 0
            try:
                b = int(row.get(key) or 0)
            except (TypeError, ValueError):
                b = 0
            dst[key] = max(a, b)

    return [merged[key] for key in order]



def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy Synergy latest/archive JSON into daily_member_stats")
    parser.add_argument("synergy_root", help="Path to the local Synergy repository")
    args = parser.parse_args()
    root = Path(args.synergy_root).resolve()

    docs: dict[str, Path] = {}
    archive = root / "data" / "archive"
    if archive.exists():
        for p in archive.rglob("*.json"):
            try:
                date_key = p.stem
                if len(date_key) == 10:
                    docs[date_key] = p
            except Exception:
                continue
    latest = root / "data" / "latest.json"
    if latest.exists():
        d = _load(latest)
        if d.get("date"):
            docs[str(d["date"])] = latest

    if not docs:
        raise SystemExit(f"No Synergy archive/latest JSON found under {root}")

    total_rows = 0
    for stat_date, path in sorted(docs.items()):
        rows = _convert(_load(path))
        if not rows:
            print(f"[skip] {stat_date}: no valid rows")
            continue
        rows = merge_duplicate_archive_rows(rows)
        upsert_daily_snapshot(stat_date, rows)
        total_rows += len(rows)
        print(f"[ok] {stat_date}: {len(rows)} rows")

    print(f"Imported {len(docs)} dates / {total_rows} rows")


if __name__ == "__main__":
    main()
