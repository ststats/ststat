from __future__ import annotations

from repositories.supabase import get_supabase

PAGE_SIZE = 1000


def _fetch_all(table: str, columns: str, order: str) -> list[dict]:
    db = get_supabase()
    rows: list[dict] = []
    start = 0
    while True:
        response = (
            db.table(table)
            .select(columns)
            .order(order)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def load_matches() -> list[dict]:
    return _fetch_all(
        "matches",
        "match_no,source_order,match_date,opponent_team,match_format,method,final_result,set_result",
        "match_no",
    )


def load_rounds() -> list[dict]:
    return _fetch_all(
        "rounds",
        "id,source_order,match_no,match_date,opponent_team,match_format,set_name,round_name,"
        "our_player,our_race,our_tier,result,opponent_player,opponent_race,opponent_tier,map_name",
        "id",
    )
