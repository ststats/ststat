"""사이트에 나오지 않는 휴면 선수의 개인정보를 공개 조회(anon)에 내주지 않는지(운영 결정 2026-09-27)."""
import re
from pathlib import Path

SQL = Path("supabase/ststat.sql").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    a = SQL.index(start)
    return SQL[a:SQL.index(end, a)]


def test_public_player_view_hides_dormant_soop_id():
    view = _block("create or replace view public.elo_public_players", ";")
    assert re.search(r"case when tm\.affiliation = '휴면' then null else tm\.soop_id end as soop_id", view)


def test_daily_stats_public_read_skips_dormant_rows():
    assert re.search(
        r"create policy synergy_daily_public_read on public\.daily_member_stats\s+"
        r"for select to anon using \(coalesce\(affiliation, ''\) <> '휴면'\);", SQL)
    assert "for select to anon, authenticated using (true);\ngrant select on public.daily_member_stats" not in SQL


def test_live_broadcasts_public_read_only_displayed_players():
    policy = _block("create policy live_broadcasts_public_read", ");\n")
    assert "coalesce(tm.affiliation, '') <> '휴면'" in policy
