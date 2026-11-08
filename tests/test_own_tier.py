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


def test_bands_split_by_win_rate_and_widen_where_error_is_large():
    theta = np.linspace(-2, 2, 200)
    sure = np.ones(200, dtype=bool)
    b = ot.bands(theta, np.full(200, 0.1), sure)
    widths = np.array(b.uppers) - np.array(b.lowers)
    assert np.allclose(widths, ot.logit(ot.TIER_STEP_WIN))  # 오차가 작으면 승률 기준
    assert b.count == 9  # 실력 폭 약 3.96 / 0.49
    assert ot.band_of(2.5, b) == 0 and ot.band_of(-5, b) == b.count - 1
    # 아래 절반만 오차가 크면 아래 칸만 넓어져 칸 수가 준다
    se = np.where(theta < 0, 0.6, 0.1)
    wide = ot.bands(theta, se, sure)
    w2 = np.array(wide.uppers) - np.array(wide.lowers)
    assert wide.count < b.count
    assert w2[0] == widths[0] and w2[-1] > 2 * widths[0]
    # 잠정(sure 아님) 선수는 칸 나누기에 쓰지 않는다
    assert ot.bands(theta, se, theta >= 0).count <= 5


def _tl(values, se=0.1, active=True, sure=True):
    return [(v, se, active, sure) for v in values]


def test_start_and_promotion_use_score_minus_error():
    """처음 칸·승급 모두 오차를 뺀 점수로 정하고, 승급은 up_months달 연속이어야 한다."""
    b = ot.Bands.even(1.0, 0.5, 6)  # 칸 0 = 0.5 위, 칸 1 = [0.0, 0.5)
    # 점수 0.55는 칸 0이지만 오차 0.1을 빼면 0.5 아래라 칸 1에서 시작한다
    assert ot.assign(_tl([0.55]), b, z_up=1.0)[0][0] == 1
    run = ot.assign(_tl([0.25, 0.8, 0.25, 0.8, 0.8, 0.8]), b, z_up=1.0, up_months=3)
    assert [k for k, *_ in run] == [1, 1, 1, 1, 1, 0]  # 3달 연속 넘어야 승급


def test_never_demoted_but_decline_is_flagged():
    b = ot.Bands.even(1.0, 0.5, 6)
    out = ot.assign(_tl([0.25, -0.3, -0.3, -0.3, -0.3]), b, z_up=1.0)
    assert [k for k, *_ in out] == [1, 1, 1, 1, 1]  # 내리지 않는다
    assert [now for _, _, now, _ in out] == [1, 2, 2, 2, 2]  # 지금 실력 칸은 따로
    assert [d for *_, d in out] == [False, False, False, True, True]  # 3달 연속 한 칸 아래면 하락세


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
    assert report["groups"][1]["names"][:3] == ["0", "1", "2"]  # 2그룹은 숫자
    assert len(report["variants"]) == 4 and report["validation"]["rows"]
    assert "## 1그룹" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert (tmp_path / "table.csv").read_text(encoding="utf-8-sig").startswith("elo_id,name,group,tier,now")
