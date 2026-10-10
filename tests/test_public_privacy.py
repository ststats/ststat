"""사이트에 나오지 않는 휴면 선수의 개인정보를 공개 조회(anon)에 내주지 않는지(운영 결정 2026-09-27)."""
import re
from pathlib import Path

SQL = Path("supabase/ststat.sql").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    a = SQL.index(start)
    return SQL[a:SQL.index(end, a)]


def test_public_player_view_gives_dormant_soop_id_for_search_photo():
    """휴면 선수도 상대전적·분석 검색 화면에 나오므로 프로필 사진용 SOOP ID를 준다(운영 결정 2026-10-01)."""
    view = _block("create or replace view public.elo_public_players", ";")
    assert "tm.soop_id," in view and "then null else tm.soop_id" not in view


def test_daily_stats_public_read_skips_dormant_rows():
    # 공개 목록(공개 읽기 함수)은 그날 소속이 휴면인 행을 뺀다
    daily = SQL.split("create or replace function public.api_daily_stats", 1)[1].split("$$;", 1)[0]
    assert "coalesce(s.affiliation, '') <> '휴면'" in daily
    # 로그인한 계정 전체가 아니라 관리자만 휴면 행을 읽는다
    assert re.search(r"create policy synergy_daily_admin_read on public\.daily_member_stats\s+"
                     r"for select to authenticated using \(\(select public\.is_admin\(\)\)\);", SQL)


def test_live_broadcasts_public_read_only_displayed_players():
    policy = _block("create policy live_broadcasts_public_read", ");\n")
    assert "coalesce(tm.affiliation, '') <> '휴면'" in policy


def test_member_posts_table_is_public_read_and_written_only_by_service_role():
    assert "create table if not exists public.member_posts" in SQL
    assert re.search(r"create policy member_posts_public_read on public\.member_posts\s+for select to authenticated using \(true\);", SQL)
    assert "revoke all on function public.replace_member_posts(jsonb, text[], text[]) from public, anon, authenticated;" in SQL
    assert "grant execute on function public.replace_member_posts(jsonb, text[], text[]) to service_role;" in SQL


def test_live_status_collects_member_posts_every_run():
    from pathlib import Path
    ts = Path("supabase/functions/live-status/index.ts").read_text(encoding="utf-8")
    assert "const POSTS_EVERY_MS = 90 * 1000;" in ts
    assert 'rpc("replace_member_posts"' in ts
    # 게시판은 동시에 몇 개씩만 묻고, 모은 시각은 수집 상태 행에서 읽는다(글 행은 바뀐 것만 고친다)
    assert "POSTS_CONCURRENCY" in ts and "posts_scanned_at" in ts
    assert "member_posts?select=scanned_at" not in ts


def test_member_posts_write_only_changes():
    fn = SQL[SQL.index("create or replace function public.replace_member_posts"):]
    fn = fn[:fn.index("$$;")]
    # 받은 멤버의 글을 통째로 지우지 않고, 내용이 달라진 글만 고친다
    assert "is distinct from (excluded.reg_date, excluded.total_pages, excluded.post)" in fn
    assert "and not exists (select 1 from _mp_new n" in fn
    assert "alter table public.live_scan_state add column if not exists posts_scanned_at timestamptz;" in SQL


def test_live_status_matches_roster_in_db_not_by_download():
    """수집 함수는 명단 전체를 내려받지 않고 DB 함수로 겹치는 아이디만 받는다(egress 절약)."""
    from pathlib import Path
    ts = Path("supabase/functions/live-status/index.ts").read_text(encoding="utf-8")
    assert 'rpc("live_roster_match"' in ts
    assert "/rest/v1/tier_members" not in ts
    assert "revoke all on function public.live_roster_match(text[]) from public, anon, authenticated;" in SQL
    assert "grant execute on function public.live_roster_match(text[]) to service_role;" in SQL


def test_anon_reads_no_table_or_view():
    """익명(anon)은 표·뷰를 하나도 직접 읽지 않는다 - 14번에서 전부 회수하고, 공개 읽기 함수로만 받는다."""
    head, contract = SQL.split("-- 14. 익명(anon) 공개 범위", 1)
    # 앞 절들은 anon에게 표·뷰 전체를 주지 않는다
    assert not re.search(r"grant select on (table )?public\.[\w, .]+ to anon", head)
    assert "public.rounds_effective, public.sync_jobs from anon;" in contract
    # 표 이름을 키로 모은다(열 목록이 같은 표가 둘이어도 겹치지 않게)
    grants = {table: set(cols.split(",")) for cols, table in
              re.findall(r"grant select \(([^)]+)\) on public\.(\w+) to anon;", contract.replace("\n  ", ""))}
    assert grants == {}
    assert not re.search(r"grant select[^;]* to anon", SQL)
    for fn_only in ("daily_member_stats", "synergy_daily_dates", "live_broadcasts",
                    "member_posts", "elo_ranking_meta", "elo_rating_history",
                    "elo_public_players", "elo_public_matches", "elo_rankings", "elo_player_ratings"):
        assert fn_only not in grants, fn_only
        assert f"public.{fn_only}" in contract.split("from anon;", 1)[0], fn_only
    # 표 정책도 anon에게는 주지 않는다(권한이 없어 걸릴 일이 없고, 공개 범위는 함수 한 곳에서 정한다)
    assert not re.search(r"create policy[^;]*\bto anon\b", SQL)
    assert not re.search(r"for select to anon\b", SQL)
    # 대학 로고는 어느 화면에든 나오는 대학만(공개 읽기 함수가 거른다)
    logos = SQL.split("create or replace function public.api_university_logos", 1)[1].split("$$;", 1)[0]
    assert "public.university_logo_shown(l.name)" in logos
    # 선수 목록 함수는 검색·요약 카드에 나오는 열만(승수·마지막 경기일은 화면에 없다)
    fn = SQL.split("create or replace function public.elo_players_list", 1)[1].split("$$;", 1)[0]
    assert "wins" not in fn and "last_match_date" not in fn


def test_elo_list_functions_are_the_only_way_to_read_elo():
    """ELO 긴 목록 함수는 정의자 권한(anon은 표·뷰를 못 읽는다)이라 표 정책의 거르기(활성 스냅샷)를 함수에 적는다."""
    sql = SQL
    for name in ('elo_players_list', 'elo_player_match_list', 'elo_rankings_list', 'elo_player_ratings_list'):
        body = re.search(rf"create or replace function public\.{name}\(.*?\$\$(.*?)\$\$;", sql, re.S)
        assert body, name
        header = sql[body.start():body.start(1)]
        assert 'security definer set search_path = public' in header and 'security invoker' not in header, name
        assert re.search(rf"grant execute on function .*public\.{name}\(", sql, re.S), name
        if name in ('elo_rankings_list', 'elo_player_ratings_list'):
            assert "where snapshot_id = (select public.active_elo_snapshot_id())" in body.group(1), name
    # 선수·경기 뷰는 소유자 권한 뷰라 정의 안에서 활성 스냅샷만 고른다
    view = SQL.split("create or replace view public.elo_public_players", 1)[1].split(";", 1)[0]
    assert "join public.elo_derived_snapshots s on s.status='active'" in view


def test_live_status_rejects_malformed_first_page():
    """첫 쪽 건수가 이상하거나 건수는 있는데 목록이 비면 빈 결과로 덮지 않고 실패로 기록한다."""
    from pathlib import Path
    ts = Path("supabase/functions/live-status/index.ts").read_text(encoding="utf-8")
    assert "!Number.isFinite(totalCnt)" in ts
    assert "totalCnt > 0 && first.broad.length === 0" in ts
    assert "const uid = b && b.user_id;" in ts


def test_player_specific_reads_go_through_functions():
    """선수를 지정한 조회(프로필 등)는 휴면이어도 주되, 화면에 나오는 열만 돌려주는 함수로만."""
    for fn in ("player_stats(text, date)", "player_profile_stats(text, date)", "player_live(text)"):
        assert f"revoke all on function public.{fn} from public;" in SQL
        assert f"grant execute on function public.{fn} to anon, authenticated;" in SQL
    # 목록 함수는 생년월일·종족 대신 생일 달만
    daily = SQL.split("create or replace function public.api_daily_stats", 1)[1].split("$$;", 1)[0]
    assert "birth_date" not in daily and "race" not in daily and "'birth_month'" in daily


def test_race_is_normalized_where_ststat_writes_it():
    """EloBoard 선수·일별 통계·대기 명단 종족은 staruniv.sql의 normalize_race로 맞춘다(T와 테란이 섞이지 않게).
    upsert_elo_batch는 맞춘 값으로 비교해야 매번 모든 선수를 다시 쓰지 않는다."""
    batch = _block("create or replace function public.upsert_elo_batch", "$$;")
    assert "public.normalize_race(r.race)" in batch
    assert "public.normalize_race(x.race)" in SQL and "public.normalize_tier(x.tier)" in SQL
    assert "create trigger tier_member_candidates_normalize_race" in SQL


def test_public_api_functions_keep_the_same_filters_as_table_policies():
    """StarUniv 공개 페이지가 부르는 읽기 함수(api_*): 정의자 권한이라 표 정책 대신 같은 거르기를 함수에 적는다."""
    block = _block("-- 공개 읽기 함수(/api/v1): StarUniv", "to anon, authenticated;")
    names = re.findall(r"create or replace function public\.(api_\w+)\(", block)
    assert set(names) == {"api_university_logos", "api_daily_stats", "api_live", "api_live_ids", "api_stats_dates", "api_member_posts",
                          "api_recent_posts", "api_elo_rating_range", "api_elo_rating_history", "api_elo_ranking_meta"}
    assert block.count("security definer set search_path = public") == len(names)
    grant = block[block.index("grant execute on function"):]
    for name in names:
        assert f"public.{name}(" in grant
    # 방송은 5분 안·사이트에 나오는 선수(휴면 아님)만, 통계 날짜는 휴면 행을 빼고, ELO는 활성 스냅샷만, 로고는 화면에 나오는 대학만
    assert block.count("b.scanned_at > now() - interval '5 minutes'") == 2
    assert block.count("coalesce(tm.affiliation, '') <> '휴면'") == 2
    assert "from public.daily_member_stats where coalesce(affiliation, '') <> '휴면'" in block
    assert block.count("snapshot_id = (select public.active_elo_snapshot_id())") == 3
    assert "and public.university_logo_shown(l.name)" in block
    # 홈 카드 최근 글은 카드 칸만, 개수는 1~50
    assert "'thumb', p.post->'photos'->0->>'url'" in block
    assert "limit least(greatest(coalesce(p_limit, 6), 1), 50)" in block


def test_synergy_daily_list_function_gives_only_list_columns():
    """시너지 목록(api_daily_stats): 휴면 제외, 생일은 달만(생년월일·종족 없음), 지난달 순위용(light)은 지표 칸만."""
    fn = _block("create or replace function public.api_daily_stats", "$$;")
    assert "birth_month" in fn and "birth_date" not in fn and "'race'" not in fn
    assert fn.count("coalesce(affiliation, '') <> '휴면'") + fn.count("coalesce(s.affiliation, '') <> '휴면'") == 2
    light = fn[fn.index("then json_build_object("):fn.index("else json_build_object(")]
    keys = set(re.findall(r"'(\w+)', s\.", light))
    assert keys == {"role", "affiliation", "gender", "balloons", "broadcast_seconds", "cumulative_viewers",
                    "viewership_seconds", "sponsor_wins", "sponsor_losses"}


def test_daily_publish_keeps_viewership_so_republishing_does_not_drop_it():
    """하루치 게시는 행을 지우고 다시 넣으므로 뷰어십 칸도 함께 넣어야 스폰 재집계·월말 확정 때 지워지지 않는다."""
    fn = _block("create or replace function public.publish_daily_member_stats", "$$;")
    assert fn.count("viewership_seconds") == 3   # 넣는 칸, 값, 읽는 칸
    from repositories.synergy_stats import DAILY_SNAPSHOT_COLUMNS
    assert "viewership_seconds" in DAILY_SNAPSHOT_COLUMNS.split(",")
