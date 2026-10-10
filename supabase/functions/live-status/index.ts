// Supabase Edge Function "live-status": 방송 중 표시(live_broadcasts)와 멤버 공지 모음(member_posts)을 채운다.
// pg_cron이 2분마다 부른다(ststat.sql 12번). 공식 목록이 첫 쪽부터 안 되면 비공식 목록을 쓰되, 한 수집 안에서는
// 한쪽만 쓴다(쪽 경계가 달라 빠지거나 겹친다).
// 비밀값: SOOP_CLIENT_ID(대시보드 Edge Functions → Secrets, 없으면 비공식 목록만).
// 배포: 대시보드 Edge Functions에 붙여 넣고 "Verify JWT"는 끈다(pg_cron이 키 없이 부른다).
// ?dry=1은 SOOP에 묻지 않고 마지막 수집 상태만 보여 준다(누구나 부를 수 있다).

const PAGE_SIZE = 60;
const MAX_PAGES = 150;
const CONCURRENCY = 6;
const FETCH_TIMEOUT_MS = 8000;
// 못 받은 쪽이 이 비율 이하이면 받은 쪽만으로 표를 바꾼다(방송이 한 번 빠지는 편이 끝난 방송이 남는 것보다 낫다).
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

// 명단 전체를 내려받지 않고 DB가 겹치는 아이디만 준다(egress 절약).
async function matchRoster(ids: string[]): Promise<{ roster: number; ids: Set<string> }> {
  const r = await rpc("live_roster_match", { p_ids: ids });
  return { roster: Number(r?.roster) || 0, ids: new Set<string>(r?.ids || []) };
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

// 공식 목록은 카테고리 이름 없이 번호만 줘서 카테고리 목록 API로 이름을 채운다.
type Source = {
  name: string;
  fetchPage: (page: number) => Promise<any>;
  viewers: (b: any) => unknown;
  categoryName: (b: any) => string | null;
  ready?: Promise<unknown>;   // categoryName을 쓰기 전에 기다릴 것
  categoryCount?: () => number;
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

// "00040001"과 40001처럼 앞의 0 유무가 달라도 같은 번호로 본다.
const cateKey = (no: unknown) => String(no ?? "").trim().replace(/^0+(?=\d)/, "");

// 응답 모양이 문서 예시와 조금 달라도(data로 감싸거나 배열만 오거나) 읽는다. 못 받으면 빈 표(번호만 저장).
async function officialCategoryNames(): Promise<Map<string, string>> {
  const names = new Map<string, string>();
  const data = await getJson(
    `https://openapi.sooplive.com/broad/category/list?client_id=${encodeURIComponent(SOOP_CLIENT_ID)}&locale=ko_KR`,
  );
  const walk = (list: any) => {
    if (!Array.isArray(list)) return;
    for (const c of list) {
      const no = c?.cate_no ?? c?.category_no, name = c?.cate_name ?? c?.category_name;
      if (no != null && name) names.set(cateKey(no), String(name));
      walk(c?.child ?? c?.children);
    }
  };
  walk(data?.broad_category ?? data?.data?.broad_category ?? data?.data ?? data);
  return names;
}

async function officialSource(): Promise<Source> {
  let names = new Map<string, string>();
  const ready = officialCategoryNames().then(m => { names = m; });
  return {
    name: "official",
    ready,
    categoryCount: () => names.size,
    // select_value를 비우면 전체 카테고리
    fetchPage: page => getJson(
      `https://openapi.sooplive.com/broad/list?client_id=${encodeURIComponent(SOOP_CLIENT_ID)}` +
        `&select_key=cate&select_value=&order_type=view_cnt&page_no=${page}`,
    ),
    viewers: b => b.total_view_cnt,
    categoryName: b => names.get(cateKey(b.broad_cate_no)) ?? null,
  };
}

async function scanAllSOOP() {
  const tried: string[] = [];
  const sources = SOOP_CLIENT_ID ? [officialSource, async () => legacySource()] : [async () => legacySource()];
  for (const make of sources) {
    const source = await make();
    const first = await source.fetchPage(1);
    if (first && Array.isArray(first.broad)) return scanWith(source, first, tried);
    tried.push(source.name);
  }
  // 빈 결과로 덮어쓰지 않는다
  throw new Error(`SOOP 방송 목록 첫 쪽을 받지 못했습니다(${tried.join(", ")}).`);
}

async function scanWith(source: Source, first: any, tried: string[]) {
  const live = new Map<string, any>();    // 아이디 → 방송 항목(명단과 겹치는 것만 끝에서 남긴다)
  let requested = 0;
  let failed = 0;

  function processPage(data: any) {
    requested++;
    if (!data || !Array.isArray(data.broad)) { failed++; return; }
    for (const b of data.broad) {
      const uid = b && b.user_id;
      if (uid && !live.has(uid)) live.set(uid, b);
    }
  }

  processPage(first);

  const totalCnt = parseInt(String(first.total_cnt ?? ""), 10);
  // 첫 쪽 건수가 이상하면 빈/일부 결과로 덮지 않고 실패로 기록한다(표는 그대로).
  if (!Number.isFinite(totalCnt) || totalCnt < 0 || totalCnt < first.broad.length ||
      (totalCnt > 0 && first.broad.length === 0)) {
    const err = new Error(`SOOP 방송 목록 첫 쪽이 이상합니다(total_cnt=${first.total_cnt}, broad=${first.broad.length}).`);
    (err as any).info = { source: source.name, total_cnt: first.total_cnt ?? null };
    throw err;
  }
  const pageSize = parseInt(String(first.page_block || ""), 10) || PAGE_SIZE;
  const totalPages = Math.min(Math.ceil(totalCnt / pageSize), MAX_PAGES);
  let page = 2;
  while (page <= totalPages) {
    const batch: number[] = [];
    for (let i = 0; i < CONCURRENCY && page <= totalPages; i++, page++) batch.push(page);
    (await Promise.all(batch.map(source.fetchPage))).forEach(processPage);
  }

  const info: Record<string, unknown> = {
    source: source.name, total_cnt: totalCnt, total_pages: totalPages, requested, failed,
  };
  if (tried.length) info.fallback_from = tried;
  if (failed / requested > MAX_FAILED_RATIO) {
    const err = new Error(`SOOP 방송 목록 ${requested}쪽 중 ${failed}쪽을 받지 못했습니다.`);
    (err as any).info = info;
    throw err;
  }
  const match = await matchRoster([...live.keys()]);
  if (match.roster === 0) throw new Error("선수 목록이 비어 있습니다.");
  info.roster = match.roster;
  info.found = match.ids.size;
  await source.ready;
  const rows = [...live.entries()].filter(([uid]) => match.ids.has(uid)).map(([uid, b]) => {
    const viewers = parseInt(String(source.viewers(b) ?? ""), 10);
    return {
      soop_id: uid,
      broad_no: b.broad_no != null ? String(b.broad_no) : null,
      broad_title: b.broad_title ?? null,
      current_sum_viewer: Number.isFinite(viewers) ? viewers : null,
      broad_start: b.broad_start ?? null,
      category_name: source.categoryName(b),
      // 이름이 빌 때 사이트가 번호로 스타 여부를 판단하므로 8자리("00040001")로 맞춘다
      broad_cate_no: b.broad_cate_no != null ? String(b.broad_cate_no).trim().replace(/^\d{1,7}$/, n => n.padStart(8, "0")) : null,
    };
  });
  // 카테고리 이름이 잘 붙는지 dry=1로 확인하는 용도
  const unnamed = rows.filter(r => !r.category_name);
  if (source.categoryCount) info.categories = source.categoryCount();
  info.unnamed = unnamed.length;
  if (unnamed.length) info.unnamed_cate_nos = [...new Set(unnamed.map(r => r.broad_cate_no))].slice(0, 5);
  return { rows, info };
}

// ---------------------------------------------------------------------------
// 멤버 공지 모음
// ---------------------------------------------------------------------------
// 2분 주기가 몇 초씩 어긋나도 건너뛰지 않게 90초로 잡는다.
const POSTS_EVERY_MS = 90 * 1000;
const POSTS_PER_PAGE = 10;          // 사이트 fetchMemberFeed와 같은 쪽 크기
const POSTS_CONCURRENCY = 6;

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

// 글 행은 바뀐 것만 고치므로 모은 시각은 수집 상태 행에서 읽는다.
async function lastPostsScanMs(): Promise<number> {
  const res = await fetch(
    `${SUPABASE_URL}/rest/v1/live_scan_state?select=posts_scanned_at&id=eq.1`,
    { headers: DB_HEADERS },
  );
  if (!res.ok) throw new Error("공지 수집 시각 읽기 실패: " + res.status);
  const rows = await res.json();
  return rows[0]?.posts_scanned_at ? Date.parse(rows[0].posts_scanned_at) : 0;
}

// 사이트(soop.js mergeOwnPosts)와 같은 규칙: 일반글+공지 중 본인 글만, titleNo로 중복 제거.
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
  const results: ({ id: string; posts: any[]; totalPages: number } | null)[] = new Array(ids.length).fill(null);
  let nextIdx = 0;
  await Promise.all(Array.from({ length: Math.min(POSTS_CONCURRENCY, ids.length) }, async () => {
    while (nextIdx < ids.length) {
      const i = nextIdx++;
      results[i] = await fetchBoard(ids[i]).then(r => ({ id: ids[i], ...r }), () => null);
    }
  }));
  const ok = results.filter(Boolean) as { id: string; posts: any[]; totalPages: number }[];
  // 절반 넘게 못 받으면 SOOP 쪽 문제로 보고 바꾸지 않는다
  if (ok.length < ids.length / 2) throw new Error(`게시판 ${ids.length}명 중 ${ids.length - ok.length}명을 받지 못했습니다.`);
  const rows = ok.flatMap(r => r.posts.map(post => ({
    soop_id: r.id, title_no: post.titleNo, reg_date: post.regDate, total_pages: r.totalPages, post,
  })));
  const saved = await rpc("replace_member_posts", { p_rows: rows, p_scanned: ok.map(r => r.id), p_active: ids });
  return { members: ids.length, fetched: ok.length, changed: saved };
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
    const scan = await scanAllSOOP();
    info = { ...scan.info, ms: Date.now() - t0 };
    const saved = await rpc("replace_live_broadcasts", { p_rows: scan.rows, p_info: info });
    body = { success: true, live_count: saved, ...info };
  } catch (e) {
    info = { ...info, ...((e as any).info || {}), ms: Date.now() - t0 };
    const msg = e instanceof Error ? e.message : String(e);
    try { await rpc("fail_live_scan", { p_error: msg, p_info: info }); } catch (_e) { /* 기록 실패는 무시 */ }
    body = { success: false, error: msg, ...info };
    status = 500;
  }
  // 방송 중 수집이 실패해도 공지는 모은다. ?posts=1이면 간격을 무시한다.
  try {
    body.posts = await refreshMemberPosts(new URL(req.url).searchParams.get("posts") === "1");
  } catch (e) {
    body.posts = { error: e instanceof Error ? e.message : String(e) };
  }
  return json(body, status);
});
