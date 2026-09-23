# ststat Part 4 — EloBoard 파생 통계 중앙화

이번 파트는 StarUniv의 EloBoard 상대전적/랭킹 계산을 ststat로 옮깁니다.

## 무엇이 추가되나

- `calculate_eloboard_stats` job
- 누적 승패: `elo_player_stats`
- 상대전적: `elo_h2h_stats`
- 종족전: `elo_race_stats`
- 티어 랭킹: `elo_rankings`
- 랭킹 메타: `elo_ranking_meta`
- 월별 레이팅: `elo_rating_history`
- 상세 경기용 방향성 view: `elo_player_matches`
- 안전한 스냅샷 전환: `elo_derived_snapshots`

현재 StarUniv의 `build_h2h.py`와 `build_ranking.py` 계산 방식을 그대로 ststat 안에서 실행해 의미가 바뀌지 않게 했습니다. 계산용 JSON은 임시 디렉터리에서만 생성되고 저장소에는 남지 않습니다.

## 안전장치

새 통계를 기존 활성 데이터 위에 바로 덮어쓰지 않습니다.

1. 새 snapshot을 `building`으로 생성
2. 모든 파생 통계 계산/업로드
3. 검증 성공
4. `activate_elo_derived_snapshot()` RPC로 한 번에 활성 snapshot 변경

중간 실패 시 기존 `active` snapshot은 그대로 유지됩니다.

## 적용 방법

### 1. ZIP 덮어쓰기

이 ZIP의 파일을 현재 `ststat` 로컬 폴더에 덮어씁니다.

### 2. Supabase SQL 실행

Supabase → SQL Editor에서 다음 파일을 한 번 실행합니다.

```text
migrations/004_eloboard_derived_stats.sql
```

### 3. 커밋/푸시

```cmd
git add .
git commit -m "Centralize EloBoard derived stats"
git push origin main
```

### 4. GitHub Actions 실행

`Run ststat pipeline`을 수동 실행합니다. 순서는 다음입니다.

```text
healthcheck
sync_roster
sync_eloboard
calculate_eloboard_stats
```

외부 cron을 연결해 둔 경우에도 같은 workflow를 4시간마다 호출하면 됩니다.

### 5. 성공 확인

Supabase의 `sync_jobs`에서 아래 행을 확인합니다.

```text
job_name = calculate_eloboard_stats
status = success
```

그리고 아래 테이블에 데이터가 생성되면 성공입니다.

```text
elo_derived_snapshots
elo_player_stats
elo_h2h_stats
elo_race_stats
elo_rankings
elo_ranking_meta
elo_rating_history
```

`elo_derived_snapshots`에서 `status = active`인 행은 하나여야 합니다.

## 아직 하지 않는 것

- StarUniv의 기존 `docs/data/h2h/**` 삭제 안 함
- 기존 `build_h2h.py`, `build_ranking.py` 삭제 안 함
- StarUniv 프론트가 새 Supabase 파생 테이블만 보게 전환하지 않음
- Synergy 통계는 아직 안 건드림
- `generate_stats.py`의 캄몬스타즈 내부 경기/라운드 통계는 이번 범위 아님

즉 Part 4에서는 새 결과를 Supabase에 병행 생성합니다. 결과가 정상인지 확인한 뒤 다음 파트에서 StarUniv 읽기 경로를 전환합니다.
