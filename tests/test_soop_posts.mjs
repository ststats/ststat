import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { decryptBadgeCookie, collectMemberPosts, boardApiUrl, ownPosts } from '../supabase/functions/live-status/soop-posts.mjs';
const key = 'unit-test-only-1234567890';
function encrypt(text) {
  const iv = crypto.randomBytes(12), cipher = crypto.createCipheriv('aes-256-gcm', crypto.createHash('sha256').update(key).digest(), iv);
  const encrypted = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString('base64');
}
const json = x => new Response(JSON.stringify(x));
const board = () => ({ contents: [{ titleNo: 1, userId: 'alice', regDate: '2026-10-12 10:00:00' }], noticeData: [{ titleNo: 1, userId: 'alice' }, { titleNo: 2, userId: 'alice' }, { titleNo: 3, userId: 'other' }], meta: { totalPages: 2 } });
test('Node badges AES-256-GCM envelope is compatible with Edge Web Crypto', async () => {
  const encrypted = encrypt('PdboxTicket=example');
  assert.equal(await decryptBadgeCookie(encrypted, key), 'PdboxTicket=example');
  await assert.rejects(decryptBadgeCookie(encrypted, 'wrong-long-enough-key-value'));
  const bad = Buffer.from(encrypted, 'base64'); bad[30] ^= 1;
  await assert.rejects(decryptBadgeCookie(bad.toString('base64'), key));
});
test('matching posts from contents and noticeData deduplicate; outsiders excluded', () => {
  assert.deepEqual(ownPosts(board(), 'ALICE').map(p => p.titleNo), [1, 2]);
  assert.equal(new URL(boardApiUrl('alice')).searchParams.get('type'), 'all');
  assert.throws(() => boardApiUrl('https://evil.test'));
});
test('public-only default never reads saved cookie or forwards it', async () => {
  const urls = [];
  const scan = await collectMemberPosts(['alice'], { supabaseUrl: 'https://demo.supabase.co', serviceKey: 'test', cookieKey: key, fetcher: async (url, opts) => {
    urls.push([url, opts.headers]); return json(board());
  } });
  assert.equal(scan.cookieStatus, 'disabled');
  assert.equal(urls.length, 1);
  assert.equal('Cookie' in urls[0][1], false);
});
test('opted-in new badge cookie takes effect next scan; service role only', async () => {
  let current = encrypt('PdboxTicket=first');
  const headers = [];
  const fetcher = async (url, opts) => {
    headers.push(opts.headers);
    return json(url.includes('/badge_files') ? [{content:current}] : board());
  };
  const options = { allowFavorites:true, supabaseUrl:'https://demo.supabase.co', serviceKey:'service-secret', cookieKey:key, fetcher };
  assert.equal((await collectMemberPosts(['alice'], options)).cookieStatus, 'loaded');
  assert.equal(headers[0].Authorization, 'Bearer service-secret');
  assert.equal(headers[1].Cookie, 'PdboxTicket=first');
  current = encrypt('PdboxTicket=second');
  await collectMemberPosts(['alice'], options);
  assert.equal(headers[3].Cookie, 'PdboxTicket=second');
});
test('wrong decryption key falls back to public notices without sending a cookie', async () => {
  const boardHeaders = [];
  const scan = await collectMemberPosts(['alice'], { allowFavorites:true, supabaseUrl:'https://demo.supabase.co', serviceKey:'service-secret', cookieKey:'wrong-but-long-enough-key', fetcher: async (url, opts) => {
    if (url.includes('/badge_files')) return json([{content:encrypt('secret=sample')}]);
    boardHeaders.push(opts.headers); return json(board());
  } });
  assert.equal(scan.cookieStatus, 'unavailable');
  assert.ok(boardHeaders.every(h => !('Cookie' in h)));
  assert.equal(scan.results.length, 1);
});
test('expired cookie retries public collection and removes access-dependent notices', async () => {
  const scan = await collectMemberPosts(['alice'], { allowFavorites:true, supabaseUrl:'https://demo.supabase.co', serviceKey:'service-secret', cookieKey:key, fetcher: async (url, opts) => {
    if (url.includes('/badge_files')) return json([{content:encrypt('PdboxTicket=expired')}]);
    return opts.headers.Cookie ? new Response('blocked', {status:500}) : json({contents:[{titleNo:1,userId:'alice'}],noticeData:[],meta:{totalPages:1}});
  } });
  assert.equal(scan.fallbackMembers, 1);
  assert.deepEqual(scan.results[0].posts.map(p=>p.titleNo), [1]);
});

test('non-readable Vercel key uses authenticated notice bridge, never transfers SOOP credentials', async () => {
  const calls = [];
  const scan = await collectMemberPosts(['alice'], { allowFavorites: true, supabaseUrl: 'https://demo.supabase.co', serviceKey: 'server-key', page: 2,
    fetcher: async (input, options) => {
      const url = new URL(input); calls.push(url);
      assert.equal(options.redirect, 'error');
      if (url.pathname.endsWith('/badge_notice_bridge_secret')) {
        assert.equal(options.headers.Authorization, 'Bearer server-key');
        return json('test-only-bridge-secret');
      }
      assert.equal(url.origin, 'https://staruniv-badges.vercel.app');
      assert.equal(url.searchParams.get('page'), '2');
      assert.equal(options.headers.Authorization, 'Bearer test-only-bridge-secret');
      assert.equal(options.headers.Cookie, undefined);
      return json({ posts: [{ titleNo: 9, userId: 'alice', secret: 'strip' }], totalPages: 5 });
    } });
  assert.equal(scan.cookieStatus, 'bridge_ready');
  assert.equal(scan.results[0].posts[0].titleNo, 9);
  assert.equal(scan.results[0].totalPages, 5);
  assert.equal(scan.fallbackMembers, 0);
  assert.equal(calls.length, 2);
  assert.doesNotMatch(JSON.stringify(scan), /server-key|test-only-bridge-secret|strip/);
});
test('bridge authentication or deployment failure falls back to the requested public page', async () => {
  const scan = await collectMemberPosts(['alice'], { allowFavorites: true, supabaseUrl: 'https://demo.supabase.co', serviceKey: 'server-key', page: 2,
    fetcher: async (input, options) => {
      const url = new URL(input);
      if (url.pathname.endsWith('/badge_notice_bridge_secret')) return json('test-only-bridge-secret');
      if (url.hostname === 'staruniv-badges.vercel.app') return new Response(null, { status: 401 });
      assert.equal(url.searchParams.get('page'), '2');
      assert.equal(options.headers.Authorization, undefined);
      assert.equal(options.headers.Cookie, undefined);
      return json(board());
    } });
  assert.equal(scan.fallbackMembers, 1);
  assert.equal(scan.results.length, 1);
});
