# ststat Part 6 — 영상 자동 수집 중앙화

이번 단계는 **StarUniv 영상 탭의 자동 YouTube 수집만 `ststat`로 이동**합니다.

## 소유권

### ststat가 자동 관리
- `video_channels.channel_id`
- `video_channels.title`
- `video_channels.thumb`
- `video_channels.uploads`
- `videos.channel_url`
- `videos.title`
- `videos.published`
- `videos.thumb`
- `videos.views`
- `videos.short`

### StarUniv 관리자가 계속 관리
- 채널 URL
- 채널 표시명
- 채널 순서
- 채널 활성/비활성
- 수집 영상 숨김(`videos.hidden`)
- 추천 영상(`video_picks`) 전체

따라서 4시간 동기화가 돌아도 관리자가 숨긴 영상이 다시 노출되지 않습니다.

## 적용

1. ZIP 내용을 현재 `ststat` 레포 루트에 덮어씁니다.
2. Supabase SQL Editor에서 다음 파일을 1회 실행합니다.

```text
migrations/006_video_pipeline.sql
```

3. GitHub `ststat` → Settings → Secrets and variables → Actions → Secrets에
   `YOUTUBE_API_KEY`를 추가하는 것을 권장합니다.

키가 없어도 RSS fallback으로 동작하지만 채널당 최신 약 15개만 받을 수 있습니다.
기존 StarUniv에서 사용하던 YouTube API Key가 있다면 같은 값을 `ststat` Secret으로 옮기면 됩니다.

4. 커밋/푸시:

```cmd
git add .
git commit -m "Centralize video collection"
git push origin main
```

5. GitHub Actions → `Run ststat pipeline` 실행

순서:

```text
healthcheck
→ sync_roster
→ sync_eloboard
→ calculate_eloboard_stats
→ sync_synergy_daily
→ sync_videos
```

6. Supabase `sync_jobs`에서 `sync_videos / success` 확인

## 안전장치

- 채널 하나가 실패해도 다른 채널은 계속 수집합니다.
- 모든 채널이 실패하면 job 자체를 실패 처리합니다.
- 실패한 채널의 기존 DB 데이터는 삭제하지 않습니다.
- `video_picks`는 읽거나 쓰지 않습니다.
- `videos.hidden`은 기존 값을 보존합니다.
- 채널의 관리자 설정(`display_name`, `source_order`, `active`)을 덮어쓰지 않습니다.

## StarUniv의 기존 `scripts/sync_videos.py`

아직 삭제하지 마세요.

이번 Part 6에서는 `ststat` 결과가 정상적으로 갱신되는지만 먼저 확인합니다.
다음 웹 정리 단계에서 StarUniv workflow의 `sync_videos.py` 실행을 끊고 파일을 제거합니다.
