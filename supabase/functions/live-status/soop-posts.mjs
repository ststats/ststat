// Shared SOOP notice reader. No login credential is stored in member_posts, logs or browser.
// Mirrors ststats/badges/src/live.mjs: base64(IV[12] || GCM tag[16] || ciphertext)
// with AES-256-GCM and SHA-256(COOKIE_KEY).

const SOOP_HOSTS = ['api-channel.sooplive.com', 'api-channel.sooplive.co.kr'];
const BOARD_FIELDS = 'title,contents,user_nick,user_id,hashtags';

export function boardApiUrl(soopId, page = 1, host = SOOP_HOSTS[0], perPage = 10) {
  if (!/^[a-z0-9_]+$/i.test(soopId) || !SOOP_HOSTS.includes(host)) throw new Error('Invalid SOOP board');
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(perPage) || perPage < 1 || perPage > 50)
    throw new Error('Invalid SOOP page');
  const params = new URLSearchParams({
    perPage: String(perPage), startDate: '', endDate: '', field: BOARD_FIELDS,
    keyword: '', type: 'all', orderBy: 'reg_date', page: String(page), bbsNo: '',
  });
  return 'https://' + host + '/v1.1/channel/' + encodeURIComponent(soopId) + '/board?' + params;
}

// Preserve the existing member_posts JSON contract; include pinned notices and deduplicate.
export function ownPosts(board, soopId) {
  const owner = String(soopId).toLowerCase();
  const all = [...(Array.isArray(board?.contents) ? board.contents : []),
    ...(Array.isArray(board?.noticeData) ? board.noticeData : [])]
    .filter(p => p && String(p.userId || '').toLowerCase() === owner);
  const seen = new Set();
  return all.filter(p => (seen.has(String(p.titleNo)) ? false : (seen.add(String(p.titleNo)), true)))
    .map(p => ({
      titleNo: p.titleNo,
      titleName: p.titleName ?? null,
      regDate: p.regDate ?? null,
      userId: p.userId ?? null,
      userNick: p.userNick ?? null,
      content: p.content ? { textContent: p.content.textContent ?? '', content: p.content.content ?? '' } : null,
      display: p.display ? { bbsName: p.display.bbsName ?? '' } : null,
      count: p.count ? { likeCnt: p.count.likeCnt ?? 0, readCnt: p.count.readCnt ?? 0 } : null,
      photos: Array.isArray(p.photos) ? p.photos.map(x => ({ url: x?.url ?? '' })).filter(x => x.url) : [],
    }));
}

export async function decryptBadgeCookie(ciphertext, secret, cryptoApi = globalThis.crypto) {
  if (!secret || secret.length < 16) throw new Error('Missing cookie decryption key');
  const bytes = Uint8Array.from(atob(String(ciphertext).trim()), c => c.charCodeAt(0));
  if (bytes.length <= 28) throw new Error('Invalid cookie ciphertext');
  const iv = bytes.slice(0, 12);
  const tag = bytes.slice(12, 28);
  const encrypted = bytes.slice(28);
  const keyHash = await cryptoApi.subtle.digest('SHA-256', new TextEncoder().encode(secret));
  const key = await cryptoApi.subtle.importKey('raw', keyHash, { name: 'AES-GCM' }, false, ['decrypt']);
  // Web Crypto expects ciphertext || tag; Node badges stores tag before ciphertext.
  const joined = new Uint8Array(encrypted.length + tag.length);
  joined.set(encrypted);
  joined.set(tag, encrypted.length);
  const plain = await cryptoApi.subtle.decrypt({ name: 'AES-GCM', iv, tagLength: 128 }, key, joined);
  const cookie = new TextDecoder('utf-8', { fatal: true }).decode(plain);
  if (!cookie.includes('=') || /[\r\n]/.test(cookie)) throw new Error('Invalid decrypted cookie');
  return cookie;
}

// Direct RLS-protected table read using service_role. Never use public badge_get for credentials.
export async function loadBadgeCookie({ supabaseUrl, serviceKey, secret, fetcher = fetch, cryptoApi = globalThis.crypto }) {
  if (!/^https:\/\//.test(supabaseUrl) || !serviceKey) throw new Error('Cookie source unavailable');
  const endpoint = new URL('/rest/v1/badge_files', supabaseUrl);
  endpoint.search = 'select=content&name=eq.cookie&limit=1';
  const response = await fetcher(endpoint.toString(), {
    redirect: 'error',
    headers: { apikey: serviceKey, Authorization: 'Bearer ' + serviceKey, Accept: 'application/json' },
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) throw new Error('Cookie source unavailable');
  const rows = await response.json();
  if (!Array.isArray(rows)) throw new Error('Unexpected cookie source response');
  if (!rows.length) return '';
  if (typeof rows[0]?.content !== 'string') throw new Error('Unexpected cookie source response');
  return decryptBadgeCookie(rows[0].content, secret, cryptoApi);
}

// Authenticated reads retry the two allowlisted SOOP hosts used by badges.
// A caller can retry without a cookie on failure (and discard inaccessible posts).
export async function fetchSoopBoard(soopId, { cookie = '', page = 1, perPage = 10, fetcher = fetch, timeoutMs = 8000 } = {}) {
  const hosts = cookie ? SOOP_HOSTS : SOOP_HOSTS.slice(0, 1);
  for (const host of hosts) {
    const url = boardApiUrl(soopId, page, host, perPage);
    const headers = { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' };
    if (cookie) {
      headers.Cookie = cookie;
      headers.Referer = 'https://www.sooplive.co.kr/station/' + encodeURIComponent(soopId) + '/board';
    }
    try {
      const response = await fetcher(url, { headers, redirect: 'error', signal: AbortSignal.timeout(timeoutMs) });
      if (!response.ok) continue;
      const board = await response.json();
      if (!Array.isArray(board?.contents)) continue;
      const total = Number(board?.meta?.totalPages);
      return { posts: ownPosts(board, soopId), totalPages: Number.isSafeInteger(total) && total > 0 ? total : 1 };
    } catch (_) { /* never include cookie in an error */ }
  }
  throw new Error('SOOP board unavailable');
}

// Read the latest badges cookie on every collection, without duplicating its storage.
// Restricted posts are only republished after an explicit operator opt-in.
export async function collectMemberPosts(ids, {
  allowFavorites = false, cookieKey = '', supabaseUrl = '', serviceKey = '',
  fetcher = fetch, concurrency = 6, timeoutMs = 8000, page = 1,
} = {}) {
  let cookie = '';
  let bridgeSecret = '';
  let cookieStatus = 'disabled';
  if (allowFavorites) {
    if (cookieKey.length < 16) {
      // Vercel Secret values cannot be read back. Use its existing decrypting
      // server without transferring the SOOP cookie or encryption key.
      cookieStatus = 'bridge_unavailable';
      try {
        const response = await fetcher(new URL('/rest/v1/rpc/badge_notice_bridge_secret', supabaseUrl), {
          method: 'POST', body: '{}', redirect: 'error',
          headers: { apikey: serviceKey, Authorization: 'Bearer ' + serviceKey, 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(timeoutMs),
        });
        if (response.ok) {
          const value = await response.json();
          if (typeof value === 'string' && value.length >= 16) {
            bridgeSecret = value;
            cookieStatus = 'bridge_ready';
          }
        }
      } catch (_) { /* public fallback; never log bridge credentials */ }
    }
    else {
      try {
        cookie = await loadBadgeCookie({ supabaseUrl, serviceKey, secret: cookieKey, fetcher });
        cookieStatus = cookie ? 'loaded' : 'missing';
      } catch (_) {
        cookieStatus = 'unavailable';
      }
    }
  }
  const results = new Array(ids.length).fill(null);
  let cursor = 0;
  let fallbackMembers = 0;
  await Promise.all(Array.from({ length: Math.min(Math.max(1, concurrency), ids.length) }, async () => {
    while (cursor < ids.length) {
      const index = cursor++;
      const id = ids[index];
      let found = null;
      if (bridgeSecret) {
        try {
          const url = new URL('https://staruniv-badges.vercel.app/api/shared-notices');
          url.searchParams.set('soop_id', id);
          url.searchParams.set('page', String(page));
          const response = await fetcher(url, {
            headers: { Authorization: 'Bearer ' + bridgeSecret, Accept: 'application/json' },
            redirect: 'error', signal: AbortSignal.timeout(20000),
          });
          if (response.ok) {
            const data = await response.json();
            if (Array.isArray(data?.posts)) {
              const total = Number(data.totalPages);
              found = { posts: ownPosts({ contents: data.posts }, id), totalPages: Number.isSafeInteger(total) && total > 0 ? total : 1 };
            }
          }
        } catch (_) { /* independent public read below */ }
        if (!found) fallbackMembers++;
      }
      if (!found) found = await fetchSoopBoard(id, { cookie, page, fetcher, timeoutMs }).catch(() => null);
      if (!found && cookie) {
        // Never continue publishing stale private posts after credential rejection.
        fallbackMembers++;
        found = await fetchSoopBoard(id, { page, fetcher, timeoutMs }).catch(() => null);
      }
      if (found) results[index] = { id, ...found };
    }
  }));
  return { results: results.filter(Boolean), cookieStatus, fallbackMembers };
}
