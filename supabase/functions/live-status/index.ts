// 방송 중 표시(라이브) 수집 - Supabase Edge Function "live-status"
//
// SOOP 전체 방송 목록(시청자 많은 순)을 끝까지 훑어 tier_members에 있는 SOOP 아이디 중 방송 중인 사람만
// public.live_broadcasts에 통째로 바꿔 넣는다. 예전 Cloudflare Worker + KV를 대신한다.
// pg_cron이 2분마다 부른다(supabase/ststat.sql 12번 맨 아래).
//
// 배포: Supabase 대시보드 → Edge Functions → 새 함수 "live-status"에 이 파일을 붙여 넣고,
//       "Verify JWT"는 끈다(pg_cron이 키 없이 부른다). 쓰기는 함수 안에서만 service role로 한다.
// 확인: 브라우저로 .../functions/v1/live-status?dry=1 을 열면 수집하지 않고 마지막 수집의 시각·쪽수·실패 수·찾은 수만
//       보여 준다(누구나 부를 수 있으므로 SOOP에 요청하지 않고, 정기 수집의 50초 칸도 잡지 않는다).
// 같은 시각에 여러 번 불러도 50초에 한 번만 실제로 수집한다(try_begin_live_scan).

const PAGE_SIZE = 60;
const MAX_PAGES = 150;
const CONCURRENCY = 6;
const FETCH_TIMEOUT_MS = 8000;   // SOOP 한 쪽 요청 최대 대기
// 못 받은 쪽이 이보다 많으면 결과를 저장하지 않는다. 이하이면 받은 쪽만으로 표를 바꾼다 - 못 받은 쪽의
// 방송이 한 번(2분) 빠질 수 있지만, 끝난 방송이 남는 것보다 낫다(운영 결정 2026-09-26).
const MAX_FAILED_RATIO = 0.2;

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

async function fetchPage(page: number) {
  const apiUrl =
    `https://live.sooplive.com/api/main_broad_list_api.php` +
    `?selectType=action&selectValue=all&orderType=view_cnt` +
    `&pageNo=${page}&strmLangType=&lang=ko_KR`;
  try {
    const res = await fetch(apiUrl, {
      headers: { "User-Agent": "Mozilla/5.0" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return null;
  }
}

async function scanAllSOOP(wantedIds: Set<string>) {
  const found = new Map<string, Record<string, unknown>>();
  let requested = 0;
  let failed = 0;

  function processPage(data: any) {
    requested++;
    if (!data) { failed++; return; }
    for (const b of data.broad || []) {
      const uid = b.user_id;
      if (uid && wantedIds.has(uid) && !found.has(uid)) {
        const viewers = parseInt(b.current_view_cnt, 10);
        found.set(uid, {
          soop_id: uid,
          broad_no: b.broad_no != null ? String(b.broad_no) : null,
          broad_title: b.broad_title ?? null,
          current_sum_viewer: Number.isFinite(viewers) ? viewers : null,
          broad_start: b.broad_start ?? null,
          category_name: b.category_name ?? null,   // "스타크래프트"
          broad_cate_no: b.broad_cate_no ?? null,   // "00040001" (이름이 빌 때 대비)
        });
      }
    }
  }

  const first = await fetchPage(1);
  // 첫 쪽부터 못 받으면 SOOP 쪽 문제 - 빈 결과로 덮어쓰지 않는다
  if (!first) throw new Error("SOOP 방송 목록 첫 쪽을 받지 못했습니다.");
  processPage(first);

  const totalCnt = parseInt(first.total_cnt || "0", 10);
  const totalPages = Math.min(Math.ceil(totalCnt / PAGE_SIZE), MAX_PAGES);
  let page = 2;
  while (page <= totalPages && found.size < wantedIds.size) {
    const batch: number[] = [];
    for (let i = 0; i < CONCURRENCY && page <= totalPages; i++, page++) batch.push(page);
    (await Promise.all(batch.map(fetchPage))).forEach(processPage);
  }

  const info = { total_cnt: totalCnt, total_pages: totalPages, requested, failed, found: found.size };
  // 못 받은 쪽이 많으면 방송 중인 사람이 빠진 결과일 수 있다 - 저장하지 않는다
  if (failed / requested > MAX_FAILED_RATIO) {
    const err = new Error(`SOOP 방송 목록 ${requested}쪽 중 ${failed}쪽을 받지 못했습니다.`);
    (err as any).info = info;
    throw err;
  }
  return { rows: [...found.values()], info };
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
  try {
    const roster = await loadRosterIds();
    if (roster.size === 0) throw new Error("선수 목록이 비어 있습니다.");
    const scan = await scanAllSOOP(roster);
    info = { ...scan.info, roster: roster.size, ms: Date.now() - t0 };
    const saved = await rpc("replace_live_broadcasts", { p_rows: scan.rows, p_info: info });
    return json({ success: true, live_count: saved, ...info });
  } catch (e) {
    info = { ...info, ...((e as any).info || {}), ms: Date.now() - t0 };
    const msg = e instanceof Error ? e.message : String(e);
    try { await rpc("fail_live_scan", { p_error: msg, p_info: info }); } catch (_e) { /* 기록 실패는 무시 */ }
    return json({ success: false, error: msg, ...info }, 500);
  }
});
