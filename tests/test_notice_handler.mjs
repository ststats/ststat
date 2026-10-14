import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { createNoticeHandler } from '../supabase/functions/member-notices/handler.mjs';

const key = 'test-only-cookie-key-12345';
function encrypted() {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', crypto.createHash('sha256').update(key).digest(), iv);
  const body = Buffer.concat([cipher.update('session=test-cookie'), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString('base64');
}
const json = data => new Response(JSON.stringify(data));
function setup({ expired = false, cache = [], member = true, unavailable = false, firstPageRows = [] } = {}) {
  const calls = [], writes = [];
  const handler = createNoticeHandler({ supabaseUrl: 'https://test.supabase.co', serviceKey: 'server-test-key', cookieKey: key,
    fetcher: async (input, options) => {
      const url = new URL(input);
      calls.push({ url, options });
      if (url.pathname.endsWith('/members')) return json(member ? [{ soop_id: 'alice' }] : []);
      if (url.pathname.endsWith('/member_posts')) return json(firstPageRows);
      if (url.pathname.endsWith('/badge_files')) return json([{ content: encrypted() }]);
      if (url.pathname.endsWith('/notice_public_pages')) {
        if (options.method === 'POST') { writes.push(JSON.parse(options.body)); return new Response(null, { status: 201 }); }
        return json(cache);
      }
      if (unavailable || (expired && options.headers.Cookie)) return new Response('upstream secret must not escape', { status: 500 });
      assert.equal(url.searchParams.get('page'), '2');
      assert.equal(options.redirect, 'error');
      assert.equal(options.headers.Authorization, undefined);
      return json({ contents: [{ titleNo: options.headers.Cookie ? 2 : 1, userId: 'alice' }], meta: { totalPages: 4 } });
    },
  });
  return { handler, calls, writes };
}
const request = query => new Request('https://test.supabase.co/functions/v1/member-notices?' + query);
test('익명 2쪽 요청은 허락된 공지를 비밀 칸 없이 준다', async () => {
  const { handler, writes } = setup();
  const response = await handler(request('soop_id=alice&page=2'));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  const body = await response.json();
  assert.equal(body.posts[0].titleNo, 2);
  assert.equal(body.totalPages, 4);
  assert.equal(writes[0].page, 2);
  assert.doesNotMatch(JSON.stringify(body), /test-cookie|server-test-key|content.*encrypted/);
});
test('쿠키가 만료되면 같은 쪽의 공개 공지로 대신한다', async () => {
  const { handler, writes } = setup({ expired: true });
  const response = await handler(request('soop_id=alice&page=2'));
  assert.equal((await response.json()).posts[0].titleNo, 1);
  assert.equal(writes[0].payload.posts[0].titleNo, 1);
});
test('캐시가 새것이면 SOOP과 쿠키를 읽지 않는다', async () => {
  const { handler, calls } = setup({ cache: [{ payload: { posts: [], totalPages: 3 }, fetched_at: new Date().toISOString() }] });
  assert.equal((await handler(request('soop_id=alice&page=2'))).status, 200);
  assert.equal(calls.length, 2);
});
test('개인 첫 쪽은 이미 모은 것에서 그 멤버 것만 읽고 SOOP·연결을 부르지 않는다', async () => {
  const { handler, calls } = setup({ firstPageRows: [{ post: { titleNo: 8, userId: 'alice' }, total_pages: 5 }] });
  const response = await handler(request('soop_id=alice&page=1'));
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.posts[0].titleNo, 8);
  assert.equal(body.totalPages, 5);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].url.searchParams.get('soop_id'), 'eq.alice');
});
test('입력 검사와 멤버 확인으로 아무 주소나 대신 읽지 못하게 막는다', async () => {
  const { handler, calls } = setup({ member: false });
  for (const query of ['soop_id=evil.test&page=1', 'soop_id=alice&page=0', 'soop_id=alice&page=1001', 'soop_id=alice&page=1.5'])
    assert.equal((await handler(request(query))).status, 400);
  assert.equal(calls.length, 0);
  assert.equal((await handler(request('soop_id=alice&page=2'))).status, 404);
  assert.equal(calls.length, 1);
});
test('양쪽 다 실패하면 일반 오류만 알리고 실패는 캐시하지 않는다', async () => {
  const { handler, writes } = setup({ unavailable: true });
  const response = await handler(request('soop_id=alice&page=2'));
  assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /secret|cookie|key/);
  assert.equal(writes.length, 0);
});
