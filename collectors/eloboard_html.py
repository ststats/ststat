"""EloBoard 전적을 공개 페이지(HTML)에서 읽는다.

2026-11-06 EloBoard가 /api/ 자동 수집을 막아(robots.txt·nginx 403) API 대신 robots.txt가 허용한 페이지를 읽는다.
페이지는 화면을 그리려고 Next.js 원본 데이터(self.__next_f)를 함께 싣는데, 거기에 경기 번호·선수 번호·맵이 있어
API와 같은 경기(세트) 단위 행을 만든다. 요청 사이는 collectors.eloboard와 같이 최소 2초.

- 스폰: /matches?p=N (경기 묶음마다 세트별 경기 번호)
- 중장전·끝장전·멸망전: /matches?kind=...&p=N (같은 모양)
- 팀리그·프로리그: /matches?kind=team → 그날 상세 /events/{id}?tab=results&day=...
- 대학대전·미니대전: /matches?kind=war → 그날 상세 /colleges/wars/days/{id}
- 그 밖에 기간 안에 경기가 있는 대회(KCM·ASL·컵 등): /events/{id}?tab=results

만든 행은 API 행과 같은 모양({id, played_on, map_id, map_name, category, participants})이라 EloMatch.parse를 그대로 쓴다.
대회 종류(category)는 대회 목록(/events)의 category를 쓴다(API와 같은 이름). 스폰 탭 묶음은 종족이 없어 race는 비운다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from collectors.eloboard import DEFAULT_DELAY, MAX_RETRY, USER_AGENT

BASE = "https://eloboard.com"
SPONSORED = "sponsored"
# 그 탭에서 다 보이는 대회 묶음(events.group). 나머지 묶음은 대회 페이지에서 따로 읽는다.
TAB_KINDS = ("jungjang", "kkeutjang", "myeolmang")
TAB_GROUPS = {"team", "pro_league", *TAB_KINDS}
MAX_LIST_PAGES = 400  # 목록 한 탭에서 넘길 최대 쪽 수(30일치 스폰이 약 180쪽)

_PUSH_RE = re.compile(r"self\.__next_f\.push\(\[1,\"(.*?)\"\]\)", re.S)
_last_request = 0.0


class EloBoardBlocked(RuntimeError):
    """EloBoard가 요청을 거절했다(403 등). 다시 시도하지 않는다."""


def get_html(path: str, *, delay: float = DEFAULT_DELAY) -> str:
    """robots.txt Crawl-delay대로 앞 요청과 delay초 이상 띄워 페이지를 받는다."""
    global _last_request
    request = Request(f"{BASE}{path}", headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    last_error: Exception | None = None
    for attempt in range(MAX_RETRY):
        wait = _last_request + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise EloBoardBlocked(f"EloBoard HTTP {exc.code} at {path}") from exc
            last_error = exc
        except (URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(delay * (2**attempt))
    raise RuntimeError(f"EloBoard fetch failed after {MAX_RETRY} attempts at {path}: {last_error}")


_ROW_RE = re.compile(r"(?:^|\n)([0-9a-f]+):", re.M)
_REF_RE = re.compile(r'(?<!"\$",)"\$L([0-9a-f]+)"')


def page_data(html: str) -> str:
    """페이지에 실린 Next.js 원본 데이터(문자열 조각을 이은 것).

    뒤에 따로 실리는 조각("$L1c" → 1c:[...] 줄)은 제자리에 끼워 넣는다(경기 한쪽 선수가 이렇게 오기도 한다)."""
    data = "".join(json.loads(f'"{chunk}"') for chunk in _PUSH_RE.findall(html))
    marks = list(_ROW_RE.finditer(data))
    rows = {
        m.group(1): data[m.end() : marks[k + 1].start() if k + 1 < len(marks) else len(data)].strip()
        for k, m in enumerate(marks)
    }
    for _ in range(4):
        data, n = _REF_RE.subn(lambda m: rows.get(m.group(1), m.group(0)) or m.group(0), data)
        if not n:
            break
    return data


def json_arrays(data: str, key: str) -> list[list]:
    """data 안의 "key":[...] 배열을 모두 꺼낸다."""
    decoder = json.JSONDecoder()
    out, start = [], 0
    marker = f'"{key}":['
    while (i := data.find(marker, start)) >= 0:
        value, end = decoder.raw_decode(data, i + len(marker) - 1)
        out.append(value)
        start = end
    return out


def duels_of(data: str) -> list[dict]:
    """등록된 전적 묶음(승인 대기 묶음은 경기 번호가 없어 빠진다)."""
    return [d for arr in json_arrays(data, "duels") for d in arr if isinstance(d, dict)]


def _row(match_id, day, map_id, map_name, category, winner, loser) -> dict:
    return {
        "id": int(match_id),
        "played_on": day,
        "map_id": map_id,
        "map_name": map_name,
        "category": category,
        "participants": [
            {"player_id": winner[0], "name": winner[1], "race": winner[2], "result": "win"},
            {"player_id": loser[0], "name": loser[1], "race": loser[2], "result": "loss"},
        ],
    }


def duel_rows(duel: dict, category: str) -> list[dict]:
    """경기 묶음 하나의 세트별 행. 경기 번호가 없는 세트(승인 대기)는 뺀다."""
    rows = []
    for s in duel.get("sets") or []:
        match_id = s.get("match_id")
        if not isinstance(match_id, int):
            continue
        rows.append(
            _row(
                match_id,
                duel.get("day"),
                s.get("map_id"),
                s.get("map_name"),
                category,
                (s.get("winner_id"), s.get("winner"), None),
                (s.get("loser_id"), s.get("loser"), None),
            )
        )
    return rows


# 그날 상세 페이지의 경기 하나: 경기 번호가 React 키로 붙고, 맵·양쪽 선수(승/패 표시·종족)가 뒤따른다.
_GAME_RE = re.compile(r'"\$L[0-9a-z]+","(\d{6,})",\{"panelClass":"[^"]*gpanel"')
_MAP_ID_RE = re.compile(r"maps/(\d+)_")
_MAP_NAME_RE = re.compile(r'gmapName","children":"([^"]*)"')
_SIDE_RE = re.compile(r'"className":"[^"]*__gname[^"]*__g(win|lose)"')
_PLAYER_RE = re.compile(r'"href":"/players/(\d+)".*?"race ([A-Z]+)".*?__nm","children":"([^"]*)"')


def day_rows(data: str, day: str, category: str) -> tuple[list[dict], int]:
    """(행, 건너뛴 경기 수). 한쪽 선수가 둘 이상인 경기(2:2)는 API도 받지 않던 모양이라 건너뛴다."""
    # 끼워 넣은 조각과 원래 조각이 둘 다 남아 같은 경기가 여러 번 보인다. 경기 번호마다 온전히 읽힌 첫 번째를 쓴다
    found: dict[int, dict] = {}
    seen: set[int] = set()
    starts = [(m.start(), int(m.group(1))) for m in _GAME_RE.finditer(data)]
    for k, (pos, match_id) in enumerate(starts):
        seen.add(match_id)
        if match_id in found:
            continue
        seg = data[pos : starts[k + 1][0] if k + 1 < len(starts) else len(data)]
        map_id, map_name = _MAP_ID_RE.search(seg), _MAP_NAME_RE.search(seg)
        sides = list(_SIDE_RE.finditer(seg))
        if len(sides) != 2 or {s.group(1) for s in sides} != {"win", "lose"}:
            continue
        players = []
        for j, side in enumerate(sides):
            end = sides[j + 1].start() if j + 1 < len(sides) else len(seg)
            players.append([(int(p), name, race) for p, race, name in _PLAYER_RE.findall(seg[side.end() : end])])
        if any(len(p) != 1 for p in players):
            continue
        by_result = {side.group(1): players[j][0] for j, side in enumerate(sides)}
        found[match_id] = _row(
            match_id,
            day,
            int(map_id.group(1)) if map_id else None,
            map_name.group(1) if map_name else None,
            category,
            by_result["win"],
            by_result["lose"],
        )
    return list(found.values()), len(seen - found.keys())


_BOARD_RE = re.compile(
    r'"\$L[0-9a-z]+","(\d+)",\{"className":"[^"]*__brow","href":"(/events/\d+\?tab=results&day=\d+)"'
)
_BOARD_DATE_RE = re.compile(r'__bdate","children":\["(\d{4})\.(\d\d)\.(\d\d)"')


def board_days(data: str) -> list[tuple[str, str]]:
    """대회 결과 표의 (날짜, 그날 상세 주소). 표는 최근 날짜부터다."""
    out = []
    marks = list(_BOARD_RE.finditer(data))
    for k, m in enumerate(marks):
        seg = data[m.end() : marks[k + 1].start() if k + 1 < len(marks) else len(data)]
        d = _BOARD_DATE_RE.search(seg)
        if d:
            out.append((f"{d.group(1)}-{d.group(2)}-{d.group(3)}", m.group(2)))
    return out


@dataclass
class Collected:
    rows: dict[int, dict] = field(default_factory=dict)
    skipped: int = 0  # 2:2 등 받지 않는 모양
    requests: int = 0
    complete: bool = True  # 기간 처음까지 다 읽었는가(아니면 사라진 경기 판정을 하지 않는다)
    notes: list[str] = field(default_factory=list)

    def add(self, rows: list[dict]) -> None:
        for row in rows:
            self.rows[row["id"]] = row


def collect(cutoff: str, *, get=get_html) -> Collected:
    """cutoff(포함) 이후 경기를 모두 읽는다. get은 시험에서 바꿔 끼운다."""
    out = Collected()

    def fetch(path: str) -> str:
        out.requests += 1
        return page_data(get(path))

    events = {e["id"]: e for arr in json_arrays(fetch("/events"), "events") for e in arr if isinstance(e, dict)}
    by_name = {e["name"]: e for e in events.values()}

    def category_of_context(context: str, default: str) -> str:
        name = context.split(" · ", 1)[-1].strip()
        return (by_name.get(name) or {}).get("category") or default

    # 경기 묶음 목록 탭(스폰·중장전·끝장전·멸망전): 날짜순이라 cutoff보다 이른 묶음이 보이면 멈춘다
    for kind in (None, *TAB_KINDS):
        for page in range(1, MAX_LIST_PAGES + 1):
            query = "&".join(x for x in (f"kind={kind}" if kind else "", f"p={page}" if page > 1 else "") if x)
            duels = [
                d
                for d in duels_of(fetch(f"/matches?{query}" if query else "/matches"))
                if str(d.get("key", "")).startswith("m")
            ]
            if not duels:
                break
            for d in duels:
                if str(d.get("day") or "") >= cutoff:
                    out.add(
                        duel_rows(
                            d, SPONSORED if kind is None else category_of_context(d.get("context", ""), "solo_event")
                        )
                    )
            if min(str(d.get("day") or "") for d in duels) < cutoff:
                break
        else:
            out.complete = False
            out.notes.append(f"matches kind={kind}: {MAX_LIST_PAGES}쪽 안에 {cutoff}까지 닿지 못함")

    # 팀리그·프로리그·대학대전: 그날 묶음 목록 → 그날 상세
    for kind in ("team", "war"):
        for page in range(1, MAX_LIST_PAGES + 1):
            duels = duels_of(fetch(f"/matches?kind={kind}" + (f"&p={page}" if page > 1 else "")))
            if not duels:
                break
            for d in duels:
                day, href = str(d.get("day") or ""), str(d.get("href") or "")
                if day < cutoff or not href:
                    continue
                if kind == "war":
                    category = "college_mini" if str(d.get("context", "")).startswith("미니대전") else "college_war"
                else:
                    ev = events.get(int(m.group(1))) if (m := re.match(r"/events/(\d+)", href)) else None
                    if not ev:
                        out.complete = False
                        out.notes.append(f"대회 목록에 없는 대회: {href}")
                        continue
                    category = ev["category"]
                rows, skipped = day_rows(fetch(href), day, category)
                out.add(rows)
                out.skipped += skipped
            if min(str(d.get("day") or "") for d in duels) < cutoff:
                break

    # 탭에 안 나오는 대회(KCM·ASL·컵 등) 중 기간 안에 경기가 있는 것
    for ev in events.values():
        if ev.get("children") or ev.get("group") in TAB_GROUPS or str(ev.get("last_on") or "") < cutoff:
            continue
        data = fetch(f"/events/{ev['id']}?tab=results")
        duels = [d for d in duels_of(data) if str(d.get("key", "")).startswith("m")]
        for d in duels:
            if str(d.get("day") or "") >= cutoff:
                out.add(duel_rows(d, ev["category"]))
        for day, href in board_days(data):
            if day >= cutoff:
                rows, skipped = day_rows(fetch(href), day, ev["category"])
                out.add(rows)
                out.skipped += skipped
        listed = [str(d.get("day") or "") for d in duels] + [day for day, _ in board_days(data)]
        if listed and min(listed) >= cutoff and len(listed) >= 30:
            out.complete = False
            out.notes.append(f"대회 {ev['id']} {ev['name']}: 첫 쪽이 {cutoff}까지 닿지 않음")
    return out


if __name__ == "__main__":  # 시험용: DB에 쓰지 않고 기간 경기를 모아 개수만 본다
    import argparse
    import collections
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="cutoff", required=True)
    ap.add_argument("--to", dest="until")
    ap.add_argument("--out")
    args = ap.parse_args()
    got = collect(args.cutoff)
    rows = [r for r in got.rows.values() if not args.until or r["played_on"] <= args.until]
    print(f"요청 {got.requests}번, 경기 {len(rows)}, 건너뜀 {got.skipped}, 완전 {got.complete}", file=sys.stderr)
    for note in got.notes:
        print("  ", note, file=sys.stderr)
    for cat, n in collections.Counter(r["category"] for r in rows).most_common():
        print(f"{cat}\t{n}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
