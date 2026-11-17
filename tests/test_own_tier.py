import datetime as dt
import random

import numpy as np

from processors import own_tier as ot


def _games(skills, n_games, start, days, seed=1, pairs=None):
    """실력(로짓)대로 승패를 뽑은 경기 행 [id, 날짜, 승자, 패자, 맵, 형식]."""
    rng = random.Random(seed)
    ids = list(skills)
    rows = []
    for k in range(n_games):
        a, b = rng.sample(pairs or ids, 2) if not pairs else rng.choice(pairs)
        p = 1 / (1 + np.exp(-(skills[a] - skills[b])))
        win, lose = (a, b) if rng.random() < p else (b, a)
        day = start + dt.timedelta(days=rng.randrange(days))
        rows.append([k + 1, day.isoformat(), win, lose, None, 0])
    return rows


def test_rate_recovers_skill_order_without_tier_labels():
    skills = {i: (i - 10) * 0.3 for i in range(1, 21)}
    rows = _games(skills, 6000, dt.date(2026, 1, 1), 300)
    data = ot.prepare(rows, ["solo_event"], {str(i): [f"p{i}", "T"] for i in skills})
    theta, se, _, _ = ot.rate(ot.fold(data, dt.date(2026, 11, 1)))
    order = [int(data["ids"][i]) for i in np.argsort(-theta)]
    # 이웃끼리는 뒤바뀔 수 있어도 위·아래 다섯은 제자리
    assert set(order[:5]) == {16, 17, 18, 19, 20}
    assert set(order[-5:]) == {1, 2, 3, 4, 5}
    assert np.all(se > 0)


def test_groups_split_rarely_meeting_crowds_and_gap_sign():
    """서로 거의 안 붙는 두 무리를 나누고, 무리 사이 경기로 높이 차이 부호를 맞힌다."""
    upper = {i: 1.0 + (i - 15) * 0.1 for i in range(1, 31)}  # 높은 무리 30명
    lower = {i: -1.0 + (i - 45) * 0.1 for i in range(31, 61)}  # 낮은 무리 30명
    rows = _games(upper, 4000, dt.date(2026, 1, 1), 300, seed=4)
    rows += _games(lower, 4000, dt.date(2026, 1, 1), 300, seed=5)
    rng = random.Random(6)
    for k in range(200):  # 무리 사이 경기는 드물고 대개 높은 무리가 이긴다
        a, b = rng.choice(list(upper)), rng.choice(list(lower))
        p = 1 / (1 + np.exp(-(upper[a] - lower[b])))
        win, lose = (a, b) if rng.random() < p else (b, a)
        rows.append([100000 + k, "2026-06-01", win, lose, None, 0])
    skills = {**upper, **lower}
    data = ot.prepare(rows, ["solo_event"], {str(i): [f"p{i}", "T"] for i in skills})
    group = ot.groups(data)
    g_of = {int(pid): int(g) for pid, g in zip(data["ids"], group)}
    assert len({g_of[i] for i in upper}) == 1 and len({g_of[i] for i in lower}) == 1
    assert g_of[1] != g_of[31]
    theta, _, _, _ = ot.rate(ot.fold(data, dt.date(2026, 11, 1)))
    gap, se, n, _ = ot.group_gap(data, theta, group)
    assert n == 200
    # 0번 무리가 높은 무리면 gap > 0
    assert (gap > 0) == (g_of[1] == 0)
    assert abs(abs(gap) - 2.0) < 4 * se + 0.5


def test_verify_cap_limits_score_to_opponents_level():
    """아래 상대만 이긴 선수는 상대 상위 10% 수준 + 50점까지만 인정한다."""
    rows = [[k, "2026-06-01", 1, 2 + k % 5, None, 0] for k in range(60)]  # 1번이 2~6번만 이긴다
    data = ot.prepare(rows, ["solo_event"], {})
    f = ot.fold(data, dt.date(2026, 11, 1))
    theta = np.array([3.0, 0.0, 0.1, 0.2, 0.3, 0.4])  # ids 순서: 1, 2, 3, ...
    cap = ot.verify_cap(f, theta)
    pos = {pid: k for k, pid in enumerate(data["ids"])}
    assert abs(cap[pos["1"]] - (0.4 + ot.CAP_MARGIN)) < 1e-9
    assert np.all(
        np.minimum(theta, cap)[[pos[str(i)] for i in range(2, 7)]] == theta[[pos[str(i)] for i in range(2, 7)]]
    )


def test_cohort_gap_finds_an_overrated_debut_year():
    """같은 그룹 안에서 2026년 데뷔 선수들이 점수보다 덜 이기면 차이가 음수로 나온다."""
    rng = random.Random(9)
    old, new = list(range(1, 21)), list(range(21, 41))
    rows = [[k, "2024-01-01", a, b, None, 0] for k, (a, b) in enumerate(zip(old, old[1:] + old[:1]))]
    rows += [[100 + k, "2026-02-01", n, n + 1 if n < 40 else 21, None, 0] for k, n in enumerate(new)]
    for k in range(400):  # 옛 선수가 신입을 70% 이긴다
        a, b = rng.choice(old), rng.choice(new)
        win, lose = (a, b) if rng.random() < 0.7 else (b, a)
        rows.append([1000 + k, "2026-06-01", win, lose, None, 0])
    data = ot.prepare(rows, ["solo_event"], {})
    theta = np.zeros(len(data["ids"]))  # 점수는 모두 같다고 두면
    gaps = {
        (g, y): gap
        for g, y, gap, _, _ in ot.cohort_gaps(
            data, theta, np.zeros(len(theta), dtype=int), dt.date(2026, 11, 1).toordinal()
        )
    }
    assert gaps[(0, 2026)] < -0.5  # 신입 연도가 점수만큼 못 이긴다(점수가 높게 떠 있다)


def test_bands_split_by_win_rate_and_widen_where_error_is_large():
    theta = np.linspace(-2, 2, 200)
    sure = np.ones(200, dtype=bool)
    b = ot.bands(theta, np.full(200, 0.1), sure)
    widths = np.array(b.uppers) - np.array(b.lowers)
    up = np.array(b.uppers)
    assert np.allclose(widths[up > 0], ot.logit(ot.TIER_STEP_WIN))  # 중앙값 위는 승률 기준 그대로
    below = widths[up <= 0]  # 중앙값 아래는 한 칸마다 BOTTOM_GROW배씩 넓어진다
    assert np.allclose(below[1:] / below[:-1], ot.BOTTOM_GROW) and below[0] > widths[0]
    assert ot.band_of(2.5, b) == 0 and ot.band_of(-5, b) == b.count - 1
    # 칸 수 상한이 있으면 마지막 칸이 그 아래 선수를 모두 받는다(2그룹 베이비)
    capped = ot.bands(theta, np.full(200, 0.1), sure, most=3)
    assert capped.count == 3 and ot.band_of(-2.0, capped) == 2
    # 아래 절반만 오차가 크면 아래 칸만 넓어져 칸 수가 준다
    se = np.where(theta < 0, 0.6, 0.1)
    wide = ot.bands(theta, se, sure)
    w2 = np.array(wide.uppers) - np.array(wide.lowers)
    assert wide.count < b.count
    assert w2[0] == widths[0] and np.isclose(w2[-1], 2 * 0.6)  # 아래 칸 폭 = 오차 중앙값의 2배
    # 잠정(sure 아님) 선수는 칸 나누기에 쓰지 않는다
    assert ot.bands(theta, se, theta >= 0).count <= 5


def _tl(values, se=0.1, active=True, sure=True):
    return [(v, se, active, sure) for v in values]


def test_start_and_promotion_use_score_minus_error():
    """처음 칸·승급 모두 오차를 뺀 점수로 정하고, 승급은 최근 up_window달 중 up_months달 넘어야 한다."""
    b = ot.Bands.even(1.0, 0.5, 6)  # 칸 0 = 0.5 위, 칸 1 = [0.0, 0.5)
    # 점수 0.55는 칸 0이지만 오차 0.1을 빼면 0.5 아래라 칸 1에서 시작한다
    assert ot.assign(_tl([0.55]), b, z_up=1.0)[0][0] == 1
    run = ot.assign(_tl([0.25, 0.8, 0.25, 0.8, 0.8, 0.8]), b, z_up=1.0, up_months=3, up_window=3)
    assert [k for k, *_ in run] == [1, 1, 1, 1, 1, 0]  # 3달 중 3달이면 연속과 같다
    # 3달 중 2달이면 한 달 삐끗해도 이어서 센다(이번 달은 넘어야 한다)
    run = ot.assign(_tl([0.25, 0.8, 0.25, 0.8]), b, z_up=1.0, up_months=2, up_window=3)
    assert [k for k, *_ in run] == [1, 1, 1, 0]
    run = ot.assign(_tl([0.25, 0.8, 0.8, 0.25]), b, z_up=1.0, up_months=3, up_window=4)
    assert {k for k, *_ in run} == {1}  # 이번 달이 아래면 올리지 않는다


def test_hot_streak_alone_does_not_promote():
    """최근 폼으로만 점수가 오르면(바탕 점수는 그대로) 칸을 올리지 않는다. 지금 실력 칸은 폼까지 본다."""
    b = ot.Bands.even(1.0, 0.5, 6)
    hot = [(0.25, 0.1, True, True, 0.25)] + [(0.8, 0.1, True, True, 0.25)] * 4  # θ 0.8, 바탕 0.25
    out = ot.assign(hot, b, z_up=1.0)
    assert {k for k, *_ in out} == {1}
    assert out[-1][2] == 0
    steady = [(0.25, 0.1, True, True, 0.25)] + [(0.8, 0.1, True, True, 0.8)] * 4
    assert ot.assign(steady, b, z_up=1.0)[-1][0] == 0


def test_one_tier_drop_is_not_demoted_but_flagged():
    b = ot.Bands.even(1.0, 0.5, 6)
    out = ot.assign(_tl([0.25] + [-0.3] * 8), b, z_up=1.0)
    assert {k for k, *_ in out} == {1}  # 한 칸 아래로는 오래 있어도 내리지 않는다
    assert [now for _, _, now, _ in out][:5] == [1, 2, 2, 2, 2]  # 지금 실력 칸은 따로
    assert [d for *_, d in out][:5] == [False, False, False, True, True]  # 3달 연속 한 칸 아래면 하락세


def test_demoted_one_tier_only_when_far_below_for_long():
    """오차를 더한 점수로도 DEMOTE_GAP칸 아래인 게 DEMOTE_MONTHS달 연속이어야 한 칸만 내린다."""
    b = ot.Bands.even(1.0, 0.5, 6)  # 칸 1 = [0.0, 0.5), 칸 3 = [-1.0, -0.5)
    months = ot.DEMOTE_MONTHS
    out = ot.assign(_tl([0.25] + [-0.8] * (months + 2)), b, z_up=1.0)
    assert [k for k, *_ in out] == [1] * months + [2] * 3  # 6달째에 한 칸, 그 뒤로는 한 칸 차이라 그대로
    # 오차가 크면 넉넉히 본 점수가 두 칸 안이라 내리지 않는다
    wide = ot.assign([(0.25, 0.1, True, True)] + [(-0.8, 1.0, True, True)] * (months + 2), b, z_up=1.0)
    assert {k for k, *_ in wide} == {1}
    # 중간에 휴면하면 다시 센다
    gap = _tl([0.25] + [-0.8] * (months - 1)) + [(-0.8, 0.1, False, False)] + _tl([-0.8] * (months - 1))
    assert {k for k, *_ in ot.assign(gap, b, z_up=1.0)} == {1}


def test_dormant_freezes_tier():
    b = ot.Bands.even(1.0, 0.5, 6)
    out = ot.assign(_tl([0.25]) + [(-2.0, 0.1, False, False)] * 2 + _tl([0.25]), b, z_up=1.0)
    assert [(k, s) for k, s, *_ in out] == [(1, "확정"), (1, "휴면"), (1, "휴면"), (1, "확정")]


def test_provisional_is_not_placed_until_sure():
    b = ot.Bands.even(1.0, 0.5, 6)
    out = ot.assign(_tl([0.85], sure=False) + _tl([0.85]), b, z_up=1.0)
    assert [(k, s) for k, s, *_ in out] == [(None, "잠정"), (0, "확정")]


def test_linked_accounts_are_merged_into_main():
    rows = [[1, "2026-01-01", 10, 20, None, 0], [2, "2026-01-02", 11, 20, None, 0], [3, "2026-01-03", 10, 11, None, 0]]
    data = ot.prepare(rows, ["solo_event"], {}, alias={"11": "10"})
    assert data["ids"] == ["10", "20"]
    assert len(data["day"]) == 2  # 같은 사람끼리 붙은 경기는 뺀다


def test_anchor_moves_only_where_bands_start(monkeypatch):
    """도달 티어가 비교 티어와 같은 칸이 가장 많아지게 경계 위치만 옮긴다. 칸 폭은 그대로다."""
    from scripts import own_tier_report as rep

    monkeypatch.setattr(ot, "BOTTOM_GROW", 1.0)  # 고른 칸 폭으로 비교 티어를 만든다

    n = 200
    theta, se, on = np.linspace(-2, 2, n), np.full(n, 0.1), np.ones(n, dtype=bool)
    fits = [{"theta": theta, "se": se, "active": on, "sure": on, "form": np.zeros(n)} for _ in range(4)]
    step = ot.logit(ot.TIER_STEP_WIN)
    # 비교 티어: 맨 위(2.0)에서 반 칸 위를 0번 칸 위 경계로 잡았을 때의 처음 칸(오차를 뺀 점수)
    target = np.floor((2.0 + 0.5 * step - (theta - ot.Z_UP * se)) / step).astype(int)
    group = np.zeros(n, dtype=int)
    b = ot.bands(theta, se, on, top=rep.anchor_top(fits, group, 0, target, len(ot.TIER_NAMES)))
    assert np.allclose(np.array(b.uppers) - np.array(b.lowers), step)
    reached = np.array([ot.assign([(t, 0.1, True, True)] * 4, b)[-1][0] for t in theta])
    assert np.mean(reached == target) > 0.95
    assert reached.min() == 0  # 맨 위 칸은 비지 않는다
    # 비교 티어가 모두 한 칸 아래로 밀려 있어도 맨 위 칸을 비우는 위치는 고르지 않는다
    top = rep.anchor_top(fits, group, 0, target + 1, len(ot.TIER_NAMES))
    assert min(ot.assign([(t, 0.1, True, True)] * 4, ot.bands(theta, se, on, top=top))[-1][0] for t in theta) == 0
    assert rep.anchor_top(fits, group, 0, np.full(n, -1), len(ot.TIER_NAMES)) is None  # 비교할 선수가 없으면 기본 위치


def test_report_runs_end_to_end(tmp_path, monkeypatch):
    import sys

    from scripts import own_tier_report as rep

    skills = {i: (i - 30) * 0.08 for i in range(1, 61)}
    rows = _games(skills, 20000, dt.date(2025, 1, 1), 680, seed=3)
    tiers = ["갓", "킹", "잭", "조커", "스페이드", "0", "1", "2", "3", "4", "5", "6", "7", "8"]
    source = {
        "categories": [{"category_id": 0, "name": "solo_event"}],
        "maps": [],
        "players": [{"elo_id": i, "name": f"p{i}", "race": "TZP"[i % 3]} for i in skills],
        "matches": [
            {
                "elo_match_id": r[0],
                "match_date": r[1],
                "winner_elo_id": r[2],
                "loser_elo_id": r[3],
                "map_id": None,
                "category_id": 0,
            }
            for r in rows
        ],
        "tier_members": [
            {
                "elo_id": i,
                "nickname": f"n{i}",
                "soop_id": f"s{i}",
                "tier": tiers[min(13, (60 - i) // 5)],
                "affiliation": "캄몬대",
                "race": "T",
            }
            for i in skills
        ],
    }
    import repositories.derived_stats as ds

    monkeypatch.setattr(ds, "load_source_data", lambda: source)
    monkeypatch.setattr(rep, "load_aliases", dict)
    monkeypatch.setattr(sys, "argv", ["own_tier_report.py", "--months", "6", "--out", str(tmp_path)])
    rep.main()
    import json

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert [g["label"] for g in report["groups"]] == ["1그룹", "2그룹"]
    assert report["groups"][0]["names"][:2] == ["갓", "킹"]  # 1그룹은 카드 이름
    assert report["groups"][1]["names"][:3] == ["0", "1", "2"]  # 2그룹은 숫자
    assert len(report["variants"]) == 4 and report["validation"]["rows"]
    assert {"before", "after", "players"} <= report["cap"].keys() and isinstance(report["cohorts"], list)
    assert "## 1그룹" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (tmp_path / "table.csv").read_text(encoding="utf-8-sig").startswith("elo_id,name,group,tier,half,now")
    assert all(p["half"] in ("상", "하") for p in report["players"] if p["tier"])  # 칸 안 위치
    assert all(isinstance(p["pending"], bool) for p in report["players"])  # 승급불
