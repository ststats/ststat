// SOOP 공지 공용 읽기. 로그인 정보는 member_posts·로그·브라우저 어디에도 남기지 않는다.
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

// 기존 member_posts JSON 모양을 지킨다. 고정 공지도 넣고 겹치는 글은 한 번만.
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
  // Web Crypto는 암호문 뒤에 태그를 기대하고, badges(Node)는 태그를 암호문 앞에 저장한다.
  const joined = new Uint8Array(encrypted.length + tag.length);
  joined.set(encrypted);
  joined.set(tag, encrypted.length);
  const plain = await cryptoApi.subtle.decrypt({ name: 'AES-GCM', iv, tagLength: 128 }, key, joined);
  const cookie = new TextDecoder('utf-8', { fatal: true }).decode(plain);
  if (!cookie.includes('=') || /[\r\n]/.test(cookie)) throw new Error('Invalid decrypted cookie');
  return cookie;
}

// RLS로 막힌 표를 service_role로 직접 읽는다. 로그인 정보는 공개 함수로 읽지 않는다.
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

// 로그인 읽기는 badges가 쓰는 허용된 SOOP 주소 두 곳을 차례로 시도한다.
// 실패하면 부르는 쪽이 쿠키 없이 다시 읽을 수 있다(볼 수 없게 된 글은 버린다).
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

// 수집할 때마다 badges의 최신 쿠키를 읽는다(쿠키를 따로 복사해 두지 않는다).
// 공개 범위가 제한된 글은 운영자가 명시적으로 허락했을 때만 다시 게시한다.
export async function collectMemberPosts(ids, {
  allowFavorites = false, cookieKey = '', supabaseUrl = '', serviceKey = '',
  fetcher = fetch, concurrency = 6, timeoutMs = 8000, page = 1,
} = {}) {
  let cookie = '';
  let bridgeSecret = '';
  let cookieStatus = 'disabled';
  if (allowFavorites) {
    if (cookieKey.length < 16) {
      // Vercel 비밀값은 다시 읽을 수 없다. SOOP 쿠키나 암호화 키를 옮기지 않고
      // 이미 복호화할 수 있는 badges 서버를 쓴다.
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
        // 로그인 정보가 거절되면 예전에 받은 비공개 글을 계속 게시하지 않는다.
        fallbackMembers++;
        found = await fetchSoopBoard(id, { page, fetcher, timeoutMs }).catch(() => null);
      }
      if (found) results[index] = { id, ...found };
    }
  }));
  return { results: results.filter(Boolean), cookieStatus, fallbackMembers };
}
