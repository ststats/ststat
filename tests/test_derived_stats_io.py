"""파생 통계 저장소의 병렬 읽기·쓰기와 남은 스냅샷 정리를 가짜 DB로 확인한다."""
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from repositories import derived_stats as repo


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.orders = [], []
        self.count = self.lim = None
        self.action, self.payload = 'select', None

    def select(self, cols, count=None):
        self.count = count
        return self

    def order(self, col, desc=False):
        self.orders.append((col, desc))
        return self

    def gt(self, col, v):
        self.filters.append(lambda r: r[col] > v)
        return self

    def lte(self, col, v):
        self.filters.append(lambda r: r[col] <= v)
        return self

    def lt(self, col, v):
        self.filters.append(lambda r: r[col] < v)
        return self

    def eq(self, col, v):
        self.filters.append(lambda r: r[col] == v)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def insert(self, payload):
        self.action, self.payload = 'insert', payload
        return self

    def update(self, payload):
        self.action, self.payload = 'update', payload
        return self

    def delete(self):
        self.action = 'delete'
        return self

    def execute(self):
        with self.db.lock:
            rows = self.db.tables.setdefault(self.table, [])
            if self.action == 'insert':
                self.db.threads.add(threading.get_ident())
                rows.extend(self.payload)
                return SimpleNamespace(data=self.payload, count=None)
            hit = [r for r in rows if all(f(r) for f in self.filters)]
            if self.action == 'delete':
                self.db.tables[self.table] = [r for r in rows if r not in hit]
                return SimpleNamespace(data=hit, count=None)
            if self.action == 'update':
                for r in hit:
                    r.update(self.payload)
                return SimpleNamespace(data=hit, count=None)
            for col, desc in reversed(self.orders):
                hit.sort(key=lambda r: r[col], reverse=desc)
            limit = min([x for x in (self.lim, self.db.max_rows) if x is not None], default=None)
            return SimpleNamespace(data=[dict(r) for r in hit[:limit]], count=len(hit) if self.count else None)


class FakeDB:
    def __init__(self, tables=None, max_rows=None):
        self.tables = tables or {}
        self.max_rows = max_rows
        self.lock = threading.Lock()
        self.threads = set()

    def table(self, name):
        return FakeQuery(self, name)


def _matches(ids):
    return [{'elo_match_id': i, 'match_date': '2026-09-01', 'winner_elo_id': 1, 'loser_elo_id': 2,
             'map_id': None, 'category_id': 1} for i in ids]


@pytest.fixture
def fake(monkeypatch):
    def install(db):
        monkeypatch.setattr(repo, 'get_supabase', lambda: db)
        monkeypatch.setattr(repo, 'new_supabase', lambda: db)
        monkeypatch.setattr(repo, '_local', threading.local())
        return db
    return install


def test_parallel_load_returns_every_match_once_in_id_order(fake):
    # ID가 고르지 않게 퍼져 있고(빈 구간·몰린 구간) 한 구간이 여러 페이지를 넘는 경우
    ids = list(range(10, 3000, 7)) + list(range(50000, 53500)) + [99999]
    fake(FakeDB({'elo_matches': _matches(reversed(ids))}))
    rows = repo.load_matches()
    assert [r['elo_match_id'] for r in rows] == sorted(ids)


def test_load_survives_server_row_cap_smaller_than_page(fake):
    # 서버가 한 번에 주는 행 수가 페이지 크기보다 작아도 빠짐없이 받는다
    fake(FakeDB({'elo_matches': _matches(range(1, 50001))}, max_rows=300))
    rows = repo.load_matches()
    assert [r['elo_match_id'] for r in rows] == list(range(1, 50001))


def test_load_fails_instead_of_returning_partial_data(fake, monkeypatch):
    fake(FakeDB({'elo_matches': _matches(range(1, 5001))}))
    real = repo._load_match_range
    # 한 구간을 덜 받은 상황(읽는 도중 행이 사라짐 등) → 정확한 행 수와 달라 실패해야 한다
    monkeypatch.setattr(repo, '_load_match_range', lambda a, b: real(a, b)[1:] if a < 100 else real(a, b))
    with pytest.raises(RuntimeError, match='mismatch'):
        repo.load_matches()


def test_load_empty_table(fake):
    fake(FakeDB({'elo_matches': []}))
    assert repo.load_matches() == []


def test_split_ranges_cover_everything_without_overlap():
    for low, high, parts in [(1, 1, 24), (5, 100, 24), (1, 376270, 24), (100, 103, 10)]:
        ranges = repo._split_ranges(low, high, parts)
        assert ranges[0][0] == low - 1 and ranges[-1][1] == high
        assert all(a < b for a, b in ranges)
        assert all(ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1))
        assert len(ranges) <= parts


def test_insert_writes_every_row_in_chunks_with_snapshot_id(fake):
    db = fake(FakeDB())
    rows = [{'n': i} for i in range(2345)]
    assert repo._insert('elo_h2h_stats', rows, 'snap', chunk=100) == 2345
    stored = db.tables['elo_h2h_stats']
    assert sorted(r['n'] for r in stored) == list(range(2345))
    assert all(r['snapshot_id'] == 'snap' for r in stored)
    assert 'snapshot_id' not in rows[0]  # 원본은 건드리지 않는다


def test_insert_failure_propagates(fake, monkeypatch):
    fake(FakeDB())
    calls = []

    def boom(table, payload):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError('insert failed')
    monkeypatch.setattr(repo, '_insert_chunk', boom)
    with pytest.raises(RuntimeError, match='insert failed'):
        repo._insert('elo_h2h_stats', [{'n': i} for i in range(1000)], 'snap', chunk=100)


def test_cleanup_removes_only_old_orphan_building_snapshots(fake):
    now = datetime.now(timezone.utc)
    ago = lambda h: (now - timedelta(hours=h)).isoformat()
    db = fake(FakeDB({'elo_derived_snapshots': [
        {'snapshot_id': 'active', 'status': 'active', 'created_at': ago(1)},
        {'snapshot_id': 'building-now', 'status': 'building', 'created_at': ago(0.2)},
        {'snapshot_id': 'building-orphan', 'status': 'building', 'created_at': ago(10)},
        *[{'snapshot_id': f'old{i}', 'status': 'retired', 'created_at': ago(5 + i)} for i in range(5)],
    ]}))
    repo.cleanup_old_snapshots(keep=3)
    left = {r['snapshot_id'] for r in db.tables['elo_derived_snapshots']}
    assert left == {'active', 'building-now', 'old0', 'old1', 'old2'}


def test_run_job_closes_stale_running_rows_of_the_same_job():
    from scripts import run_job

    now = datetime.now(timezone.utc)
    db = FakeDB({'sync_jobs': [
        {'id': 1, 'job_name': 'sync_eloboard', 'status': 'running', 'started_at': (now - timedelta(hours=5)).isoformat()},
        {'id': 2, 'job_name': 'sync_eloboard', 'status': 'running', 'started_at': (now - timedelta(minutes=10)).isoformat()},
        {'id': 3, 'job_name': 'sync_videos', 'status': 'running', 'started_at': (now - timedelta(hours=5)).isoformat()},
    ]})
    run_job.close_stale_runs(db, 'sync_eloboard')
    status = {r['id']: r['status'] for r in db.tables['sync_jobs']}
    assert status == {1: 'failed', 2: 'running', 3: 'running'}
