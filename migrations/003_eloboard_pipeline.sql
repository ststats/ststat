-- ststat Part 3: EloBoard collection hardening.
-- Safe to run more than once. Existing StarUniv elo_* tables are reused.

create index if not exists elo_matches_date_idx on public.elo_matches (match_date desc);
create index if not exists elo_matches_winner_idx on public.elo_matches (winner_elo_id);
create index if not exists elo_matches_loser_idx on public.elo_matches (loser_elo_id);
create index if not exists elo_matches_map_idx on public.elo_matches (map_id);
create index if not exists elo_matches_category_idx on public.elo_matches (category_id);
create index if not exists elo_players_name_idx on public.elo_players (name);

-- Pipeline writes these tables with the service role. Browser write access is unnecessary.
alter table public.elo_categories enable row level security;
alter table public.elo_maps enable row level security;
alter table public.elo_players enable row level security;
alter table public.elo_matches enable row level security;

grant select, insert, update, delete on table public.elo_categories to service_role;
grant select, insert, update, delete on table public.elo_maps to service_role;
grant select, insert, update, delete on table public.elo_players to service_role;
grant select, insert, update, delete on table public.elo_matches to service_role;
