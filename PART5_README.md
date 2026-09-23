# ststat Part 5 — Synergy 풍고 + 일별 통계 중앙화

Part 5는 Synergy가 직접 하던 풍고 수집, 월 누적 스폰전적 계산, `latest.json`/`archive` 성격의 일별 스냅샷, `modified_at` 기반 과거 메타데이터 소급보정을 `ststat`로 옮깁니다.

## 이번 단계에서 바뀌는 데이터 흐름

- 풍고 월 누적 통계: `poonggo.com/api/monthly` → `ststat` → `poonggo_monthly_stats`
- 스폰전적: 외부 EloBoard를 다시 호출하지 않고 Part 3의 `elo_matches`를 월 범위로 집계
- 일별 Synergy 스냅샷: `daily_member_stats`
- 종료된 월의 마지막 날: 풍고를 사후 재조회하고 EloBoard DB를 재집계하여 확정
- `tier_members.modified_at`: 해당 날짜 이후 `daily_member_stats`의 닉네임/팀/티어/종족/역할/elo_id 소급보정이 성공한 뒤에만 NULL 처리

## 중요

Part 5에서는 Synergy 웹을 아직 바꾸지 않습니다. 기존 `data/latest.json`, `data/archive/**`와 기존 Synergy workflow는 **검증용 fallback/비교 대상으로 그대로 유지**합니다.

## 적용 순서

1. 이 ZIP의 파일을 기존 `ststat` 폴더에 덮어씁니다.
2. Supabase SQL Editor에서 `migrations/005_synergy_daily_stats.sql`을 1회 실행합니다.
3. 기존 Synergy archive를 Supabase로 가져오는 것을 권장합니다. 로컬 CMD에서 `SUPABASE_URL`과 `SUPABASE_SERVICE_ROLE_KEY`가 설정된 상태로:

```cmd
python scripts\import_synergy_archives.py "C:\Users\ys2ok\OneDrive\문서\synergy-main"
```

실제 Synergy 폴더명이 다르면 마지막 경로만 바꾸면 됩니다. 이 스크립트는 `data/archive/**/*.json`과 `data/latest.json`을 읽어 `daily_member_stats`에 upsert합니다. 기존 JSON은 삭제하지 않습니다.

4. 커밋/푸시:

```cmd
git add .
git commit -m "Centralize Synergy daily stats"
git push origin main
```

5. GitHub Actions에서 `Run ststat pipeline`을 실행합니다.

실행 순서는:

```text
healthcheck
→ sync_roster
→ sync_eloboard
→ calculate_eloboard_stats
→ sync_synergy_daily
```

6. Supabase `sync_jobs`에서 `sync_synergy_daily / success` 확인.

## 확인할 테이블

- `poonggo_monthly_stats`
- `daily_member_stats`
- `synergy_month_confirmations`

오늘 날짜의 `daily_member_stats` 행 수가 현재 `tier_members` 유효 로스터 수와 비슷해야 합니다. 현재 데이터 기준으로 약 1,200명대입니다.

## 안전장치

- 유효 로스터가 100명 미만이면 쓰기 중단
- 풍고 응답이 로스터의 50% 미만이면 부분 장애로 판단하고 일별 스냅샷 쓰기 중단
- 스폰전적은 중앙 `elo_matches`를 사용하므로 EloBoard 중복 크롤링 없음
- `modified_at`은 소급보정 완료 후에만 비움
- 현재 Synergy JSON/사이트는 이번 파트에서 변경하지 않음

## 다음 단계

Part 5 결과와 기존 Synergy `latest.json/archive`를 비교해 값이 맞는지 확인한 뒤, 웹 읽기 경로를 Supabase로 전환하고 기존 수집 workflow/자동생성 JSON을 제거합니다.
