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


def test_bands_split_by_win_rate_and_error():
    theta = np.linspace(-2, 2, 200)
    se = np.full(200, 0.1)
    top, width, count = ot.bands(theta, se, np.ones(200, dtype=bool))
    assert abs(width - ot.logit(ot.TIER_STEP_WIN)) < 1e-9  # 오차가 작으면 승률 기준
    assert count == 9  # 실력 폭 약 3.96 / 0.49
    assert ot.band_of(2.5, top, width, count) == 0
    assert ot.band_of(-5, top, width, count) == count - 1
    # 오차가 크면 칸을 넓혀 칸 수가 준다
    assert ot.bands(theta, np.full(200, 0.4), np.ones(200, dtype=bool))[2] < count


def _tl(values, se=0.1, active=True, sure=True, start_games=50):
    return [(v, se, active, sure, start_games + 10 * k) for k, v in enumerate(values)]


def test_promotion_needs_two_months_and_demotion_three():
    top, width, count = 1.0, 0.5, 6  # 칸 1 = (0.0, 0.5]
    mid = 0.25
    up = ot.assign(_tl([mid, 0.75, mid, 0.75, 0.75]), top, width, count)
    assert [k for k, _ in up] == [1, 1, 1, 1, 0]  # 한 달만 넘은 건 무시, 2달 연속이면 승급
    # 아래 경계(0.0)보다 반 칸(0.25) 넘게 낮아야 하고 3달 연속이어야 강등, 한 칸씩
    down = ot.assign(_tl([mid, -0.2, -0.6, -0.6, -0.6, -2.0, -2.0, -2.0]), top, width, count)
    assert [k for k, _ in down] == [1, 1, 1, 1, 2, 2, 2, 3]


def test_dormant_freezes_and_return_protects_from_demotion():
    top, width, count = 1.0, 0.5, 6
    tl = _tl([0.25, 0.25]) + [(0.25, 0.1, False, False, 70)] * 2
    # 복귀한 달 누적 80판. 강등 조건(3달 연속)은 6번째 달에 차지만 복귀 뒤 20판이 안 돼 미룬다
    tl += [(-0.9, 0.1, True, True, g) for g in (80, 85, 90, 95, 105)]
    out = ot.assign(tl, top, width, count)
    assert [s for _, s in out][2:4] == ["휴면", "휴면"]
    assert [k for k, _ in out][:4] == [1, 1, 1, 1]  # 휴면 동안 그대로
    assert [k for k, _ in out][4:] == [1, 1, 1, 1, 2]  # 복귀 뒤 10·15판은 보호, 25판에 강등


def test_provisional_shows_band_but_does_not_lock():
    top, width, count = 1.0, 0.5, 6
    out = ot.assign(_tl([0.75, 0.25], sure=False), top, width, count)
    assert out == [(0, "잠정"), (1, "잠정")]


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
    monkeypatch.setattr(sys, "argv", ["own_tier_report.py", "--months", "6", "--out", str(tmp_path)])
    rep.main()
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "1그룹 티어표" in report and "2그룹 티어표" in report and "## 검증" in report
    lines = (tmp_path / "table.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 61
