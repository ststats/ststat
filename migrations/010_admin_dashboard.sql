-- One authenticated admin request replaces a dozen browser-side exact counts and
-- exposes the timestamps operators actually need when checking pipeline health.
create or replace function public.admin_dashboard_stats()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  result jsonb;
  elo_total bigint;
  elo_min_id bigint;
  elo_max_id bigint;
  elo_first_date date;
  elo_last_date date;
  daily_date date;
  daily_updated timestamptz;
  job_name text;
  job_status text;
  job_finished timestamptz;
begin
  if not public.is_admin() then
    raise exception 'admin access required' using errcode = '42501';
  end if;

  select count(*), min(elo_match_id), max(elo_match_id), min(match_date), max(match_date)
    into elo_total, elo_min_id, elo_max_id, elo_first_date, elo_last_date
    from public.elo_matches;
  select max(stat_date), max(updated_at)
    into daily_date, daily_updated
    from public.daily_member_stats;
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
      'min_match_id', elo_min_id,
      'max_match_id', elo_max_id,
      'first_match_date', elo_first_date,
      'last_match_date', elo_last_date
    ),
    'freshness', jsonb_build_object(
      'daily_stat_date', daily_date,
      'daily_updated_at', daily_updated,
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
