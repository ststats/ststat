# Shared SOOP notices (badges → ststat → StarUniv)

The operator has explicitly selected anonymous public publication of all notices accessible to the shared SOOP account, including favorites-only notices. Supabase Auth login is therefore not required to read notice content. SOOP permissions are still enforced upstream; this code does not bypass them.

## Deployment

1. Apply `supabase/notice-access.sql`. The new page cache has RLS and server-only grants.
2. Preferred production mode: apply badges `supabase/shared-notices.sql` and deploy its authenticated `api/shared-notices` reader. ststat uses its service-role-only `badge_notice_bridge_secret()` RPC to authenticate to that fixed endpoint. The existing Vercel COOKIE_KEY and SOOP cookie remain in badges; only whitelisted notice data crosses between servers. No copying or re-entry of the non-readable Vercel Secret is needed. An existing direct-key installation may still set `BADGES_COOKIE_KEY` securely; never expose it in source, logs, issues, or browser config.
3. Deploy `live-status/index.ts` and its `soop-posts.mjs` dependency. Preserve `verify_jwt=false` for the existing cron and dry-status endpoint. Live broadcast scanning and public RPC contracts are preserved.
4. Deploy `member-notices/index.ts`, `handler.mjs`, and the shared `live-status/soop-posts.mjs` dependency. Set `verify_jwt=false`: this is an intentionally anonymous GET-only reader. Server writes use service_role. Browser authorization never determines the upstream cookie.
5. Deploy StarUniv PR #4 after the Edge endpoint is ready. The first page uses the existing Supabase RPC; missing first pages and pages 2–1000 use the Supabase Edge endpoint. The browser makes no direct SOOP requests.

The existing encrypted badge_files cookie is read and decrypted by badges in bridge mode (or ststat in direct-key mode) at each cache miss/collection. Bridge requests require the existing server write credential; unauthenticated clients receive 401 and no notice or credential data. AES-256-GCM uses the existing badges envelope. Missing keys/cookies, decryption errors, or rejected SOOP requests fall back to cookie-free reads on the same requested page. Public responses contain only whitelisted post fields and totalPages; they never contain cookie, ciphertext, key, or upstream error bodies. Redirects on credential-bearing requests are rejected.

Page caches expire after two minutes. Cookie updates take effect on the next miss; first-page scans run every two minutes. On complete upstream failure, the page endpoint returns 503 without serving an expired page. The existing first-page collector preserves previous records if both authenticated and public requests fail, as before. Previously published content may remain in downstream copies; public publication is intentional.

## Verification

Run `node --test tests/test_soop_posts.mjs tests/test_notice_handler.mjs` and `python -m pytest -q`. Tests cover encryption interoperability, modified/wrong keys, credential rotation, cookie rejection fallback, anonymous page 2, roster/input restrictions, cache expiry, safe errors, and credential isolation.

## Existing badges storage follow-up

The legacy `badge_get('cookie')` RPC returns encrypted ciphertext to anonymous callers. The integration reads the RLS-protected table directly and never calls that RPC. Apply badges `supabase/close-public-cookie.sql` only after the new production reader is verified. Its `badge_get_secret` RPC validates the existing server credential; legacy `badge_get` then exposes schedule only. This preserves schedule and cookie editing features while closing anonymous ciphertext access.
