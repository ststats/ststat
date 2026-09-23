-- ststat Part 4: EloBoard derived statistics.
-- Safe-swap design: ststat writes a complete new snapshot, validates it, then activates it
-- with one transactional RPC. The previously active snapshot stays readable until activation.

create extension if not exists pgcrypto;

create table if not exists public.elo_derived_snapshots (
  snapshot_id uuid primary key default gen_random_uuid(),
  as_of date not null,
  status text not null default 'building' check (status in ('building','active','retired','failed')),
  source_match_count integer not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  activated_at timestamptz
);
create unique index if not exists elo_derived_one_active_uq
  on public.elo_derived_snapshots ((status)) where status = 'active';

create table if not exists public.elo_player_stats (
  snapshot_id uuid not null references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  total_games integer not null default 0,
  wins integer not null default 0,
  losses integer not null default 0,
  win_rate numeric(7,4),
  last_match_date date,
  primary key (snapshot_id, elo_id)
);
create index if not exists elo_player_stats_elo_idx on public.elo_player_stats(elo_id);

create table if not exists public.elo_h2h_stats (
  snapshot_id uuid not null references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  player_elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  opponent_elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  games integer not null default 0,
  wins integer not null default 0,
  losses integer not null default 0,
  win_rate numeric(7,4),
  last_match_date date,
  primary key (snapshot_id, player_elo_id, opponent_elo_id)
);
create index if not exists elo_h2h_stats_player_idx on public.elo_h2h_stats(player_elo_id);
create index if not exists elo_h2h_stats_opponent_idx on public.elo_h2h_stats(opponent_elo_id);

create table if not exists public.elo_race_stats (
  snapshot_id uuid not null references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  opponent_race text not null,
  games integer not null default 0,
  wins integer not null default 0,
  losses integer not null default 0,
  win_rate numeric(7,4),
  primary key (snapshot_id, elo_id, opponent_race)
);
create index if not exists elo_race_stats_elo_idx on public.elo_race_stats(elo_id);

create table if not exists public.elo_rankings (
  snapshot_id uuid not null references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  tier text,
  tier_rank integer,
  tier_count integer,
  raw_rating numeric(10,3),
  rating numeric(10,3),
  data_tier text,
  tier_gap integer,
  recent_365_games integer not null default 0,
  recent_365_wins integer not null default 0,
  recent_90_games integer not null default 0,
  recent_90_wins integer not null default 0,
  recent_30_games integer not null default 0,
  recent_30_wins integer not null default 0,
  as_of date not null,
  primary key (snapshot_id, elo_id)
);
create index if not exists elo_rankings_tier_rank_idx on public.elo_rankings(tier, tier_rank);
create index if not exists elo_rankings_elo_idx on public.elo_rankings(elo_id);

create table if not exists public.elo_ranking_meta (
  snapshot_id uuid primary key references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  as_of date not null,
  half_life_days integer not null,
  half_life_tier_days integer not null,
  recent_days integer not null,
  min_recent_games integer not null,
  tier_counts jsonb not null default '{}'::jsonb,
  tier_levels jsonb not null default '{}'::jsonb
);

create table if not exists public.elo_rating_history (
  snapshot_id uuid not null references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  month_end date not null,
  rating numeric(10,3),
  primary key (snapshot_id, elo_id, month_end)
);
create index if not exists elo_rating_history_elo_idx on public.elo_rating_history(elo_id, month_end);

-- Detailed directional match view; no duplicated match storage.
create or replace view public.elo_player_matches as
select m.elo_match_id, m.match_date, m.winner_elo_id as elo_id,
       m.loser_elo_id as opponent_elo_id, true as won, m.map_id, m.category_id
from public.elo_matches m
union all
select m.elo_match_id, m.match_date, m.loser_elo_id as elo_id,
       m.winner_elo_id as opponent_elo_id, false as won, m.map_id, m.category_id
from public.elo_matches m;

-- Atomic pointer swap after a complete snapshot was inserted.
create or replace function public.activate_elo_derived_snapshot(p_snapshot uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not exists (
    select 1 from public.elo_derived_snapshots
    where snapshot_id = p_snapshot and status = 'building'
  ) then
    raise exception 'snapshot % is not in building state', p_snapshot;
  end if;

  update public.elo_derived_snapshots
     set status = 'retired'
   where status = 'active';

  update public.elo_derived_snapshots
     set status = 'active', activated_at = now()
   where snapshot_id = p_snapshot;
end;
$$;
revoke all on function public.activate_elo_derived_snapshot(uuid) from public, anon, authenticated;
grant execute on function public.activate_elo_derived_snapshot(uuid) to service_role;

-- Public readers can only see rows belonging to the active snapshot.
alter table public.elo_derived_snapshots enable row level security;
alter table public.elo_player_stats enable row level security;
alter table public.elo_h2h_stats enable row level security;
alter table public.elo_race_stats enable row level security;
alter table public.elo_rankings enable row level security;
alter table public.elo_ranking_meta enable row level security;
alter table public.elo_rating_history enable row level security;

create or replace function public.is_active_elo_snapshot(p_snapshot uuid)
returns boolean language sql stable security definer set search_path=public
as $$ select exists(select 1 from public.elo_derived_snapshots s where s.snapshot_id=p_snapshot and s.status='active') $$;
revoke all on function public.is_active_elo_snapshot(uuid) from public;
grant execute on function public.is_active_elo_snapshot(uuid) to anon, authenticated, service_role;

do $$
declare t text;
begin
  drop policy if exists elo_derived_snapshots_public_read on public.elo_derived_snapshots;
  create policy elo_derived_snapshots_public_read on public.elo_derived_snapshots
    for select to anon, authenticated using (status='active');

  foreach t in array array['elo_player_stats','elo_h2h_stats','elo_race_stats','elo_rankings','elo_ranking_meta','elo_rating_history'] loop
    execute format('drop policy if exists %I on public.%I', t || '_public_read', t);
    execute format('create policy %I on public.%I for select to anon, authenticated using (public.is_active_elo_snapshot(snapshot_id))', t || '_public_read', t);
  end loop;
end $$;

grant select on public.elo_derived_snapshots, public.elo_player_stats, public.elo_h2h_stats,
  public.elo_race_stats, public.elo_rankings, public.elo_ranking_meta, public.elo_rating_history to anon, authenticated;
grant select on public.elo_player_matches to anon, authenticated;
