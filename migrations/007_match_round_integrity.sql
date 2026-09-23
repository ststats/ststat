-- ststat Part 7
-- Match/round linking is already authoritative in Supabase:
--   rounds.match_no -> matches.match_no
-- Therefore the old heuristic match_link.py must NOT be ported as a writer.
--
-- This migration adds a canonical read-only view that reproduces the only
-- still-useful derived behavior from match_link.py: mirrored internal rounds.

create or replace view public.rounds_effective
with (security_invoker = true)
as
select
  r.id as source_round_id,
  false as is_mirrored,
  r.source_order,
  r.match_no,
  r.match_date,
  r.opponent_team,
  r.match_format,
  r.set_name,
  r.round_name,
  r.our_player,
  r.our_race,
  r.our_tier,
  r.result,
  r.opponent_player,
  r.opponent_race,
  r.opponent_tier,
  r.map_name
from public.rounds r

union all

select
  r.id as source_round_id,
  true as is_mirrored,
  r.source_order,
  r.match_no,
  r.match_date,
  r.opponent_team,
  r.match_format,
  r.set_name,
  r.round_name,
  r.opponent_player as our_player,
  r.opponent_race as our_race,
  r.opponent_tier as our_tier,
  case
    when r.result = '승' then '패'
    when r.result = '패' then '승'
    else r.result
  end as result,
  r.our_player as opponent_player,
  coalesce(
    nullif(r.our_race, ''),
    case
      when coalesce(m.race, '') like '%테란%' then 'T'
      when coalesce(m.race, '') like '%저그%' then 'Z'
      when coalesce(m.race, '') like '%프로토스%' then 'P'
      else ''
    end
  ) as opponent_race,
  r.our_tier as opponent_tier,
  r.map_name
from public.rounds r
left join public.members m
  on m.nickname = r.our_player
where trim(coalesce(r.opponent_team, '')) = '내전'
  and trim(coalesce(r.our_player, '')) <> ''
  and trim(coalesce(r.opponent_player, '')) <> '';

comment on view public.rounds_effective is
  'Read-only canonical rounds view. Includes one mirrored row for each internal round; base public.rounds is never duplicated or rewritten.';

-- Intentionally no anonymous grant yet.
-- StarUniv still uses its current frontend fallback during validation.
