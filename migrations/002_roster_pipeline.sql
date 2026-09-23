-- ststat Part 2: roster candidate staging.
-- Run once in the SAME Supabase project used by StarUniv/Synergy.
-- Safe to re-run.

create table if not exists public.tier_member_candidates (
  id text primary key,
  nickname text not null default '',
  elo_id integer,
  gender text,
  race text,
  tier text,
  affiliation text,
  source text,
  found_at date,
  status text not null default 'pending',
  last_seen_at date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.tier_member_candidates
  add column if not exists status text not null default 'pending';
alter table public.tier_member_candidates
  add column if not exists last_seen_at date;
alter table public.tier_member_candidates
  add column if not exists updated_at timestamptz not null default now();

create index if not exists tier_member_candidates_elo_id_idx
  on public.tier_member_candidates (elo_id);
create index if not exists tier_member_candidates_found_at_idx
  on public.tier_member_candidates (found_at desc);
create index if not exists tier_member_candidates_status_idx
  on public.tier_member_candidates (status);

alter table public.tier_member_candidates enable row level security;

-- Internal pipeline table. Browser clients do not need access.
revoke all on table public.tier_member_candidates from anon, authenticated;
grant select, insert, update, delete on table public.tier_member_candidates to service_role;
