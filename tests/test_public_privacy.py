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
    # 로그인한 계정 전체가 아니라 관리자만 휴면 행을 읽는다
    assert re.search(r"create policy synergy_daily_admin_read on public\.daily_member_stats\s+"
                     r"for select to authenticated using \(\(select public\.is_admin\(\)\)\);", SQL)


def test_live_broadcasts_public_read_only_displayed_players():
    policy = _block("create policy live_broadcasts_public_read", ");\n")
    assert "coalesce(tm.affiliation, '') <> '휴면'" in policy


def test_member_posts_table_is_public_read_and_written_only_by_service_role():
    assert "create table if not exists public.member_posts" in SQL
    assert re.search(r"create policy member_posts_public_read on public\.member_posts\s+for select to anon, authenticated using \(true\);", SQL)
    assert "revoke all on function public.replace_member_posts(jsonb, text[], text[]) from public, anon, authenticated;" in SQL
    assert "grant execute on function public.replace_member_posts(jsonb, text[], text[]) to service_role;" in SQL


def test_live_status_collects_member_posts_every_run():
    from pathlib import Path
    ts = Path("supabase/functions/live-status/index.ts").read_text(encoding="utf-8")
    assert "const POSTS_EVERY_MS = 90 * 1000;" in ts
    assert 'rpc("replace_member_posts"' in ts
