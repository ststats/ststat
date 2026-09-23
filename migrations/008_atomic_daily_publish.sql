-- Publish a complete Synergy day in one database transaction.
-- Apply before deploying the matching ststat job.

alter table public.daily_member_stats
  add column if not exists gender text,
  add column if not exists birth_date date;

update public.daily_member_stats d
set gender = coalesce(d.gender, tm.gender),
    birth_date = coalesce(d.birth_date, tm.birth_date)
from public.tier_members tm
where tm.soop_id = d.soop_id
  and (d.gender is null or d.birth_date is null);

create or replace function public.publish_daily_member_stats(
  p_stat_date date,
  p_rows jsonb
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
  v_distinct integer;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' or jsonb_array_length(p_rows) = 0 then
    raise exception 'daily snapshot rows must be a non-empty array';
  end if;

  -- Serialize publishers for the same date. Readers continue to see the old
  -- complete day until this transaction commits.
  perform pg_advisory_xact_lock(hashtext('daily_member_stats:' || p_stat_date::text));

  select count(*), count(distinct x.soop_id)
    into v_count, v_distinct
    from jsonb_to_recordset(p_rows) as x(stat_date date, soop_id text);

  if v_count <> v_distinct then
    raise exception 'daily snapshot contains duplicate soop_id values';
  end if;
  if exists (
    select 1 from jsonb_to_recordset(p_rows) as x(stat_date date)
    where x.stat_date is distinct from p_stat_date
  ) then
    raise exception 'daily snapshot contains a different stat_date';
  end if;

  delete from public.daily_member_stats where stat_date = p_stat_date;

  insert into public.daily_member_stats (
    stat_date, month_start, soop_id, elo_id, nickname, role, affiliation,
    race, tier, gender, birth_date, balloons, broadcast_seconds,
    cumulative_viewers, sponsor_wins, sponsor_losses, updated_at,
    sponsor_updated_at
  )
  select
    x.stat_date, x.month_start, x.soop_id, x.elo_id, x.nickname,
    coalesce(x.role, ''), x.affiliation, x.race, x.tier, x.gender,
    x.birth_date, coalesce(x.balloons, 0),
    coalesce(x.broadcast_seconds, 0), coalesce(x.cumulative_viewers, 0),
    coalesce(x.sponsor_wins, 0), coalesce(x.sponsor_losses, 0),
    coalesce(x.updated_at, now()), x.sponsor_updated_at
  from jsonb_to_recordset(p_rows) as x(
    stat_date date,
    month_start date,
    soop_id text,
    elo_id integer,
    nickname text,
    role text,
    affiliation text,
    race text,
    tier text,
    gender text,
    birth_date date,
    balloons bigint,
    broadcast_seconds bigint,
    cumulative_viewers bigint,
    sponsor_wins integer,
    sponsor_losses integer,
    updated_at timestamptz,
    sponsor_updated_at timestamptz
  );

  get diagnostics v_count = row_count;
  if v_count <> jsonb_array_length(p_rows) then
    raise exception 'daily snapshot row count mismatch: inserted %, expected %',
      v_count, jsonb_array_length(p_rows);
  end if;
  return v_count;
end;
$$;

revoke all on function public.publish_daily_member_stats(date, jsonb) from public, anon, authenticated;
grant execute on function public.publish_daily_member_stats(date, jsonb) to service_role;
