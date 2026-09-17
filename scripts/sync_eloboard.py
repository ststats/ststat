"""eloboard 전적을 받아 data/eloboard.json으로 저장한다.

[왜 이 형태인가]
원본 응답은 1건이 733바이트다(필드 21개 + participants 2명 × 6필드). 37.5만 건이면
267MB인데, GitHub의 파일 1개 100MB 하드 리밋을 한참 넘어서 커밋 자체가 거부되고,
브라우저에 올리면 JS 메모리만 1GB에 가까워 휴대폰 탭이 죽는다.
실제로 필요한 건 6개뿐이라 그것만 배열 행으로 남긴다:

    [경기id, 날짜, 승자 player_id, 패자 player_id, map_id, 카테고리코드]

37.5만 건 기준 14.1MB(gzip 4.0MB), 1건당 39바이트. 실측으로 중급 휴대폰(CPU 4배
스로틀)에서 파싱 0.8초 + 특정 선수 전적·상대별·맵별 집계 0.13초, JS 메모리 75MB다.
일일 동기화로 커밋해도 git이 델타 압축을 하므로 저장소는 커밋당 1~2KB만 늘어난다
(실측: 8커밋 후 팩 크기 변화 없음, 1년치로 약 1MB).

  - 승패는 필드로 두지 않는다. 원본의 participants[0]이 항상 승자이므로 순서가
    곧 승패다(관찰된 샘플 전건 일치. 어긋난 건이 오면 아래에서 경고를 띄운다).
  - 맵 이름/이미지, 카테고리 문자열, 선수 이름은 행마다 반복하지 않고 사전으로
    한 번만 담는다(원본에서 이 중복이 19MB를 차지한다).
  - 종족은 선수 사전에 최빈 종족으로 담는다. [주의] 종족은 선수 속성이 아니라
    "그 경기"의 속성이다 - 실제로 같은 날 같은 선수가 Z와 P로 각각 뛴 사례가 있다
    (player_id 723). 그래서 이 사전의 종족은 "주 종족"이고, 종족전 승률을 정확히
    내려면 행에 종족을 같이 담아야 한다(+0.6MB). 지금은 담지 않는다.

[증분]
응답은 경기 id 내림차순(최신 먼저)이다. 그래서 두 번째 이후 실행은 저장해 둔 max_id까지
내려오면 멈춘다 - 하루 20~30건 수준이라 요청 1~2회로 끝난다.
다만 id로만 끊으면 "이미 받은 경기의 결과·맵이 나중에 고쳐진 것"을 영영 못 본다. 그래서
max_id에 닿은 뒤에도 최근 며칠치(--overlap-days, 기본 3일)는 한 번 더 받아 같은 id의 행을
덮어쓴다. 3일치면 대개 같은 페이지 안이라 요청이 늘지 않는다.
삭제도 이 겹쳐 읽기로 따라간다. 이번 실행이 실제로 훑은 id 구간(min_seen ~ ceiling) 안에서
응답에 없던 경기는 원본에서 지워진 것으로 보고 우리 아카이브에서도 지운다. 그 구간 밖(더 옛날)
경기의 삭제는 증분으로는 알 수 없고, --full 로 한 번 돌 때 정리된다.

수집이 중간에 끊긴 실행(--max-pages 한도 등)은 max_id를 올리지 않고 삭제도 하지 않는다 -
훑다 만 구간을 "없어진 경기"로 오해하면 안 되기 때문이다. 다음 실행이 같은 자리에서 이어받는다.
전체 1회 수집은 37.5만 ÷ 190 = 약 1,974요청.
행을 최신 먼저로 정렬해 두는 이유가 하나 더 있다: 새 경기가 파일 앞에 붙는 쪽이
뒤에 붙는 쪽보다 git 팩이 작다(실측 4.0MB 대 5.0MB).

[예의]
robots.txt가 /api/를 Disallow로 두고 Crawl-delay: 2를 걸어놨다. 기본값으로 그
2초를 지킨다 - 전체 1회에 약 66분이 걸린다(1,974요청 × 2초). --delay 0.5면 16분.
1,974회는 남의 서버에 가볍지 않은 양이라, 전체 수집 전에 운영자에게 허락을 받거나
덤프를 요청하는 편이 낫다. User-Agent는 누가 긁는지 알 수 있게 밝힌다.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict

API_URL = 'https://eloboard.co.kr/api/matches'
OUT_PATH = os.path.join('data', 'eloboard.json')

PAGE_LIMIT = 200
# 페이지를 조금씩 겹쳐 받는다. offset이 0부터인지 1부터인지 문서가 없어서(사용자가
# 쓰던 주소는 offset=1이었다) 한 칸 어긋나면 경기가 빠질 수 있는데, 겹쳐 받고 id로
# 중복을 걸러내면 그 위험이 사라진다. 750요청이 790요청이 되는 정도의 비용이다.
PAGE_STEP = PAGE_LIMIT - 10
START_OFFSET = 0

DEFAULT_DELAY = 2.0          # robots.txt의 Crawl-delay
USER_AGENT = os.environ.get(
    'ELOBOARD_UA',
    'staruniv-sync/1.0 (+https://ststats.github.io/staruniv; 캄몬스타즈 전적 집계용)')

MAX_RETRY = 4


def fetch_page(offset, limit, delay):
    """한 페이지를 받아 리스트로 돌려준다. 일시적 오류는 지수 백오프로 재시도한다."""
    url = f'{API_URL}?limit={limit}&offset={offset}'
    req = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/json',
    })
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.loads(res.read().decode('utf-8'))
            if not isinstance(data, list):
                raise ValueError(f'배열이 올 자리에 {type(data).__name__}이 왔습니다: {str(data)[:200]}')
            return data
        except urllib.error.HTTPError as e:
            # 429/5xx는 기다리면 풀릴 수 있고, 403/404는 기다려도 그대로다.
            if e.code in (429, 500, 502, 503, 504):
                last_err = e
            else:
                raise SystemExit(
                    f'❌ HTTP {e.code} ({url})\n'
                    f'   403이면 robots.txt/User-Agent 차단일 수 있습니다. 운영자 허락을 먼저 받으세요.')
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = e
        wait = max(delay, 1.0) * (2 ** attempt)
        print(f'  ⚠️ {type(last_err).__name__}: 재시도 {attempt + 1}/{MAX_RETRY} ({wait:.0f}초 후)')
        time.sleep(wait)
    raise SystemExit(f'❌ {MAX_RETRY}번 재시도 실패: {last_err} ({url})')


def load_store():
    """기존 저장분. 없으면 빈 상태로 시작한다."""
    try:
        with open(OUT_PATH, 'r', encoding='utf-8') as f:
            store = json.load(f)
    except FileNotFoundError:
        return {'rows': [], 'players': {}, 'maps': {}, 'cats': [], 'max_id': 0}
    except ValueError as e:
        raise SystemExit(f'❌ {OUT_PATH}이 깨졌습니다: {e}\n   지우고 전체 수집을 다시 하세요.')
    store.setdefault('rows', [])
    store.setdefault('players', {})
    store.setdefault('maps', {})
    store.setdefault('cats', [])
    store.setdefault('max_id', 0)
    return store


def write_atomic(path, text):
    """임시 파일에 다 쓴 뒤 교체한다 - 중간에 죽어도 반쯤 쓰인 파일이 남지 않는다
    (build_html.py의 write_text_atomic과 같은 이유, 같은 방식)."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def slim(rec, cats, maps, races, names, warn):
    """원본 1건 → 배열 행 1개. 담을 수 없는 건은 None을 돌려준다."""
    parts = rec.get('participants') or []
    if len(parts) != 2:
        # 팀전이라도 원본은 1행 1판(2명)이다. 2명이 아니면 우리가 모르는 형태다.
        warn['participants_아님'] += 1
        return None

    winners = [p for p in parts if p.get('result') == 'win']
    losers = [p for p in parts if p.get('result') == 'loss']
    if len(winners) != 1 or len(losers) != 1:
        warn['승자_패자_아님'] += 1
        return None
    win, lose = winners[0], losers[0]
    # 관찰된 전건이 participants[0] = 승자였다. 이 전제가 깨지면 알아야 한다
    # (깨져도 위에서 result로 골라냈으니 데이터는 맞다 - 문서화용 경고다).
    if parts[0].get('result') != 'win':
        warn['승자가_앞이_아님'] += 1

    rid = rec.get('id')
    date = rec.get('played_on')
    map_id = rec.get('map_id')
    if rid is None or not date:
        warn['id_또는_날짜_없음'] += 1
        return None

    cat = rec.get('category') or ''
    if cat not in cats:
        cats.append(cat)

    if map_id is not None and rec.get('map_name'):
        maps[str(map_id)] = rec['map_name']

    for p in (win, lose):
        pid = p.get('player_id')
        if pid is None:
            continue
        key = str(pid)
        if p.get('name'):
            names[key] = p['name']        # 이름이 바뀌면 최근 값으로 덮는다
        if p.get('race'):
            races[key][p['race']] += 1    # 종족은 최빈값을 쓴다

    return [rid, date, win.get('player_id'), lose.get('player_id'), map_id, cats.index(cat)]


def main():
    ap = argparse.ArgumentParser(description='eloboard 전적 수집 (전체 1회 + 이후 증분)')
    ap.add_argument('--full', action='store_true',
                    help='저장분을 무시하고 전체를 다시 받는다(첫 실행/스키마 변경 시)')
    ap.add_argument('--delay', type=float, default=DEFAULT_DELAY,
                    help=f'요청 간 대기 초. 기본 {DEFAULT_DELAY}(robots.txt Crawl-delay)')
    # 전체 1회가 약 1,974페이지라 넉넉히 잡는다(무한 루프만 막는 용도).
    ap.add_argument('--overlap-days', type=int, default=3,
                    help='이미 받은 경기라도 최근 며칠치는 다시 받아 덮어쓴다(결과 수정 반영). 기본 3')
    ap.add_argument('--max-pages', type=int, default=4000, help='안전장치: 최대 페이지 수')
    args = ap.parse_args()

    store = load_store()
    stop_at = 0 if args.full else store['max_id']
    if args.full:
        print('▶ 전체 수집 (37.5만 건이면 약 1,974요청)')
    elif stop_at:
        print(f'▶ 증분 수집 (저장된 최신 경기 id {stop_at:,} 까지)')
    else:
        print('▶ 저장분이 없어 전체 수집으로 시작합니다')

    cats = list(store['cats'])
    maps = dict(store['maps'])
    names = {k: v[0] for k, v in store['players'].items()}
    races = defaultdict(Counter)
    for k, v in store['players'].items():
        if len(v) > 1 and v[1]:
            races[k][v[1]] += 1

    warn = Counter()
    by_id = {r[0]: r for r in store['rows'] if r}
    before = len(by_id)
    changed = 0
    seen = set()            # 이번에 응답으로 실제 본 경기 id (삭제 판단용)
    min_seen = None         # 이번에 훑은 가장 낮은 id
    ceiling = None          # 수집을 시작한 시점의 최신 id. 그보다 새 경기는 다음 실행에 맡긴다
    reached = False         # 저장된 최신 경기까지 내려왔다
    finished = False        # 이번 범위를 끝까지 받았다(도중에 끊기지 않았다)

    # 겹쳐 읽기 기준일. 이 날짜 이후 경기는 이미 받았더라도 다시 받아 덮어쓴다.
    cutoff = '' if args.full else (dt.date.today() - dt.timedelta(days=args.overlap_days)).isoformat()
    if cutoff:
        print(f'  (최근 {args.overlap_days}일 = {cutoff} 이후 경기는 겹쳐 받아 갱신합니다)')

    for page in range(args.max_pages):
        offset = START_OFFSET + page * PAGE_STEP
        try:
            batch = fetch_page(offset, PAGE_LIMIT, args.delay)
        except SystemExit as e:
            # 전체 수집은 한 시간이 넘는다. 도중에 연결이 끊겼다고 한 시간치를 버리지 않고,
            # 여기까지 받은 것을 저장한 뒤 끝낸다(끊긴 실행이므로 max_id는 올리지 않는다).
            print(e)
            print('  ⚠️ 받다가 멈췄습니다 - 여기까지 받은 것만 저장합니다. 다시 실행하면 이어집니다.')
            break
        if not batch:
            print(f'  빈 응답 - 끝까지 받았습니다 (offset {offset:,})')
            finished = True
            break

        if ceiling is None:
            ceiling = max(r.get('id') or 0 for r in batch)

        oldest = ''             # 이 페이지에서 가장 오래된 경기 날짜(겹쳐 읽기 종료 판단용)
        for rec in batch:
            rid = rec.get('id')
            date = str(rec.get('played_on') or '')[:10]
            if rid is None:
                warn['id_또는_날짜_없음'] += 1
                continue
            if date:
                oldest = date if not oldest else min(oldest, date)
            # 수집 도중 새로 들어온 경기는 건너뛴다. offset 페이징은 위쪽에 행이
            # 끼어들면 전체가 한 칸씩 밀려서 경기가 빠지는데, 시작 시점의 최신
            # id를 천장으로 두면 이번 수집 범위가 고정된다.
            if rid > ceiling:
                continue
            seen.add(rid)
            min_seen = rid if min_seen is None else min(min_seen, rid)
            if rid <= stop_at:
                reached = True
                # 겹쳐 읽기 창(최근 며칠) 밖의 옛 경기는 다시 담지 않는다
                if not cutoff or not date or date < cutoff:
                    continue
            row = slim(rec, cats, maps, races, names, warn)
            if row and by_id.get(rid) != row:
                by_id[rid] = row
                changed += 1

        print(f'  offset {offset:>7,} → {len(batch):>3}건 받음 / 담은 경기 누적 {changed:,}')
        # 저장분에 닿았고, 이 페이지가 겹쳐 읽기 창보다 옛날이면 더 내려갈 이유가 없다
        if reached and (not cutoff or (oldest and oldest < cutoff)):
            print('  저장된 최신 경기 + 겹쳐 읽기 구간까지 확인 - 중단')
            finished = True
            break
        if args.delay:
            time.sleep(args.delay)
    else:
        print(f'  ⚠️ --max-pages({args.max_pages}) 한도에 걸려 멈췄습니다. 다시 실행하면 이어집니다.')

    # 훑은 id 구간(min_seen 위쪽 전부) 안에서 응답에 없던 경기는 원본에서 지워진 것이다.
    # 수집은 항상 목록 맨 위(offset 0)부터 훑으므로, 지금 서버 최신 id보다 높은 우리 행도
    # "없어진 것"이 맞다(맨 위 경기가 지워진 경우). 훑다 만 실행에서는 손대지 않는다 -
    # 받지 못한 구간을 삭제로 오해하면 안 된다.
    deleted = 0
    if finished and min_seen is not None:
        gone = [rid for rid in by_id if rid >= min_seen and rid not in seen]
        for rid in gone:
            del by_id[rid]
        deleted = len(gone)

    rows = sorted(by_id.values(), key=lambda r: -r[0])   # 최신 경기가 앞. 사이트가 그대로 쓰기 좋다
    added = len(rows) + deleted - before
    updated = changed - added
    players = {k: [names.get(k, k), (races[k].most_common(1)[0][0] if races[k] else '')]
               for k in set(names) | set(races)}

    out = {
        'synced_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        # 중간에 끊긴 실행은 max_id를 올리지 않는다(못 받은 구간이 영영 빈 채로 남지 않게).
        'max_id': (rows[0][0] if rows else 0) if finished else store['max_id'],
        'count': len(rows),
        # 행의 6번째 값이 이 배열의 인덱스다
        'cats': cats,
        'maps': maps,
        'players': players,
        # [경기id, 날짜, 승자 player_id, 패자 player_id, map_id, 카테고리 인덱스]
        'rows': rows,
    }
    text = json.dumps(out, ensure_ascii=False, separators=(',', ':'))
    write_atomic(OUT_PATH, text)

    print()
    print(f'✅ {OUT_PATH} 저장: 새 경기 {added:,}건 · 고쳐진 경기 {updated:,}건 · 지워진 경기 {deleted:,}건 '
          f'/ 총 {len(rows):,}건 ({len(text) / 1048576:.1f} MB)')
    if not finished:
        print('   ⚠️ 이번 실행은 끝까지 받지 못해 max_id를 올리지 않았습니다(다음 실행이 이어받습니다).')
    print(f'   선수 {len(players):,}명 · 맵 {len(maps)}종 · 카테고리 {cats}')
    if warn:
        print('   ⚠️ 담지 못한/이상한 건:', dict(warn))

    # 종족이 여러 개로 관찰된 선수는 "주 종족"으로 뭉쳐진다. 몇 명인지 알려준다.
    multi = [k for k, c in races.items() if len(c) > 1]
    if multi:
        print(f'   ℹ️ 종족이 경기마다 다른 선수 {len(multi)}명 - 사전에는 최빈 종족만 담깁니다')


if __name__ == '__main__':
    main()
