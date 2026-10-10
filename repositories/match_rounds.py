from __future__ import annotations

from repositories.supabase import fetch_all, get_supabase


def load_matches() -> list[dict]:
    db = get_supabase()
    return fetch_all(lambda: db.table("matches").select(
        "match_no,source_order,match_date,opponent_team,match_format,method,final_result,set_result"
    ).order("match_no"))


def load_rounds() -> list[dict]:
    db = get_supabase()
    return fetch_all(lambda: db.table("rounds").select(
        "id,source_order,match_no,match_date,opponent_team,match_format,set_name,round_name,"
        "our_player,our_race,our_tier,result,opponent_player,opponent_race,opponent_tier,map_name"
    ).order("id"))
