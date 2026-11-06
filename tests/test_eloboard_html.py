import json

from collectors import eloboard_html as html


def _page(data) -> str:
    """Next.js 페이지 흉내: 원본 데이터를 self.__next_f 조각 하나로 싣는다."""
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"<script>self.__next_f.push([1,{json.dumps(text, ensure_ascii=False)}])</script>"


def _side(result, player_id, race, name):
    return (
        f'{{"className":"g__gname g__g{result}"}},'
        f'{{"href":"/players/{player_id}"}},{{"className":"race {race}"}},{{"className":"p__nm","children":"{name}"}}'
    )


def _game(match_id, map_id, map_name, win, lose):
    return (
        f'["$","$L1e","{match_id}",{{"panelClass":"g gpanel"}},{{"src":"/maps/{map_id}_a.png"}},'
        f'{{"className":"g__gmapName","children":"{map_name}"}},{_side("win", *win)},{_side("lose", *lose)}]'
    )


def _duel(day, sets, key="m1", context=""):
    return {"key": key, "day": day, "context": context, "sets": sets}


def _set(match_id, winner, loser, map_id=7):
    return {
        "match_id": match_id,
        "winner_id": winner,
        "loser_id": loser,
        "winner": f"w{winner}",
        "loser": f"l{loser}",
        "map_id": map_id,
        "map_name": "투혼",
    }


EVENTS = {
    "events": [
        {"id": 19, "name": "K리그", "category": "pro_league", "group": "pro_league", "children": [], "last_on": ""},
        {"id": 30, "name": "중장전", "category": "solo_event", "group": "jungjang", "children": [], "last_on": ""},
        {"id": 50, "name": "KCM", "category": "solo_event", "group": "kcm", "children": [], "last_on": "2026-09-22"},
        {"id": 51, "name": "옛 컵", "category": "solo_event", "group": "cup", "children": [], "last_on": "2026-08-01"},
    ]
}


def _site(spon_reaches_cutoff=True):
    old = _duel("2026-09-01", [_set(1, 1, 2)])
    pages = {
        "/events": EVENTS,
        "/matches": {"duels": [_duel("2026-09-23", [_set(3000, 1, 2), {"match_id": None}]), _duel("x", [], key="p9")]},
        "/matches?p=2": {"duels": [_duel("2026-09-20", [_set(2990, 3, 4)]), *([old] if spon_reaches_cutoff else [])]},
        "/matches?kind=jungjang": {"duels": [_duel("2026-09-22", [_set(2980, 5, 6)], context="8강 · 중장전"), old]},
        "/matches?kind=kkeutjang": {"duels": [old]},
        "/matches?kind=myeolmang": {"duels": [old]},
        "/matches?kind=team": {
            "duels": [{"day": "2026-09-21", "href": "/events/19?tab=results&day=7"}, {"day": "2026-09-01"}]
        },
        "/events/19?tab=results&day=7": "[" + _game(2912970, 9, "폴리곤", (11, "T", "철수"), (12, "Z", "영희")) + "]",
        "/matches?kind=war": {
            "duels": [{"day": "2026-09-21", "href": "/colleges/wars/days/5", "context": "미니대전 3"}, old]
        },
        "/colleges/wars/days/5": "["
        + _game(2912960, 9, "폴리곤", (13, "P", "민수"), (14, "T", "지수"))
        + ","
        # 2:2는 받지 않는다
        + _game(2912961, 9, "폴리곤", (13, "P", "민수"), (14, "T", "지수")).replace(
            '"children":"민수"}',
            '"children":"민수"},{"href":"/players/15"},{"className":"race Z"},{"className":"p__nm","children":"둘째"}',
        )
        + "]",
        "/events/50?tab=results": {"duels": [_duel("2026-09-22", [_set(2950, 7, 8)])]},
    }
    asked = []

    def get(path):
        asked.append(path)
        page = pages.get(path, {"duels": []})
        return _page(page)

    return get, asked


def test_collect_reads_every_tab_with_event_categories():
    get, asked = _site()
    got = html.collect("2026-09-10", get=get)
    by_id = {i: (r["category"], r["played_on"]) for i, r in got.rows.items()}
    assert by_id == {
        3000: ("sponsored", "2026-09-23"),
        2990: ("sponsored", "2026-09-20"),
        2980: ("solo_event", "2026-09-22"),
        2912970: ("pro_league", "2026-09-21"),
        2912960: ("college_mini", "2026-09-21"),
        2950: ("solo_event", "2026-09-22"),
    }
    assert got.skipped == 1  # 2:2
    assert got.complete and not got.notes
    assert "/events/51?tab=results" not in asked  # 기간 전에 끝난 대회는 읽지 않는다
    assert got.requests == len(asked)
    team = got.rows[2912970]
    assert team["map_id"] == 9 and team["map_name"] == "폴리곤"
    assert [(p["player_id"], p["race"], p["result"]) for p in team["participants"]] == [
        (11, "T", "win"),
        (12, "Z", "loss"),
    ]


def test_collect_marks_incomplete_when_a_list_ends_before_the_cutoff():
    """스폰 목록은 최근 30일만 보여 기간 처음까지 닿지 못하면 사라진 경기 판정을 막는다."""
    get, _ = _site(spon_reaches_cutoff=False)
    got = html.collect("2026-09-10", get=get)
    assert not got.complete
    assert got.notes == ["matches kind=None: 목록이 2026-09-10까지 닿기 전에 끝남"]


def test_collect_refuses_empty_pages():
    import pytest

    with pytest.raises(RuntimeError, match="대회 목록"):
        html.collect("2026-09-10", get=lambda path: _page({"duels": []}))


def test_page_data_inlines_lazy_rows():
    """뒤에 따로 실린 조각은 제자리에 끼우고, 컴포넌트 자리("$","$L..")는 그대로 둔다."""
    data = html.page_data(_page('0:["$","$L1d",{"x":"$L1c"}]\n1c:{"a":1}'))
    assert data.startswith('0:["$","$L1d",{"x":{"a":1}}]')


def _setup_html(monkeypatch, rows, previous, db_window_ids=(), complete=True):
    import datetime as dt

    from jobs import sync_eloboard as job

    monkeypatch.setattr(job, "SOURCE", "html")
    asked = {}

    def collect(cutoff):
        asked["cutoff"] = cutoff
        got = html.Collected(requests=3, complete=complete)
        got.add(rows)
        return got

    monkeypatch.setattr(job, "collect", collect)
    monkeypatch.setattr(job, "get_latest_match_id", lambda: 3000)
    monkeypatch.setattr(job, "load_previous_metadata", lambda: previous)
    monkeypatch.setattr(job, "load_previous_pending_deletes", lambda: set(previous.get("pending_delete") or []))
    monkeypatch.setattr(job, "upsert_dimensions", lambda m: {"players": 0, "maps": 0, "categories": 0})
    saved = {}
    monkeypatch.setattr(
        job, "upsert_matches", lambda m: len(saved.setdefault("ids", sorted(x.elo_match_id for x in m)))
    )
    monkeypatch.setattr(job, "stage_unknown_elo_candidates", lambda m: 0)
    monkeypatch.setattr(job, "load_match_ids_between", lambda a, b: set(db_window_ids))
    monkeypatch.setattr(job, "load_match_rows", lambda ids: [{"elo_match_id": i} for i in sorted(ids)])
    monkeypatch.setattr(job, "save_deletion_backup", lambda rows, run_id=None: None)

    def delete(ids):
        saved["deleted"] = sorted(ids)
        return len(ids)

    monkeypatch.setattr(job, "delete_match_ids", delete)

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 24, 12, 0, tzinfo=tz)

    monkeypatch.setattr(job.dt, "datetime", FixedDateTime)
    return job, saved, asked


def _rows():
    from tests.test_eloboard import _row

    return [_row(3000, "2026-09-23"), _row(2900, "2026-09-12")]


def test_html_first_run_of_the_day_rescans_the_month_and_deletes(monkeypatch):
    job, saved, asked = _setup_html(
        monkeypatch, _rows(), {"deep_on": "2026-09-23", "pending_delete": [2500]}, {3000, 2900, 2500, 2400}
    )
    result = job.run()
    assert asked["cutoff"] == "2026-09-01"
    assert saved["ids"] == [2900, 3000]
    assert saved["deleted"] == [2500]
    assert result.metadata["deep_on"] == "2026-09-24"
    assert result.metadata["pending_delete"] == [2400]


def test_html_later_runs_read_recent_days_and_carry_pending(monkeypatch):
    job, saved, asked = _setup_html(
        monkeypatch, _rows(), {"deep_on": "2026-09-24", "pending_delete": [2400]}, {3000, 2900, 2400}
    )
    result = job.run()
    assert asked["cutoff"] == "2026-09-21"
    assert "deleted" not in saved
    assert result.metadata["delete_skipped"] == "recent_only"
    assert result.metadata["pending_delete"] == [2400]  # 다음 다시 읽기에서 판정
    assert result.metadata["deep_on"] == "2026-09-24"


def test_html_incomplete_rescan_never_deletes(monkeypatch):
    job, saved, _ = _setup_html(monkeypatch, _rows(), {"pending_delete": [2500]}, {3000, 2900, 2500}, complete=False)
    result = job.run()
    assert "deleted" not in saved
    assert result.metadata["delete_skipped"] == "incomplete"
    assert result.metadata["pending_delete"] == [2500]


def test_stored_race_is_kept_when_rows_have_no_race(monkeypatch):
    """스폰 목록엔 종족이 없어도 저장된 종족을 지우지 않는다."""
    import repositories.eloboard as repo
    from models.eloboard import EloMatch, EloParticipant

    sent = {}
    monkeypatch.setattr(repo, "load_player_races", lambda ids: {i: "Z" for i in ids if i == 2})
    monkeypatch.setattr(repo, "_upsert_batch", lambda players, maps: sent.setdefault("players", players) and {})
    monkeypatch.setattr(repo, "load_categories", lambda: {"sponsored": 0})
    monkeypatch.setattr(repo, "ensure_categories", lambda names, before: before)
    match = EloMatch(
        1,
        "2026-09-01",
        EloParticipant(1, "a", "T", "win"),
        EloParticipant(2, "b", None, "loss"),
        None,
        None,
        "sponsored",
    )
    repo.upsert_dimensions([match])
    assert {p["elo_id"]: p["race"] for p in sent["players"]} == {1: "T", 2: "Z"}
