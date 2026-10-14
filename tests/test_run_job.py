"""작업 실행과 sync_jobs 기록을 나눠 다루는지."""
import sys
import types

import pytest

from scripts import run_job


class DB:
    def __init__(self, fail_updates=False):
        self.fail_updates = fail_updates
        self.updates = []

    def table(self, name):
        db = self

        class Q:
            def insert(self, row):
                self.kind = "insert"; return self

            def update(self, fields):
                self.kind = "update"; self.fields = fields; return self

            def eq(self, *a):
                return self

            def lt(self, *a):
                return self

            def execute(self):
                if self.kind == "insert":
                    return types.SimpleNamespace(data=[{"id": 1}])
                if db.fail_updates and self.fields.get("status") in ("success", "failed"):
                    raise RuntimeError("log write failed")
                db.updates.append(self.fields)
                return types.SimpleNamespace(data=[])
        return Q()


def _run(monkeypatch, db, job_run):
    mod = types.ModuleType("jobs.fake_job")
    mod.run = job_run
    monkeypatch.setitem(sys.modules, "jobs.fake_job", mod)
    monkeypatch.setattr(run_job, "get_supabase", lambda: db)
    monkeypatch.setattr(run_job.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["run_job.py", "fake_job"])
    run_job.main()


def test_success_log_failure_does_not_fail_a_finished_job(monkeypatch):
    from models.sync_job import JobResult
    _run(monkeypatch, DB(fail_updates=True), lambda: JobResult())


def test_failure_log_failure_does_not_hide_the_original_error(monkeypatch):
    def boom():
        raise ValueError("real cause")
    with pytest.raises(ValueError, match="real cause"):
        _run(monkeypatch, DB(fail_updates=True), boom)


def test_success_is_recorded(monkeypatch):
    from models.sync_job import JobResult
    db = DB()
    _run(monkeypatch, db, lambda: JobResult(records_read=3))
    assert db.updates[-1]["status"] == "success" and db.updates[-1]["records_read"] == 3
