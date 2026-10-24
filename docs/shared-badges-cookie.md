# Shared SOOP notices

The operator selected public publication of all collected notices, including favorites-only notices. Reading notices requires no site login. Credentials stay server-side and SOOP enforces the cookie account's access upstream.

## Architecture

- badges decrypts its existing encrypted `badge_files` cookie with its Vercel `COOKIE_KEY`. Its authenticated `/api/shared-notices` endpoint returns only whitelisted notice fields.
- ststat authenticates to that fixed endpoint using the existing server credential from `badge_notice_bridge_secret()`, executable only by service_role. No cookie or encryption key is transferred to ststat in this mode.
- `live-status` scans active members' first pages every two minutes. `member-notices` serves a selected member's stored first page and caches subsequent pages for two minutes. Missing stored pages are collected on demand.
- StarUniv personal views request only the selected member. The all-member first-page RPC is used by All Notices and the home fallback. Personal More skips visitor-only and duplicate pages until roughly ten new own notices are collected, the board ends, or fifty source pages have been scanned.

## Deployment

1. Apply badges `supabase/badges.sql` for credential-protected cookie reads, schedule-only public reads, and the service-role-only bridge credential RPC.
2. Deploy badges, including `api/shared-notices.js`. Its existing `COOKIE_KEY` and `BADGE_WRITE_SECRET` remain in Vercel.
3. Apply ststat `supabase/notice-access.sql` for the RLS-protected page cache with server-only grants.
4. Deploy `live-status` and `member-notices` with their relative module dependencies. Preserve `verify_jwt=false`: the existing cron invokes live-status anonymously, and member-notices intentionally provides public GET-only notice reads. Database writes use service_role.
5. Deploy StarUniv after the server endpoint is ready.

Existing direct-key installations may use `BADGES_COOKIE_KEY` matching badges `COOKIE_KEY`. Never put keys, cookies or server credentials in source code, browser configuration, issues or logs. The preferred bridge mode needs no copied encryption key.

## Failure behavior and security

Missing or rejected cookies, decryption errors and bridge failures fall back to independent public SOOP reads on the requested page. If both sources fail, the page endpoint returns a generic 503 and never serves an expired page. First-page scans retain previous records when a member cannot be collected, preserving existing behavior.

Credential-bearing requests reject redirects and use fixed allowlisted hosts. Public responses include only notice fields and page counts. Anonymous `badge_get('cookie')` returns no rows; `badge_get_secret` verifies a server credential. Cookies changing in badges take effect after its ten-second storage cache and the next ststat scan or page-cache miss.

## Verification

Run `node --test tests/test_soop_posts.mjs tests/test_notice_handler.mjs` and `python -m pytest -q` in ststat, and the Node/browser tests in StarUniv. Coverage includes encryption compatibility, credential rotation, authenticated bridge reads, public fallback, selected-member filtering, sparse pagination, retries and safe errors.
