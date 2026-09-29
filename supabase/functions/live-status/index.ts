// 방송 중 표시(라이브) 수집 - Supabase Edge Function "live-status"
//
// SOOP 전체 방송 목록(시청자 많은 순)을 끝까지 훑어 tier_members에 있는 SOOP 아이디 중 방송 중인 사람만
// public.live_broadcasts에 통째로 바꿔 넣는다.
// 목록은 SOOP 공식 Open API(openapi.sooplive.com/broad/list, client_id만 필요한 Public API)로 받고,
// 공식 API가 첫 쪽부터 안 되면 예전 비공식 목록(live.sooplive.com)으로 한 번 더 해 본다. 한 번의 수집 안에서는
// 한쪽만 쓴다(두 목록을 섞으면 쪽 경계가 달라 빠지거나 겹칠 수 있다).
// 필요한 비밀값: SOOP_CLIENT_ID(SOOP Developers > My Account에서 만든 앱의 client_id, 대시보드 Edge Functions → Secrets).
//   없으면 예전 비공식 목록만 쓴다.
// pg_cron이 2분마다 부른다(supabase/ststat.sql 12번 맨 아래).
//
// 배포: Supabase 대시보드 → Edge Functions → 새 함수 "live-status"에 이 파일을 붙여 넣고,
//       "Verify JWT"는 끈다(pg_cron이 키 없이 부른다). 쓰기는 함수 안에서만 service role로 한다.
// 확인: 브라우저로 .../functions/v1/live-status?dry=1 을 열면 수집하지 않고 마지막 수집의 시각·쪽수·실패 수·찾은 수만
//       보여 준다(누구나 부를 수 있으므로 SOOP에 요청하지 않고, 정기 수집의 50초 칸도 잡지 않는다).
// 같은 시각에 여러 번 불러도 50초에 한 번만 실제로 수집한다(try_begin_live_scan).
//
// 멤버 공지 모음(member_posts, ststat.sql 13번)도 여기서 채운다: 방송 중 수집 뒤(2분마다) 매번
// 활동 중인 캄몬 멤버(members.left_date 없음)의 SOOP 게시판 첫 페이지(본인 글만)를 받아 둔다. 스타유니브 홈·멤버 공지는
// 방문자마다 멤버 수만큼 SOOP API를 부르지 않고 이 표를 한 번 조회한다. 방송 중 수집이 실패해도 공지는 따로 모은다.

const PAGE_SIZE = 60;
const MAX_PAGES = 150;
const CONCURRENCY = 6;
const FETCH_TIMEOUT_MS = 8000;   // SOOP 한 쪽 요청 최대 대기
// 못 받은 쪽이 이보다 많으면 결과를 저장하지 않는다. 이하이면 받은 쪽만으로 표를 바꾼다 - 못 받은 쪽의
// 방송이 한 번(2분) 빠질 수 있지만, 끝난 방송이 남는 것보다 낫다(운영 결정 2026-09-26).
const MAX_FAILED_RATIO = 0.2;

const SOOP_CLIENT_ID = (Deno.env.get("SOOP_CLIENT_ID") || "").trim();
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const DB_HEADERS = {
  apikey: SERVICE_KEY,
  Authorization: `Bearer ${SERVICE_KEY}`,
  "Content-Type": "application/json",
};

async function rpc(name: string, args: Record<string, unknown> = {}) {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: DB_HEADERS,
    body: JSON.stringify(args),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${name} 실패: ${res.status} ${text}`);
  // 반환값 없는 함수(fail_live_scan)는 본문 없이 204로 온다
  return text ? JSON.parse(text) : null;
}

async function loadRosterIds(): Promise<Set<string>> {
  const ids = new Set<string>();
  for (let from = 0; from < 20000; from += 1000) {
    const res = await fetch(
      `${SUPABASE_URL}/rest/v1/tier_members?select=soop_id&soop_id=not.is.null` +
        `&order=source_order.asc&offset=${from}&limit=1000`,
      { headers: DB_HEADERS },
    );
    if (!res.ok) throw new Error("선수 목록 로드 실패: " + res.status);
    const rows = await res.json();
    for (const r of rows) {
      const id = String(r.soop_id || "").trim();
      if (id) ids.add(id);
    }
    if (rows.length < 1000) break;
  }
  return ids;
}

async function getJson(url: string) {
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0", "Accept": "application/json" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return null;
  }
}

// 방송 목록 한 쪽을 받는 방법. 두 목록 모두 한 쪽 60개, 같은 방송 필드(user_id·broad_no·broad_cate_no 등)를 준다.
// 공식 목록은 시청자 수가 total_view_cnt이고 카테고리 이름이 없어(번호만) 카테고리 목록 API로 이름을 채운다.
type Source = {
  name: string;
  fetchPage: (page: number) => Promise<any>;
  viewers: (b: any) => unknown;
  categoryName: (b: any) => string | null;
  ready?: Promise<unknown>;   // categoryName을 쓰기 전에 기다릴 것(카테고리 목록)
};

function legacySource(): Source {
  return {
    name: "legacy",
    fetchPage: page => getJson(
      `https://live.sooplive.com/api/main_broad_list_api.php` +
        `?selectType=action&selectValue=all&orderType=view_cnt` +
        `&pageNo=${page}&strmLangType=&lang=ko_KR`,
    ),
    viewers: b => b.current_view_cnt,
    categoryName: b => b.category_name ?? null,
  };
}

// 공식 카테고리 번호 → 이름("00040001" → "스타크래프트"). 하위 카테고리까지 펼친다. 못 받으면 빈 표(번호만 저장).
async function officialCategoryNames(): Promise<Map<string, string>> {
  const names = new Map<string, string>();
  const data = await getJson(
    `https://openapi.sooplive.com/broad/category/list?client_id=${encodeURIComponent(SOOP_CLIENT_ID)}&locale=ko_KR`,
  );
  const walk = (list: any[]) => {
    for (const c of list || []) {
      if (c?.cate_no && c?.cate_name) names.set(String(c.cate_no), String(c.cate_name));
      walk(c?.child);
    }
  };
  walk(data?.broad_category);
  return names;
}

async function officialSource(): Promise<Source> {
  // 카테고리 이름은 방송 목록과 동시에 받는다(저장 직전에만 필요)
  let names = new Map<string, string>();
  const ready = officialCategoryNames().then(m => { names = m; });
  return {
    name: "official",
    ready,
    // select_value를 비우면 전체 카테고리, 시청자 많은 순
    fetchPage: page => getJson(
      `https://openapi.sooplive.com/broad/list?client_id=${encodeURIComponent(SOOP_CLIENT_ID)}` +
        `&select_key=cate&select_value=&order_type=view_cnt&page_no=${page}`,
    ),
    viewers: b => b.total_view_cnt,
    categoryName: b => names.get(String(b.broad_cate_no ?? "")) ?? null,
  };
}

// 공식 목록을 먼저, 안 되면 예전 목록으로. 첫 쪽이 방송 목록 모양이 아니면(오류 응답 등) 실패로 본다.
async function scanAllSOOP(wantedIds: Set<string>) {
  const tried: string[] = [];
  const sources = SOOP_CLIENT_ID ? [officialSource, async () => legacySource()] : [async () => legacySource()];
  for (const make of sources) {
    const source = await make();
    const first = await source.fetchPage(1);
    if (first && Array.isArray(first.broad)) return scanWith(source, first, wantedIds, tried);
    tried.push(source.name);
  }
  // 첫 쪽부터 못 받으면 SOOP 쪽 문제 - 빈 결과로 덮어쓰지 않는다
  throw new Error(`SOOP 방송 목록 첫 쪽을 받지 못했습니다(${tried.join(", ")}).`);
}

async function scanWith(source: Source, first: any, wantedIds: Set<string>, tried: string[]) {
  const found = new Map<string, any>();   // 아이디 → 목록의 방송 항목(저장할 모양은 끝에서 만든다)
  let requested = 0;
  let failed = 0;

  function processPage(data: any) {
    requested++;
    if (!data || !Array.isArray(data.broad)) { failed++; return; }
    for (const b of data.broad) {
      const uid = b.user_id;
      if (uid && wantedIds.has(uid) && !found.has(uid)) found.set(uid, b);
    }
  }

  processPage(first);

  const totalCnt = parseInt(String(first.total_cnt || "0"), 10);
  const pageSize = parseInt(String(first.page_block || ""), 10) || PAGE_SIZE;
  const totalPages = Math.min(Math.ceil(totalCnt / pageSize), MAX_PAGES);
  let page = 2;
  while (page <= totalPages && found.size < wantedIds.size) {
    const batch: number[] = [];
    for (let i = 0; i < CONCURRENCY && page <= totalPages; i++, page++) batch.push(page);
    (await Promise.all(batch.map(source.fetchPage))).forEach(processPage);
  }

  const info: Record<string, unknown> = {
    source: source.name, total_cnt: totalCnt, total_pages: totalPages, requested, failed, found: found.size,
  };
  if (tried.length) info.fallback_from = tried;
  // 못 받은 쪽이 많으면 방송 중인 사람이 빠진 결과일 수 있다 - 저장하지 않는다
  if (failed / requested > MAX_FAILED_RATIO) {
    const err = new Error(`SOOP 방송 목록 ${requested}쪽 중 ${failed}쪽을 받지 못했습니다.`);
    (err as any).info = info;
    throw err;
  }
  await source.ready;
  const rows = [...found.entries()].map(([uid, b]) => {
    const viewers = parseInt(String(source.viewers(b) ?? ""), 10);
    return {
      soop_id: uid,
      broad_no: b.broad_no != null ? String(b.broad_no) : null,
      broad_title: b.broad_title ?? null,
      current_sum_viewer: Number.isFinite(viewers) ? viewers : null,
      broad_start: b.broad_start ?? null,
      category_name: source.categoryName(b),                                 // "스타크래프트"
      broad_cate_no: b.broad_cate_no != null ? String(b.broad_cate_no) : null, // "00040001" (이름이 빌 때 대비)
    };
  });
  return { rows, info };
}

// ---------------------------------------------------------------------------
// 멤버 공지 모음
// ---------------------------------------------------------------------------
// 2분마다 부르는 방송 중 수집과 같은 주기로 모은다(새 공지가 늦어도 2~4분 안에 뜨게). 실행 시각이 몇 초씩
// 어긋나도 한 번씩 건너뛰지 않게 90초로 잡는다(같은 시각 중복 실행은 try_begin_live_scan이 이미 막는다).
const POSTS_EVERY_MS = 90 * 1000;
const POSTS_PER_PAGE = 10;          // 사이트 fetchMemberFeed와 같은 쪽 크기(2쪽부터는 사이트가 SOOP에 직접 묻는다)

async function loadActiveMemberIds(): Promise<string[]> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/members?select=soop_id&left_date=is.null&soop_id=not.is.null&limit=1000`,
    { headers: DB_HEADERS },
  );
  if (!res.ok) throw new Error("멤버 목록 로드 실패: " + res.status);
  const ids = new Set<string>();
  for (const r of await res.json()) {
    const id = String(r.soop_id || "").trim();
    if (id) ids.add(id);
  }
  return [...ids];
}

async function lastPostsScanMs(): Promise<number> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/member_posts?select=scanned_at&order=scanned_at.desc&limit=1`,
    { headers: DB_HEADERS },
  );
  if (!res.ok) throw new Error("공지 수집 시각 읽기 실패: " + res.status);
  const rows = await res.json();
  return rows[0]?.scanned_at ? Date.parse(rows[0].scanned_at) : 0;
}

// 사이트(soop.js mergeOwnPosts)와 같은 규칙: 일반글+공지 중 본인 글만, titleNo로 중복 제거.
// 저장은 화면이 쓰는 필드만.
function ownPosts(data: any, soopId: string) {
  const owner = soopId.toLowerCase();
  const all = [...(data?.contents || []), ...(data?.noticeData || [])]
    .filter((p: any) => p && String(p.userId || "").toLowerCase() === owner);
  const seen = new Set();
  return all.filter((p: any) => (seen.has(p.titleNo) ? false : (seen.add(p.titleNo), true))).map((p: any) => ({
    titleNo: p.titleNo,
    titleName: p.titleName ?? null,
    regDate: p.regDate ?? null,
    userId: p.userId ?? null,
    userNick: p.userNick ?? null,
    content: p.content ? { textContent: p.content.textContent ?? "", content: p.content.content ?? "" } : null,
    display: p.display ? { bbsName: p.display.bbsName ?? "" } : null,
    count: p.count ? { likeCnt: p.count.likeCnt ?? 0, readCnt: p.count.readCnt ?? 0 } : null,
    photos: Array.isArray(p.photos) ? p.photos.map((x: any) => ({ url: x?.url ?? "" })).filter((x: any) => x.url) : [],
  }));
}

async function fetchBoard(soopId: string) {
  const url = `https://api-channel.sooplive.com/v1.1/channel/${encodeURIComponent(soopId)}/board` +
    `?perPage=${POSTS_PER_PAGE}&page=1`;
  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0", "Accept": "application/json" },
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`${soopId} 게시판 ${res.status}`);
  const data = await res.json();
  return { posts: ownPosts(data, soopId), totalPages: Number(data?.meta?.totalPages) || 1 };
}

async function refreshMemberPosts(force = false) {
  if (!force && Date.now() - (await lastPostsScanMs()) < POSTS_EVERY_MS) return { skipped: true };
  const ids = await loadActiveMemberIds();
  if (ids.length === 0) throw new Error("활동 멤버가 없습니다.");
  const results = await Promise.all(ids.map(id => fetchBoard(id).then(r => ({ id, ...r }), () => null)));
  const ok = results.filter(Boolean) as { id: string; posts: any[]; totalPages: number }[];
  // 절반 넘게 못 받으면 SOOP 쪽 문제로 보고 이번엔 바꾸지 않는다(지금 있는 공지를 지키기)
  if (ok.length < ids.length / 2) throw new Error(`게시판 ${ids.length}명 중 ${ids.length - ok.length}명을 받지 못했습니다.`);
  const rows = ok.flatMap(r => r.posts.map(post => ({
    soop_id: r.id, title_no: post.titleNo, reg_date: post.regDate, total_pages: r.totalPages, post,
  })));
  const saved = await rpc("replace_member_posts", { p_rows: rows, p_scanned: ok.map(r => r.id), p_active: ids });
  return { members: ids.length, fetched: ok.length, posts: saved };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

async function lastScanState() {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/live_scan_state?select=started_at,finished_at,last_ok_at,last_error,info&id=eq.1`,
    { headers: DB_HEADERS },
  );
  if (!res.ok) throw new Error("수집 상태 읽기 실패: " + res.status);
  const rows = await res.json();
  return rows[0] ?? null;
}

Deno.serve(async (req) => {
  if (new URL(req.url).searchParams.get("dry") === "1") {
    try {
      return json({ dry: true, state: await lastScanState() });
    } catch (e) {
      return json({ dry: true, error: e instanceof Error ? e.message : String(e) }, 500);
    }
  }
  const t0 = Date.now();
  if (!(await rpc("try_begin_live_scan"))) {
    return json({ skipped: true, reason: "50초 안에 이미 수집이 시작됨" });
  }
  let info: Record<string, unknown> = {};
  let body: Record<string, unknown>;
  let status = 200;
  try {
    const roster = await loadRosterIds();
    if (roster.size === 0) throw new Error("선수 목록이 비어 있습니다.");
    const scan = await scanAllSOOP(roster);
    info = { ...scan.info, roster: roster.size, ms: Date.now() - t0 };
    const saved = await rpc("replace_live_broadcasts", { p_rows: scan.rows, p_info: info });
    body = { success: true, live_count: saved, ...info };
  } catch (e) {
    info = { ...info, ...((e as any).info || {}), ms: Date.now() - t0 };
    const msg = e instanceof Error ? e.message : String(e);
    try { await rpc("fail_live_scan", { p_error: msg, p_info: info }); } catch (_e) { /* 기록 실패는 무시 */ }
    body = { success: false, error: msg, ...info };
    status = 500;
  }
  // 공지 모음은 방송 중 수집과 따로(한쪽이 실패해도 다른 쪽은 한다). ?posts=1이면 간격을 무시하고 바로 모은다.
  try {
    body.posts = await refreshMemberPosts(new URL(req.url).searchParams.get("posts") === "1");
  } catch (e) {
    body.posts = { error: e instanceof Error ? e.message : String(e) };
  }
  return json(body, status);
});
