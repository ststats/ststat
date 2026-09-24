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
