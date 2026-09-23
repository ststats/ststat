-- Public read contract used by StarUniv and Synergy browsers.
-- Schema changes live in ststat so the two web repositories only consume them.

create or replace view public.elo_public_players
as
select
  p.elo_id,
  p.name as elo_name,
  coalesce(nullif(tm.race,''), p.race, '') as race,
  tm.nickname,
  tm.soop_id,
  tm.tier,
  tm.affiliation,
  ps.total_games,
  ps.wins,
  ps.last_match_date,
  rk.tier_rank,
  rk.tier_count,
  s.as_of
from public.elo_players p
join public.elo_derived_snapshots s on s.status='active'
left join public.tier_members tm on tm.elo_id=p.elo_id
left join public.elo_player_stats ps on ps.snapshot_id=s.snapshot_id and ps.elo_id=p.elo_id
left join public.elo_rankings rk on rk.snapshot_id=s.snapshot_id and rk.elo_id=p.elo_id;

create or replace view public.elo_public_matches
as
select
  pm.elo_match_id,
  pm.match_date,
  pm.elo_id,
  pm.opponent_elo_id,
  pm.won,
  pm.map_id,
  mp.name as map_name,
  c.name as category_name
from public.elo_player_matches pm
left join public.elo_maps mp on mp.map_id=pm.map_id
left join public.elo_categories c on c.category_id=pm.category_id;

grant select on public.elo_public_players, public.elo_public_matches to anon, authenticated;

alter table public.daily_member_stats enable row level security;
drop policy if exists synergy_daily_public_read on public.daily_member_stats;
create policy synergy_daily_public_read on public.daily_member_stats
for select to anon, authenticated using (true);
grant select on public.daily_member_stats to anon, authenticated;

create or replace view public.synergy_daily_dates
with (security_invoker = true)
as
select stat_date, max(updated_at) as updated_at
from public.daily_member_stats
group by stat_date
order by stat_date desc;

grant select on public.synergy_daily_dates to anon, authenticated;
