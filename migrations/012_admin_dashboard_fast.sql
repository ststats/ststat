-- admin_dashboard_stats가 elo_matches 전체를 count(*)·min·max 한 번에 훑어서, 경기가 쌓이자
-- 로그인 사용자 statement_timeout(기본 8초)에 걸려 어드민 운영 현황이 "조회 실패"가 됐다.
--  - 경기 수: 통계 추정치(pg_class.reltuples, ANALYZE 때 갱신). 화면에 "약"으로 표시.
--  - ID·날짜 범위: 인덱스 끝값만 읽는 order by … limit 1.
--  - 방송통계 갱신 시각: 최신 날짜 행만 본다.
-- 반환 모양은 010과 같고 elo.total_estimated만 더했다. 여러 번 실행해도 된다.
create or replace function public.admin_dashboard_stats()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  result jsonb;
  elo_total bigint;
  daily_date date;
  job_name text;
  job_status text;
  job_finished timestamptz;
begin
  if not public.is_admin() then
    raise exception 'admin access required' using errcode = '42501';
  end if;

  select greatest(c.reltuples, 0)::bigint into elo_total
    from pg_class c where c.oid = 'public.elo_matches'::regclass;
  select stat_date into daily_date
    from public.daily_member_stats order by stat_date desc limit 1;
  select s.job_name, s.status, s.finished_at
    into job_name, job_status, job_finished
    from public.sync_jobs s order by s.started_at desc limit 1;

  select jsonb_build_object(
    'counts', jsonb_build_object(
      'members', (select count(*) from public.members),
      'teams', (select count(*) from public.teams),
      'matches', (select count(*) from public.matches),
      'tier_members', (select count(*) from public.tier_members),
      'calendar_events', (select count(*) from public.calendar_events),
      'calendar_off_air', (select count(*) from public.calendar_off_air),
      'video_channels', (select count(*) from public.video_channels),
      'videos', (select count(*) from public.videos),
      'video_picks', (select count(*) from public.video_picks),
      'external_tools', (select count(*) from public.external_tools),
      'elo_players', (select count(*) from public.elo_players),
      'elo_matches', elo_total
    ),
    'elo', jsonb_build_object(
      'total', elo_total,
      'total_estimated', true,
      'min_match_id', (select elo_match_id from public.elo_matches order by elo_match_id asc limit 1),
      'max_match_id', (select elo_match_id from public.elo_matches order by elo_match_id desc limit 1),
      'first_match_date', (select match_date from public.elo_matches order by match_date asc limit 1),
      'last_match_date', (select match_date from public.elo_matches order by match_date desc limit 1)
    ),
    'freshness', jsonb_build_object(
      'daily_stat_date', daily_date,
      'daily_updated_at', (select max(updated_at) from public.daily_member_stats where stat_date = daily_date),
      'elo_as_of', (select as_of from public.elo_derived_snapshots where status='active' limit 1),
      'elo_activated_at', (select activated_at from public.elo_derived_snapshots where status='active' limit 1),
      'last_job_name', job_name,
      'last_job_status', job_status,
      'last_job_finished_at', job_finished
    )
  ) into result;
  return result;
end;
$$;

revoke all on function public.admin_dashboard_stats() from public;
grant execute on function public.admin_dashboard_stats() to authenticated;
