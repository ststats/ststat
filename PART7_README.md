# ststat Part 7 — StarUniv 경기/라운드 무결성 중앙화

## 중요한 변경

최신 StarUniv의 Supabase 구조에서는 이미:

```text
rounds.match_no -> matches.match_no
```

외래키(FK)로 연결이 강제됩니다.

따라서 예전 `scripts/match_link.py`가 하던 **날짜/상대/라운드 순서를 보고 match를 추론하는 로직을
ststat에서 다시 돌려 DB를 고치는 것은 하지 않습니다.**

그렇게 하면 현재 관리자에서 정확하게 저장한 `match_no`를 오히려 잘못 바꿀 위험이 있습니다.

Part 7은 아래 두 가지로 중앙화합니다.

1. `audit_match_rounds`
   - match/round 연결 상태 검사
   - 날짜/상대팀/형식 불일치 보고
   - 잘못된 내전 라운드 보고
   - 결과를 `sync_jobs.metadata`에 기록
   - **matches/rounds는 절대 수정하지 않음**

2. `rounds_effective` read-only view
   - 원본 round
   - 내전일 경우 반대 선수 관점의 mirror round
   를 하나의 조회 결과로 제공합니다.
   - 원본 `rounds`에 미러 row를 실제 INSERT하지 않습니다.

## 적용

### 1. ZIP 덮어쓰기

현재 `ststat` 레포 루트에 이 ZIP의 내용을 덮어씁니다.

### 2. SQL 실행

Supabase → SQL Editor에서:

```text
migrations/007_match_round_integrity.sql
```

을 1회 실행합니다.

### 3. 커밋/푸시

```cmd
git add .
git commit -m "Centralize match round integrity"
git push origin main
```

### 4. Pipeline 실행

GitHub Actions의 `Run ststat pipeline`을 실행합니다.

마지막 부분에:

```text
Audit StarUniv match-round integrity
```

가 실행됩니다.

Supabase `sync_jobs`에서:

```text
audit_match_rounds / success
```

를 확인합니다.

`metadata.issue_counts`가 `{}`이면 현재 match/round 데이터가 깨끗한 상태입니다.

## 기존 StarUniv match_link.py는?

아직 삭제하지 않습니다.

현재 StarUniv 브라우저 코드도 이미 Supabase의 `match_no`를 `_match_key`로 사용하고
내전 mirror를 브라우저에서 생성하고 있습니다.

Part 7 검증이 끝난 후 웹 레포 정리 단계에서:

- `scripts/match_link.py`
- JSON용 연결 캐시
- 중복 mirror 생성 코드

중 어떤 것이 정말 남아 있어야 하는지 다시 정리합니다.

## Pipeline 순서

Part 7 audit는 외부 데이터 수집 결과와 독립적이고, StarUniv 관리자 입력 데이터만 검사합니다.
그래서 메인 파이프라인 **마지막**에 실행하도록 두었습니다.

```text
healthcheck
→ sync_roster
→ sync_eloboard
→ calculate_eloboard_stats
→ sync_synergy_daily
→ sync_videos
→ audit_match_rounds
```

운영 데이터는 수정하지 않으므로 실패하더라도 이전 사이트 데이터에는 영향이 없습니다.
