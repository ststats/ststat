# ststat Part 3 — EloBoard 수집 중앙화

이번 단계는 **EloBoard 원본 수집만** `ststat`로 옮깁니다.
H2H/랭킹/종족전 계산은 Part 4에서 옮깁니다.

## 적용 순서

1. 이 ZIP의 파일을 현재 `ststat` 저장소 루트에 덮어씁니다.
2. Supabase SQL Editor에서 `migrations/003_eloboard_pipeline.sql`을 1회 실행합니다.
3. 기존 Part 1/2의 GitHub Variable/Secret을 그대로 사용합니다.
   - Variable: `SUPABASE_URL`
   - Secret: `SUPABASE_SERVICE_ROLE_KEY`
4. 커밋/푸시합니다.

```cmd
git add .
git commit -m "Centralize EloBoard collection"
git push origin main
```

5. GitHub Actions → `Run ststat pipeline`을 수동 실행합니다.
6. Supabase `sync_jobs`에서 `sync_eloboard`가 `success`인지 확인합니다.

## 이 job이 쓰는 테이블

- `elo_categories`
- `elo_maps`
- `elo_players`
- `elo_matches`
- `tier_member_candidates` (미등록 Elo 선수의 후보 staging만)

`members`, `tier_members`의 수동 필드, 실시간 방송 필드는 건드리지 않습니다.

## 증분 수집

- DB의 최신 `elo_match_id`를 기준으로 증분 수집합니다.
- 최근 3일은 다시 읽어 EloBoard의 사후 수정도 반영합니다.
- 훑은 범위 안에서 원본에서 삭제된 경기는 DB에서도 제거합니다.
- 요청이 중간에 실패하거나 파싱 유효율이 비정상적이면 **아무 것도 쓰지 않고 실패**합니다.

## 신규 선수

경기 API는 SOOP ID 없이 Elo ID만 주므로, 미등록 선수는 `tier_members`에 자동 추가하지 않습니다.
`tier_member_candidates`에 `elo:<elo_id>` 임시 후보로만 기록합니다.

Part 2의 티어 API 수집에서 실제 SOOP ID가 확인되면 별도 후보가 생성될 수 있습니다. 사람 검토 후 정식 로스터에 넣는 정책은 유지합니다.

## 아직 StarUniv/Synergy의 기존 EloBoard 수집기를 삭제하지 마세요

Part 3에서는 `ststat` 결과가 기존 수집 결과와 안정적으로 일치하는지 먼저 봅니다.
기존 수집기 제거는 Part 4/후속 전환 단계에서 합니다.
