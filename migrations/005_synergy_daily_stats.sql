-- Part 5: Synergy monthly live stats + daily snapshots.
-- Safe to run more than once.

create table if not exists public.poonggo_monthly_stats (
  month_start date not null,
  soop_id text not null,
  balloons bigint not null default 0,
  broadcast_seconds bigint not null default 0,
  cumulative_viewers bigint not null default 0,
  fetched_at timestamptz not null default now(),
  primary key (month_start, soop_id)
);
create index if not exists poonggo_monthly_stats_soop_idx
  on public.poonggo_monthly_stats (soop_id, month_start desc);

create table if not exists public.daily_member_stats (
  stat_date date not null,
  month_start date not null,
  soop_id text not null,
  elo_id integer,
  nickname text not null,
  role text not null default '',
  affiliation text,
  race text,
  tier text,
  gender text,
  birth_date date,
  balloons bigint not null default 0,
  broadcast_seconds bigint not null default 0,
  cumulative_viewers bigint not null default 0,
  sponsor_wins integer not null default 0,
  sponsor_losses integer not null default 0,
  updated_at timestamptz not null default now(),
  sponsor_updated_at timestamptz,
  primary key (stat_date, soop_id)
);
create index if not exists daily_member_stats_soop_date_idx
  on public.daily_member_stats (soop_id, stat_date desc);
create index if not exists daily_member_stats_date_idx
  on public.daily_member_stats (stat_date desc);
create index if not exists daily_member_stats_affiliation_idx
  on public.daily_member_stats (affiliation, stat_date desc);
create index if not exists daily_member_stats_elo_idx
  on public.daily_member_stats (elo_id, stat_date desc);

create table if not exists public.synergy_month_confirmations (
  month_start date primary key,
  poonggo_complete boolean not null default false,
  sponsor_complete boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.poonggo_monthly_stats enable row level security;
alter table public.daily_member_stats enable row level security;
alter table public.synergy_month_confirmations enable row level security;

-- No anon policy yet. During Part 5 these are pipeline validation tables.
-- Service role used by ststat bypasses RLS. Synergy public read is enabled in the web cutover phase.
