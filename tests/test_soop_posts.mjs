import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import { boardApiUrl, decryptCookie } from '../supabase/functions/_shared/soop-board.mjs';
import { collectMemberPosts, ownPosts } from '../supabase/functions/live-status/soop-posts.mjs';
const key = 'unit-test-only-1234567890';
function encrypt(text) {
    const iv = crypto.randomBytes(12),
        cipher = crypto.createCipheriv('aes-256-gcm', crypto.createHash('sha256').update(key).digest(), iv);
    const encrypted = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString('base64');
}
const json = x => new Response(JSON.stringify(x));
const board = () => ({
    contents: [{ titleNo: 1, userId: 'alice', regDate: '2026-10-12 10:00:00' }],
    noticeData: [
        { titleNo: 1, userId: 'alice' },
        { titleNo: 2, userId: 'alice' },
        { titleNo: 3, userId: 'other' },
    ],
    meta: { totalPages: 2 },
});
test('공용 SOOP 모듈은 badges 원본 사본 그대로다(고칠 땐 badges에서 node scripts/sync-soop-board.mjs ../ststat)', () => {
    const copy = fs.readFileSync(new URL('../supabase/functions/_shared/soop-board.mjs', import.meta.url));
    const lock = JSON.parse(fs.readFileSync(new URL('../.github/soop-board.json', import.meta.url), 'utf-8'));
    assert.equal(crypto.createHash('sha256').update(copy).digest('hex'), lock.sha256);
});
test('예전 badges(Node)가 저장한 AES-256-GCM 암호문도 Web Crypto로 푼다', async () => {
    const encrypted = encrypt('PdboxTicket=example');
    assert.equal(await decryptCookie(encrypted, key), 'PdboxTicket=example');
    await assert.rejects(decryptCookie(encrypted, 'wrong-long-enough-key-value'));
    const bad = Buffer.from(encrypted, 'base64');
    bad[30] ^= 1;
    await assert.rejects(decryptCookie(bad.toString('base64'), key));
});
test('contents와 noticeData의 같은 글은 한 번만, 다른 사람 글은 뺀다', () => {
    assert.deepEqual(
        ownPosts(board(), 'ALICE').map(p => p.titleNo),
        [1, 2]
    );
    assert.equal(boardApiUrl('alice').searchParams.get('type'), 'all');
    assert.throws(() => boardApiUrl('https://evil.test'));
});
test('기본(공개 글만)은 저장된 쿠키를 읽지도 보내지도 않는다', async () => {
    const urls = [];
    const scan = await collectMemberPosts(['alice'], {
        supabaseUrl: 'https://demo.supabase.co',
        serviceKey: 'test',
        cookieKey: key,
        fetcher: async (url, opts) => {
            urls.push([url, opts.headers]);
            return json(board());
        },
    });
    assert.equal(scan.cookieStatus, 'disabled');
    assert.equal(urls.length, 1);
    assert.equal('Cookie' in urls[0][1], false);
});
test('허락하면 새 badges 쿠키가 다음 수집부터 쓰이고, service role로만 읽는다', async () => {
    let current = encrypt('PdboxTicket=first');
    const headers = [];
    const fetcher = async (url, opts) => {
        headers.push(opts.headers);
        return json(String(url).includes('/badge_files') ? [{ content: current }] : board());
    };
    const options = {
        allowFavorites: true,
        supabaseUrl: 'https://demo.supabase.co',
        serviceKey: 'service-secret',
        cookieKey: key,
        fetcher,
    };
    assert.equal((await collectMemberPosts(['alice'], options)).cookieStatus, 'loaded');
    assert.equal(headers[0].Authorization, 'Bearer service-secret');
    assert.equal(headers[1].Cookie, 'PdboxTicket=first');
    current = encrypt('PdboxTicket=second');
    await collectMemberPosts(['alice'], options);
    assert.equal(headers[3].Cookie, 'PdboxTicket=second');
});
test('복호화 키가 틀리면 쿠키를 보내지 않고 공개 공지로 대신한다', async () => {
    const boardHeaders = [];
    const scan = await collectMemberPosts(['alice'], {
        allowFavorites: true,
        supabaseUrl: 'https://demo.supabase.co',
        serviceKey: 'service-secret',
        cookieKey: 'wrong-but-long-enough-key',
        fetcher: async (url, opts) => {
            if (String(url).includes('/badge_files')) return json([{ content: encrypt('secret=sample') }]);
            boardHeaders.push(opts.headers);
            return json(board());
        },
    });
    assert.equal(scan.cookieStatus, 'unavailable');
    assert.ok(boardHeaders.every(h => !('Cookie' in h)));
    assert.equal(scan.results.length, 1);
});
test('쿠키가 만료되면 공개로 다시 수집하고 로그인해야 보이는 공지는 지운다', async () => {
    const scan = await collectMemberPosts(['alice'], {
        allowFavorites: true,
        supabaseUrl: 'https://demo.supabase.co',
        serviceKey: 'service-secret',
        cookieKey: key,
        fetcher: async (url, opts) => {
            if (String(url).includes('/badge_files')) return json([{ content: encrypt('PdboxTicket=expired') }]);
            return opts.headers.Cookie
                ? new Response('blocked', { status: 500 })
                : json({ contents: [{ titleNo: 1, userId: 'alice' }], noticeData: [], meta: { totalPages: 1 } });
        },
    });
    assert.equal(scan.fallbackMembers, 1);
    assert.deepEqual(
        scan.results[0].posts.map(p => p.titleNo),
        [1]
    );
});

test('Vercel 키를 못 읽으면 인증된 공지 연결을 쓰고 SOOP 로그인 정보는 옮기지 않는다', async () => {
    const calls = [];
    const scan = await collectMemberPosts(['alice'], {
        allowFavorites: true,
        supabaseUrl: 'https://demo.supabase.co',
        serviceKey: 'server-key',
        page: 2,
        fetcher: async (input, options) => {
            const url = new URL(input);
            calls.push(url);
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
        },
    });
    assert.equal(scan.cookieStatus, 'bridge_ready');
    assert.equal(scan.results[0].posts[0].titleNo, 9);
    assert.equal(scan.results[0].totalPages, 5);
    assert.equal(scan.fallbackMembers, 0);
    assert.equal(calls.length, 2);
    assert.doesNotMatch(JSON.stringify(scan), /server-key|test-only-bridge-secret|strip/);
});
test('연결 인증·배포가 실패하면 요청한 공개 쪽으로 대신한다', async () => {
    const scan = await collectMemberPosts(['alice'], {
        allowFavorites: true,
        supabaseUrl: 'https://demo.supabase.co',
        serviceKey: 'server-key',
        page: 2,
        fetcher: async (input, options) => {
            const url = new URL(input);
            if (url.pathname.endsWith('/badge_notice_bridge_secret')) return json('test-only-bridge-secret');
            if (url.hostname === 'staruniv-badges.vercel.app') return new Response(null, { status: 401 });
            assert.equal(url.searchParams.get('page'), '2');
            assert.equal(options.headers.Authorization, undefined);
            assert.equal(options.headers.Cookie, undefined);
            return json(board());
        },
    });
    assert.equal(scan.fallbackMembers, 1);
    assert.equal(scan.results.length, 1);
});
