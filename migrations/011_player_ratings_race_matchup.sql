-- ststat ranking v4: 모든 선수의 레이팅(θ)·표준오차와 종족 상성을 공개한다.
--
-- elo_rankings는 순위에 오른 선수(티어 있음 · 최근 1년 10판 이상 · 휴면 아님)만 담는다.
-- 엔트리 예상승률은 순위 밖 선수에게도 티어 평균이 아닌 실제 추정치를 써야 하므로
-- 맞춘 모든 선수의 θ를 따로 둔다. 값은 Elo 점수 단위(1500 기준, 400/ln10 배율).
--
-- 적용 순서: 이 마이그레이션을 먼저 적용한 뒤 ranking v4 파이프라인 코드를 배포한다
-- (코드가 elo_player_ratings에 쓰고 elo_ranking_meta.race_matchup을 넣는다).

create table if not exists public.elo_player_ratings (
  snapshot_id uuid not null references public.elo_derived_snapshots(snapshot_id) on delete cascade,
  elo_id integer not null references public.elo_players(elo_id) on delete cascade,
  rating numeric(10,3) not null,
  rating_se numeric(10,3),
  as_of date not null,
  primary key (snapshot_id, elo_id)
);
create index if not exists elo_player_ratings_elo_idx on public.elo_player_ratings(elo_id);

-- 종족 상성(Elo 점수): {"TZ": 테란의 저그 상대 우위, "ZP": 저그→프로토스, "PT": 프로토스→테란}
alter table public.elo_ranking_meta
  add column if not exists race_matchup jsonb not null default '{}'::jsonb;

alter table public.elo_player_ratings enable row level security;
drop policy if exists elo_player_ratings_public_read on public.elo_player_ratings;
create policy elo_player_ratings_public_read on public.elo_player_ratings
  for select to anon, authenticated using (public.is_active_elo_snapshot(snapshot_id));
grant select on public.elo_player_ratings to anon, authenticated;
