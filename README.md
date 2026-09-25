# ststat

StarUniv와 Synergy가 사용하는 중앙 배치 파이프라인입니다. 외부 데이터를 수집하고 계산한 뒤 공유 Supabase에 게시합니다.

## 담당 영역

- EloBoard 로스터 후보와 경기 수집
- H2H, 종족전, 랭킹, 레이팅 스냅샷 계산
- Poonggo 월 누적치와 Synergy 일별 통계 게시
- YouTube 영상 메타데이터 수집
- StarUniv 경기·라운드 무결성 감사

수동 편집 필드의 소유자는 StarUniv 관리자입니다. 세부 쓰기 경계는 `config/ownership.yml`에 있습니다.

## 실행

필수 환경변수:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- 영상 작업용 `YOUTUBE_API_KEY`

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/run_job.py healthcheck
python scripts/run_job.py sync_roster
python scripts/run_job.py sync_eloboard
python scripts/run_job.py calculate_eloboard_stats
python scripts/run_job.py sync_synergy_daily
python scripts/run_job.py sync_videos
python scripts/run_job.py audit_match_rounds
```

운영에서는 `.github/workflows/run-pipeline.yml`을 외부 스케줄러가 호출합니다. 작업들은 실제 의존성에 따라 분리되어 Poonggo나 YouTube 한 곳의 장애가 다른 독립 작업을 막지 않습니다.

## DB 적용

`migrations/001_*.sql`부터 번호 순서대로 같은 Supabase 프로젝트에 적용합니다. 웹 공개 뷰와 권한도 이 저장소의 migration에서만 관리합니다.

현재 일별 통계 게시 코드는 `008_atomic_daily_publish.sql`의 RPC를 요구하며, StarUniv/Synergy 공개 조회는 `009_public_web_views.sql`, 어드민 통합 현황은 `010_admin_dashboard.sql`을 요구합니다. 티어랭킹 v4(모든 선수 레이팅·종족 상성 공개)는 `011_player_ratings_race_matchup.sql`을 요구하므로, 이 마이그레이션을 먼저 적용한 뒤 코드를 배포합니다.

## 운영 메모

- **EloBoard 요청 간격은 최소 2초**(운영자 요청). `ELOBOARD_DELAY`는 늘릴 수만 있고, 2초 미만이나 잘못된 값은
  `collectors/eloboard.py`의 `MIN_DELAY`(2초)로 올라갑니다.
- 정기 수집은 이번 달 1일(매월 1~7일은 지난달 1일)부터 다시 읽습니다. 사라진 경기 삭제는 그 기간 안에서만,
  기간 경기 수의 2%(최소 50건)까지만 하고 넘으면 지우지 않습니다. 또 **두 번 연속 안 보인 경기만** 지웁니다
  (처음 안 보이면 `sync_jobs.metadata.pending_delete`에 적어 두고 다음 실행에서도 없을 때 삭제 - 수집 도중
  EloBoard 목록이 밀려 한 번 안 보인 경기를 지우지 않게).
- 러너가 취소·강제 종료돼 `running`으로 남은 `sync_jobs` 행은 같은 작업이 다음에 시작할 때 3시간이 지났으면
  실패로 닫힙니다. 같은 이유로 남은 `building` 파생 스냅샷도 3시간이 지나면 다음 파생 계산 때 지워집니다.
- 파생 계산(`calculate_eloboard_stats`)은 경기를 ID 구간으로 나눠 동시에 읽고(정확한 행 수와 맞춰 봄),
  결과도 동시에 씁니다. 단계별 걸린 시간은 `sync_jobs.metadata.timings`에 남습니다.
- `backfill-eloboard.yml`: 누락 복구용으로 EloBoard 전체를 다시 읽어 upsert합니다(삭제 없음, 1시간 반 안팎).
- 2026-09-24 경기 삭제 사고는 staruniv에 남아 있던 2026-09-22 백업 JSON으로 복구했다(복구 작업은 끝나서 코드 삭제, git 기록 `b38249f`에 있음).
- 티어 랭킹 모델 설명과 계산 코드: `processors/staruniv_ranking.py`(파일 머리 설명).
