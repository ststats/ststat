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


def test_live_status_matches_roster_in_db_not_by_download():
    """수집 함수는 명단 전체를 내려받지 않고 DB 함수로 겹치는 아이디만 받는다(egress 절약)."""
    from pathlib import Path
    ts = Path("supabase/functions/live-status/index.ts").read_text(encoding="utf-8")
    assert 'rpc("live_roster_match"' in ts
    assert "/rest/v1/tier_members" not in ts
    assert "revoke all on function public.live_roster_match(text[]) from public, anon, authenticated;" in SQL
    assert "grant execute on function public.live_roster_match(text[]) to service_role;" in SQL


def test_anon_gets_only_the_columns_pages_read():
    """익명 조회는 14번 한 곳에서 열 단위로만 준다(화면에 나오는 것만)."""
    head, contract = SQL.split("-- 14. 익명(anon) 공개 범위", 1)
    # 앞 절들은 anon에게 표·뷰 전체를 주지 않는다
    assert not re.search(r"grant select on (table )?public\.[\w, .]+ to anon", head)
    assert "public.rounds_effective, public.sync_jobs from anon;" in contract
    # 표 이름을 키로 모은다(열 목록이 같은 표가 둘이어도 겹치지 않게)
    grants = {table: set(cols.split(",")) for cols, table in
              re.findall(r"grant select \(([^)]+)\) on public\.(\w+) to anon;", contract.replace("\n  ", ""))}
    assert "month_start" not in grants["daily_member_stats"]
    assert grants["synergy_daily_dates"] == {"stat_date"}
    assert grants["elo_rankings"] == {"elo_id", "raw_rating", "rating", "tier", "tier_rank", "as_of"}
    assert "elo_id" not in grants["daily_member_stats"]
    # 최신 날짜 뷰는 원본 표와 같은 열만, 부르는 쪽 권한(휴면 제외 정책)으로 읽는다
    assert grants["daily_member_stats_latest"] == grants["daily_member_stats"]
    assert "public.daily_member_stats_latest" in contract.split("from anon;", 1)[0]
    assert re.search(r"create or replace view public\.daily_member_stats_latest\s+with \(security_invoker = true\)", head)
    # 대학 로고는 어느 화면에든 나오는 대학만
    assert "create policy university_logos_anon_read on public.university_logos for select to anon using (public.university_logo_shown(name));" in contract
    assert "scanned_at" not in grants["live_broadcasts_current"]
    # 선수 목록은 검색·요약 카드에 나오는 열만(승수·마지막 경기일은 화면에 없다)
    assert not {"wins", "last_match_date"} & grants["elo_public_players"]
    for hidden in ("elo_player_matches", "elo_player_stats", "elo_h2h_stats", "elo_matches", "rounds_effective"):
        assert hidden not in grants


def test_list_functions_read_as_the_caller_and_only_granted_views():
    """긴 목록 함수는 anon 권한 그대로 읽는다(security invoker) - 열 권한·행 정책을 넘지 않는다."""
    sql = SQL
    for name in ('elo_players_list', 'elo_player_match_list', 'elo_rankings_list', 'elo_player_ratings_list'):
        body = re.search(rf"create or replace function public\.{name}\(.*?\$\$(.*?)\$\$;", sql, re.S)
        assert body, name
        header = sql[body.start():body.start(1)]
        assert 'security invoker' in header and 'security definer' not in header, name
        assert re.search(rf"grant execute on function .*public\.{name}\(", sql, re.S), name
