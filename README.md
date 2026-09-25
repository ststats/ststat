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

`supabase/ststat.sql` 한 파일을 Supabase SQL 편집기에 붙여 넣고 실행합니다(여러 번 실행해도 됩니다).
StarUniv 기본 표를 만드는 staruniv 저장소 `supabase/staruniv.sql`을 먼저 실행해 둬야 합니다.
파이프라인 표, 파생 통계, 공개 조회 뷰와 권한은 모두 이 파일에서만 관리합니다. 스키마를 바꾸는 코드는 이 파일을 먼저 실행한 뒤 배포합니다.

## 운영 메모

- **EloBoard 요청 간격은 최소 2초**(운영자 요청). `ELOBOARD_DELAY`는 늘릴 수만 있고, 2초 미만이나 잘못된 값은
  `collectors/eloboard.py`의 `MIN_DELAY`(2초)로 올라갑니다.
- 정기 수집은 이번 달 1일(매월 1~7일은 지난달 1일)부터 다시 읽습니다. 사라진 경기 삭제는 그 기간 안에서만,
  기간 경기 수의 2%(최소 50건)까지만 하고 넘으면 지우지 않습니다. 또 **두 번 연속 안 보인 경기만** 지웁니다
  (처음 안 보이면 `sync_jobs.metadata.pending_delete`에 적어 두고 다음 실행에서도 없을 때 삭제 - 수집 도중
  EloBoard 목록이 밀려 한 번 안 보인 경기를 지우지 않게).
  지운 경기의 원본 행은 `sync_jobs.metadata.deleted_rows`에 남아 되살릴 수 있습니다.
- EloBoard 목록은 **경기 날짜순**이라 ID 순서와 다릅니다(지난 날짜로 늦게 등록된 경기는 뒤쪽 페이지에 있고
  ID가 더 큼). ID 크기로 수집 범위를 자르지 않습니다. 날짜 형식·범위(미래는 하루까지)·승자≠패자를 검사해
  거부한 행은 이유와 함께 `metadata.invalid_samples`에 남습니다.
- 방송통계의 스폰 승패는 매 실행 EloBoard 재수집 범위(또는 더 이른 소급 수정일)부터 어제까지 지금 경기
  기록으로 다시 세어, 바뀐 날만 다시 게시합니다(월말 확정된 달·ELO ID 소급 수정 포함). 더 오래된 기간은
  `python scripts/run_job.py refresh_synergy_sponsor`(`SYNERGY_REFRESH_FROM`/`_TO`)로 다시 셉니다 -
  `backfill-eloboard.yml`은 전체 복구 뒤 이것을 자동으로 돌립니다. 월말 스냅샷이 빠진 달은
  `metadata.missing_month_end_snapshots`에 남습니다.
- Poonggo 응답에서 숫자가 아닌 값·음수·모양이 틀린 행은 오류로 멈추고(생략된 계정만 0), 여러 계정의 그달
  누적 별풍선이 절반 넘게 줄면 그 실행은 게시하지 않고 다음 실행에서 다시 받습니다.
- YouTube 쇼츠 판별·조회수를 알 수 없을 때는 0·False로 저장하지 않습니다(새 영상은 다음 실행에 재판별,
  기존 영상은 저장된 값 유지).
- 랭킹 최적화 결과가 유한하지 않거나, 수렴하지 않았는데 남은 기울기가 0.05를 넘으면 파생 작업이 실패로
  끝나 지금 게시된 스냅샷이 그대로 남습니다.
- `healthcheck`는 필요한 표·뷰와 최신 SQL의 함수(`active_elo_snapshot_id`)를 확인합니다. 명단 동기화가
  실패해도 기존 명단으로 뒤 작업을 계속합니다.
- 러너가 취소·강제 종료돼 `running`으로 남은 `sync_jobs` 행은 같은 작업이 다음에 시작할 때 3시간이 지났으면
  실패로 닫힙니다. 같은 이유로 남은 `building` 파생 스냅샷도 3시간이 지나면 다음 파생 계산 때 지워집니다.
- 파생 계산(`calculate_eloboard_stats`)은 경기를 ID 구간으로 나눠 동시에 읽고(정확한 행 수와 맞춰 봄),
  결과도 동시에 씁니다. 단계별 걸린 시간은 `sync_jobs.metadata.timings`에 남습니다.
- `backfill-eloboard.yml`: 누락 복구용으로 EloBoard 전체를 다시 읽어 upsert하고(삭제 없음, 1시간 반 안팎),
  이어서 지난 방송통계의 스폰 승패를 다시 셉니다.
- 2026-09-24 경기 삭제 사고는 staruniv에 남아 있던 2026-09-22 백업 JSON으로 복구했다(복구 작업은 끝나서 코드 삭제, git 기록 `b38249f`에 있음).
- 티어 랭킹 모델 설명과 계산 코드: `processors/staruniv_ranking.py`(파일 머리 설명).
