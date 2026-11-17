// ststats/badges/src/soop-board.mjs에서 만든 사본. 직접 고치지 않는다.
// SOOP 방송국 게시판 읽기·공개 글 모양·로그인 쿠키 암호(공용).
// 원본은 여기 하나다. ststat Edge Function은 해시로 묶인 사본(supabase/functions/_shared/soop-board.mjs)을 쓴다:
// 고친 뒤 node scripts/sync-soop-board.mjs ../ststat. Node와 Deno에서 같이 돌도록 표준 API(fetch·Web Crypto)만 쓴다.

// 같은 API가 .com과 .co.kr 두 주소에 있어 하나가 안 되면 다른 쪽을 본다.
export const SOOP_HOSTS = ['api-channel.sooplive.com', 'api-channel.sooplive.co.kr'];

// 방송국 '전체 글' 화면이 부르는 것과 같은 값
export function boardApiUrl(soopId, { page = 1, perPage = 20, host = SOOP_HOSTS[0] } = {}) {
    if (!/^[a-z0-9_]+$/i.test(soopId) || !SOOP_HOSTS.includes(host)) throw new Error('Invalid SOOP board');
    if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(perPage) || perPage < 1 || perPage > 50)
        throw new Error('Invalid SOOP page');
    const url = new URL(`https://${host}/v1.1/channel/${encodeURIComponent(soopId)}/board`);
    url.search = new URLSearchParams({
        perPage: String(perPage),
        startDate: '',
        endDate: '',
        field: 'title,contents,user_nick,user_id,hashtags',
        keyword: '',
        type: 'all',
        orderBy: 'reg_date',
        page: String(page),
        bbsNo: '',
    });
    return url;
}

// 로그인 쿠키를 붙이면 즐겨찾기 전용 글도 온다. 오류 문구에는 쿠키를 넣지 않는다.
export async function readBoard(
    soopId,
    { cookie = '', page = 1, perPage = 20, fetcher = fetch, timeoutMs = 8000 } = {}
) {
    let lastError;
    for (const host of SOOP_HOSTS) {
        try {
            const headers = { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' };
            if (cookie) {
                headers.Cookie = cookie;
                headers.Referer = `https://www.sooplive.co.kr/station/${encodeURIComponent(soopId)}/board`;
            }
            const res = await fetcher(boardApiUrl(soopId, { page, perPage, host }), {
                headers,
                redirect: 'error',
                signal: AbortSignal.timeout(timeoutMs),
            });
            if (!res.ok) throw new Error(`SOOP 게시판 HTTP ${res.status}`);
            const board = await res.json();
            if (!Array.isArray(board?.contents)) throw new Error('SOOP 게시판 응답 형식이 바뀌었습니다');
            return board;
        } catch (e) {
            lastError = e;
        }
    }
    throw lastError;
}

// 밖으로 내보내는 글 모양(member_posts JSON·공지 연결 응답). 그 밖의 칸은 버린다.
export const publicPost = p => ({
    titleNo: p.titleNo,
    titleName: p.titleName ?? null,
    regDate: p.regDate ?? null,
    userId: p.userId ?? null,
    userNick: p.userNick ?? null,
    content: p.content ? { textContent: p.content.textContent ?? '', content: p.content.content ?? '' } : null,
    display: p.display ? { bbsName: p.display.bbsName ?? '' } : null,
    count: p.count ? { likeCnt: p.count.likeCnt ?? 0, readCnt: p.count.readCnt ?? 0 } : null,
    photos: Array.isArray(p.photos) ? p.photos.map(x => ({ url: x?.url ?? '' })).filter(x => x.url) : [],
});

// 쿠키 암호문: base64(IV 12 | GCM 태그 16 | 암호문), 키는 SHA-256(COOKIE_KEY). 상태 화면이 저장하고 ststat이 푼다.
async function cipherKey(secret, usage) {
    if (!secret || secret.length < 16) throw new Error('Missing cookie key');
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(secret));
    return crypto.subtle.importKey('raw', hash, { name: 'AES-GCM' }, false, [usage]);
}

export async function encryptCookie(cookie, secret) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await cipherKey(secret, 'encrypt');
    const sealed = new Uint8Array(
        await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(cookie))
    );
    // Web Crypto는 태그를 암호문 뒤에 붙이고, 저장 모양은 태그가 앞이다.
    const stored = new Uint8Array(sealed.length + 12);
    stored.set(iv);
    stored.set(sealed.subarray(-16), 12);
    stored.set(sealed.subarray(0, -16), 28);
    return btoa(String.fromCharCode(...stored));
}

export async function decryptCookie(text, secret) {
    const bytes = Uint8Array.from(atob(String(text).trim()), c => c.charCodeAt(0));
    if (bytes.length <= 28) throw new Error('Invalid cookie ciphertext');
    const sealed = new Uint8Array(bytes.length - 12);
    sealed.set(bytes.subarray(28));
    sealed.set(bytes.subarray(12, 28), bytes.length - 28);
    const key = await cipherKey(secret, 'decrypt');
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes.subarray(0, 12) }, key, sealed);
    const cookie = new TextDecoder('utf-8', { fatal: true }).decode(plain);
    if (!cookie.includes('=') || /[\r\n]/.test(cookie)) throw new Error('Invalid decrypted cookie');
    return cookie;
}
