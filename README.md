# ststat

StarUniv와 Synergy가 사용하는 중앙 배치 파이프라인입니다. 외부 데이터를 수집하고 계산한 뒤 공유 Supabase에 게시합니다.

## 담당 영역

- EloBoard 로스터 후보와 경기 수집
- H2H, 종족전, 랭킹, 레이팅 스냅샷 계산
- 월 누적치(트래키파이, 예비로 Poonggo)와 Synergy 일별 통계 게시
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
python -m pip install -r requirements-dev.txt   # 테스트용(pytest). 운영 파이프라인은 설치하지 않음
python -m pytest -q
python scripts/run_job.py healthcheck
python scripts/run_job.py sync_roster
python scripts/run_job.py sync_eloboard
python scripts/run_job.py calculate_eloboard_stats
python scripts/run_job.py sync_synergy_daily
python scripts/run_job.py sync_videos
python scripts/run_job.py audit_match_rounds
```

운영에서는 cron-job.org가 매일 03:50·07:50·11:50·15:50·19:50·23:50(한국 시간, 4시간마다)에
`https://api.github.com/repos/ststats/ststat/actions/workflows/pipeline.yml/dispatches`로 `.github/workflows/pipeline.yml`을 실행합니다.
파이프라인은 먼저 테스트(`test.yml`)를 돌리고, 실패하면 공유 DB에 쓰지 않고 멈춥니다. 작업들은 실제 의존성에 따라 분리되어 Poonggo나 YouTube 한 곳의 장애가 다른 독립 작업을 막지 않습니다.

`sync_videos`는 보자에 등록된 숲 VOD의 빈 썸네일도 영상 페이지의 `og:image`로 채웁니다.
저장된 썸네일과 관리자 입력은 덮어쓰지 않으며, 가져오기 실패는 작업 결과에 남기고 다음 회차에 다시 시도합니다.

방송 중 표시는 이 파이프라인과 따로 돕니다: Supabase pg_cron이 2분마다 Edge Function `live-status`(`supabase/functions/live-status`)를 불러
SOOP 전체 방송 목록을 훑고 `live_broadcasts`를 통째로 바꿉니다(`supabase/ststat.sql` 12번). 함수 코드를 고치면 Supabase 대시보드에 다시 배포합니다.
목록은 SOOP 공식 Open API(`openapi.sooplive.com/broad/list`)로 받고, 안 되면 예전 비공식 목록으로 한 번 더 시도합니다.
공식 API에는 함수 비밀값 `SOOP_CLIENT_ID`(SOOP Developers에서 Public 범위로 만든 앱의 client_id)가 필요하며, 없으면 비공식 목록만 씁니다.
`/functions/v1/live-status?dry=1`의 `info.source`로 어느 목록을 썼는지 볼 수 있습니다.

시너지 월 누적(별풍선·방송시간·누적시청자·뷰어십)은 트래키파이 월간 요약(`/api/v1/p/soop/ranking/summary`, 한 번에 아이디 100개)
한 곳에서 받습니다(2026-10 운영자 이용 허락). 트래키파이에 문제가 생기면 GitHub 변수 `MONTHLY_SOURCE=poonggo`로
Poonggo(예비)에서 받고, 그동안 뷰어십은 비워 둡니다. 바꾸기 전 아카이브(2026-09-01~10-08)는 2026-10에 한 번
트래키파이 기준으로 다시 채웠습니다(별풍선·누적시청자는 하루 요약 합, 방송시간·뷰어십은 방송 기록의 시작·종료 시각을
날마다 나눈 값이고 10/1~10/8은 10/9 값을 넘지 않게 맞춤. 스폰 승패는 그대로).
트래키파이 월간 요약에서 방송이 통째로 빠진 경우(드물다)는 Supabase `missing_broadcasts` 표에 한 줄(아이디, 방송 시작·종료
시각(KST), 평균 시청자)을 넣으면 파이프라인이 그 달 방송시간·뷰어십에 더하고, 이미 게시된 지난 날에도 한 번 더합니다
(`applied_through`까지 반영됨). 트래키파이가 고치면 그 줄을 지웁니다.

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
  지우기 전에 원본 행을 `sync_jobs`(job_name `sync_eloboard_deleted_backup`)에 먼저 남깁니다 -
  그 행의 `metadata.rows`를 `elo_matches`에 upsert하면 되살아납니다.
- EloBoard 목록은 **경기 날짜순**이라 ID 순서와 다릅니다(지난 날짜로 늦게 등록된 경기는 뒤쪽 페이지에 있고
  ID가 더 큼). ID 크기로 수집 범위를 자르지 않습니다. 날짜 형식·범위(미래는 하루까지)·승자≠패자를 검사해
  거부한 행은 이유와 함께 `metadata.invalid_samples`에 남습니다.
- 방송통계의 스폰 승패는 매 실행 EloBoard 재수집 범위(또는 더 이른 소급 수정일)부터 어제까지 지금 경기
  기록으로 다시 세어, 바뀐 날만 다시 게시합니다(월말 확정된 달·ELO ID 소급 수정 포함). 방송통계를 처음
  게시한 뒤로 월말 스냅샷이 빠진 달은 `metadata.missing_month_end_snapshots`에 남습니다.
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
  먼저 입력 지문(`elo_derived_source_signature()`: 경기·선수·형식·티어표 + 계산 코드)을 활성 스냅샷의 것과 비교해,
  같으면 읽지 않고 건너뜁니다(`metadata.skipped`). 새 경기나 티어표 수정이 없는 회차의 Egress·로그를 아낍니다.
- 티어 랭킹 모델 설명과 계산 코드: `processors/staruniv_ranking.py`(파일 머리 설명).
