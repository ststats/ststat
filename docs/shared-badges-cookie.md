# Shared SOOP notice cookie (badges → ststat)

The StarUniv notice collector can reuse the **existing encrypted cookie** saved from the badges admin page. Neither a new cookie table nor redeploying when the cookie changes is needed.

## Privacy and deployment gate

**Default OFF:** only public posts are collected until the operator explicitly enables republication of SOOP favorites-only posts. Do not enable this without permission to republish those posts publicly. The account used by badges must be allowed to view the relevant members' posts.

1. Configure Supabase **Edge Functions → Secrets**:
   - `BADGES_COOKIE_KEY`: exactly the value of the existing **Vercel badges `COOKIE_KEY`**. Do not commit it, paste it into GitHub issues, or expose it to the browser.
   - `SOOP_FAVORITES_PUBLICATION_ENABLED`: keep unset or `false` for safe public-only operation. Change to `true` only after confirming authorization to publish favorites-only material publicly.
2. Deploy both files in `supabase/functions/live-status/` as a single `live-status` function. Preserve its currently deployed `verify_jwt=false` setting for the existing pg_cron trigger; harden endpoint invocation separately.
3. The 2-minute scan reads the current encrypted `public.badge_files(name='cookie')` via **service_role** and decrypts it server-side. Cookie changes in badges apply to the following scan; no rebuild, SQL change, or copy into ststat is necessary.
4. Inspect `posts.cookie_status` in the function result: `disabled`, `key_missing`, `missing`, `unavailable`, or `loaded`. `fallback_members` counts members retried publicly after authenticated read failure. Neither status exposes a credential.
5. To disable publication again, set `SOOP_FAVORITES_PUBLICATION_ENABLED=false`. The next successful scan replaces member first-page posts with publicly accessible posts; double-check that any previously cached/client copies are purged as appropriate.

## Boundaries

- The cookie is used only in server-originated SOOP requests to the two allowlisted API hosts. It is never placed in `member_posts` or a public API response.
- Existing `member_posts` and StarUniv UI contracts remain unchanged.
- The first page is kept at 10 records per member for compatibility; page 2+ remains read directly by the StarUniv browser without a cookie. **This PR does not make favorites-only posts on page 2+ available**. Implement private server-side pagination (and an authorization model) separately if that is needed.
- Invalid cookies and failed SOOP requests retry or fall back to public-only posts. The `replace_member_posts` operation preserves entries for members whose boards could not be read, as before.
- The current `badge_get` SQL RPC is publicly callable and returns an encrypted ciphertext for `cookie`; this integration deliberately bypasses that RPC and instead uses the protected table via service_role. A further hardening effort should revoke public access to encrypted credentials.
- `BADGES_COOKIE_KEY` in the Edge environment is a shared decryption secret, **not** the SOOP cookie itself. Rotate it together with badges if changed.

## Verification

Run `node --test tests/test_soop_posts.mjs` (Node 22+). CI runs these alongside pytest. Tests cover badges Node crypto interoperability, tamper/wrong-key rejection, same-title deduplication, public-default protection, automatic updated-cookie reads, and expired-cookie fallback.

No production SOOP cookie or secret is included in tests.
