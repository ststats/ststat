from datetime import UTC, datetime
from importlib import util
from pathlib import Path

_spec = util.spec_from_file_location("eloboard_due", Path("scripts/eloboard_due.py"))
due = util.module_from_spec(_spec)
_spec.loader.exec_module(due)


class _Query:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    def __getattr__(self, name):
        def method(*args):
            self.calls.append((name, *args))
            return self

        return method

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _DB:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def table(self, name):
        self.calls.append(("table", name))
        return _Query(self.rows, self.calls)


def test_kst_day_start():
    # 2026-11-17 20:00 UTC = 11-18 05:00 KST → 11-18 0시 KST = 11-17 15:00 UTC
    now = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    assert due.kst_day_start(now) == datetime(2026, 11, 17, 15, 0, tzinfo=UTC)
    # 2026-11-17 14:59 UTC = 11-17 23:59 KST → 11-17 0시 KST = 11-16 15:00 UTC
    now = datetime(2026, 11, 17, 14, 59, tzinfo=UTC)
    assert due.kst_day_start(now) == datetime(2026, 11, 16, 15, 0, tzinfo=UTC)


def test_due_only_without_success_today():
    now = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    db = _DB([])
    assert due.is_due(now, db) is True
    assert ("eq", "status", "success") in db.calls
    assert ("gte", "started_at", "2026-11-17T15:00:00+00:00") in db.calls
    assert due.is_due(now, _DB([{"id": 1}])) is False
