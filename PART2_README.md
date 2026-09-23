# ststat Part 2 — 로스터 중앙화

이번 Part 2는 기존 StarUniv/Synergy를 아직 삭제/변경하지 않고, `ststat`가 로스터 동기화 책임을 먼저 가져오는 단계입니다.

## 이번에 하는 일

- 같은 Supabase의 `tier_members` 읽기
- EloBoard `https://eloboard.co.kr/api/tiers` 조회
- 기존 선수는 **EloBoard 이름(`tier_members.name`)만** 자동 갱신
- 새 선수는 `tier_members`에 자동 추가하지 않고 `tier_member_candidates`에 대기
- 한 번에 신규 후보가 100명을 넘으면 API/매칭 사고로 보고 아무것도 쓰지 않고 실패
- 모든 실행은 기존 Part 1의 `sync_jobs`에 기록

## 이번에 절대 안 건드리는 것

`tier_members`의 아래 값은 관리자/수동 관리 영역이라 Part 2가 덮어쓰지 않습니다.

- nickname
- soop_id
- elo_id
- birth_date
- gender
- race
- tier
- affiliation
- role
- modified_at
- history / started_on / 승급일 관련 필드 등

특히 `modified_at`은 Synergy 과거 통계 보정과 연결되어 있으므로 이번 파트에서는 읽기만 하고 절대 비우지 않습니다. 과거 보정 로직을 `ststat`로 옮기는 파트에서 함께 처리합니다.

## 적용 방법

### 1. ZIP 내용을 현재 `ststat` 폴더에 덮어쓰기

이 패치는 Part 1 위에 추가하는 파일입니다.

### 2. Supabase SQL Editor에서 실행

`migrations/002_roster_pipeline.sql` 내용을 전체 복사해서 한 번 실행합니다.

기존에 `tier_member_candidates`가 있어도 안전하게 부족한 컬럼만 추가합니다.

### 3. 커밋/푸시

```cmd
git add .
git commit -m "Add centralized roster sync"
git push origin main
```

### 4. GitHub Actions 수동 실행

GitHub → ststat → Actions → `Run ststat pipeline` → Run workflow

실행 순서:

1. healthcheck
2. sync_roster

### 5. 성공 확인

Supabase `sync_jobs`에서 최신 `sync_roster` 행이 `success`인지 확인합니다.

`metadata` 예시:

```json
{
  "roster_count": 1231,
  "elo_names_updated": 3,
  "new_candidates": 1,
  "manual_fields_touched": 0,
  "modified_at_cleared": 0
}
```

새 선수가 발견되면 `tier_member_candidates`에만 생깁니다.

## 4시간 외부 cron

Part 1과 동일하게 외부 cron은 GitHub의 `workflow_dispatch`를 4시간마다 호출하면 됩니다. GitHub Actions 자체에 schedule cron은 추가하지 않았습니다.

## Cloudflare Worker

2분마다 실시간 방송 상태를 갱신하는 기존 Cloudflare Worker는 그대로 둡니다.

데이터 도메인 소유권은 `ststat`이지만 실행기는 계속 Worker입니다. `sync_roster`는 실시간 방송 필드를 전혀 업데이트하지 않습니다.

## 아직 StarUniv/Synergy에서 지우지 말 것

Part 2 검증 기간에는 다음 기존 로스터 관련 스크립트를 아직 삭제하지 마세요.

- Synergy `scripts/sync_members.py`
- Synergy `scripts/supabase_roster.py`

단, **같은 시간에 두 자동화가 동시에 로스터를 쓰게 두지는 않는 것을 권장합니다.** `ststat sync_roster`가 정상 작동하는 것을 확인한 후 기존 Synergy workflow의 로스터 sync step을 끄는 것이 다음 단계입니다.
