create or replace function public.elo_derived_source_signature()
returns text
language sql
stable
set search_path = public
as $$
  select concat_ws(':',
    (select count(*) || '/' || coalesce(sum(hashtextextended(r::text, 0)::numeric), 0)
       from (select elo_match_id, match_date, winner_elo_id, loser_elo_id, map_id, category_id
               from public.elo_matches) r),
    (select count(*) || '/' || coalesce(sum(hashtextextended(r::text, 0)::numeric), 0)
       from (select elo_id, name, race from public.elo_players) r),
    (select count(*) || '/' || coalesce(sum(hashtextextended(r::text, 0)::numeric), 0)
       from (select category_id, name from public.elo_categories) r),
    (select count(*) || '/' || coalesce(sum(hashtextextended(r::text, 0)::numeric), 0)
       from (select map_id, name from public.elo_maps) r),
    (select count(*) || '/' || coalesce(sum(hashtextextended(r::text, 0)::numeric), 0)
       from (select elo_id, nickname, name, soop_id, tier, affiliation, race, tier_table_registered,
                    promoted_tier_0, promoted_tier_1, promoted_tier_2, promoted_tier_3, promoted_tier_4,
                    promoted_tier_5, promoted_tier_6, promoted_tier_7, promoted_tier_8
               from public.tier_members) r)
  );
$$;

create or replace view public.elo_public_players
as
select
  p.elo_id,
  p.name as elo_name,
  -- 레이팅은 EloBoard 계정의 종족으로 학습한다. 예측·전적도 같은 종족을 쓴다.
  coalesce(p.race, '') as race,
  tm.nickname,
  -- 휴면 선수도 검색 화면에 나오므로 프로필 사진용 SOOP ID를 준다
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

-- 승률 전용 모델은 랭킹과 별도 저장하되 같은 스냅샷에서 원자적으로 공개한다.
create table if not exists public.elo_prediction_models (
  snapshot_id uuid primary key references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  payload jsonb not null
);
alter table public.elo_prediction_models enable row level security;
revoke all on public.elo_prediction_models from public, anon, authenticated;
grant select, insert, update, delete on public.elo_prediction_models to service_role;

create or replace function public.elo_prediction_model() returns json
language sql stable security definer set search_path = public as $$
  select coalesce((select payload::json from public.elo_prediction_models
    where snapshot_id = (select public.active_elo_snapshot_id())), '{}'::json);
$$;
revoke all on function public.elo_prediction_model() from public;
grant execute on function public.elo_prediction_model() to anon, authenticated;


