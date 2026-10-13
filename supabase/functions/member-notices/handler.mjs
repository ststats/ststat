import { collectMemberPosts } from '../live-status/soop-posts.mjs';

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, apikey, content-type, x-client-info',
  'Access-Control-Allow-Methods': 'GET, OPTIONS',
  'Cache-Control': 'no-store',
  'Content-Type': 'application/json; charset=utf-8',
};
const reply = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: cors });

export function createNoticeHandler({ supabaseUrl, serviceKey, cookieKey = '', fetcher = fetch, now = () => Date.now() }) {
  const headers = { apikey: serviceKey, Authorization: 'Bearer ' + serviceKey, 'Content-Type': 'application/json' };
  async function db(path, options = {}) {
    const response = await fetcher(new URL('/rest/v1/' + path, supabaseUrl), {
      ...options, headers: { ...headers, ...options.headers }, redirect: 'error', signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error('Notice storage unavailable');
    const body = await response.text();
    return body ? JSON.parse(body) : null;
  }
  return async request => {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'GET') return reply({ error: 'Method not allowed' }, 405);
    const url = new URL(request.url);
    const id = (url.searchParams.get('soop_id') || '').toLowerCase();
    const page = Number(url.searchParams.get('page') || 1);
    if (!/^[a-z0-9_]{1,50}$/.test(id) || !Number.isSafeInteger(page) || page < 1 || page > 1000)
      return reply({ error: 'Invalid notice page' }, 400);
    try {
      // Restrict collection to the existing active roster; callers cannot use this as a general proxy.
      const members = await db('members?select=soop_id&left_date=is.null&soop_id=eq.' + encodeURIComponent(id) + '&limit=1');
      if (!members.length) return reply({ error: 'Member not found' }, 404);
      if (page === 1) {
        // First pages are already collected by live-status. Return this member
        // only instead of making the browser download the entire roster's feed.
        const rows = await db('member_posts?select=post,total_pages&soop_id=eq.' + encodeURIComponent(id) + '&order=reg_date.desc');
        if (rows.length) return reply({ posts: rows.map(row => row.post), totalPages: Number(rows[0].total_pages) || 1 });
      }
      const cache = await db('notice_public_pages?select=payload,fetched_at&soop_id=eq.' + encodeURIComponent(id) + '&page=eq.' + page);
      if (cache[0] && now() - Date.parse(cache[0].fetched_at) < 120000)
        return reply(cache[0].payload);
      const scan = await collectMemberPosts([id], {
        allowFavorites: true, cookieKey, supabaseUrl, serviceKey, page, fetcher,
      });
      const board = scan.results[0];
      if (!board) return reply({ error: 'Notices temporarily unavailable' }, 503);
      await db('notice_public_pages?on_conflict=soop_id,page', {
        method: 'POST', headers: { Prefer: 'resolution=merge-duplicates,return=minimal' },
        body: JSON.stringify({ soop_id: id, page, payload: { posts: board.posts, totalPages: board.totalPages }, fetched_at: new Date(now()).toISOString() }),
      });
      return reply({ posts: board.posts, totalPages: board.totalPages });
    } catch (_) { return reply({ error: 'Notices temporarily unavailable' }, 503); }
  };
}
