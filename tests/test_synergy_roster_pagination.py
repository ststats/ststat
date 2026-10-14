from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_roster_loader_pages_past_1000():
    """명단이 1000행을 넘어도 잘리지 않게 공통 fetch_all로 끝까지 읽는다."""
    stats = (ROOT / "repositories" / "synergy_stats.py").read_text(encoding="utf-8")
    assert 'rows = fetch_all(lambda: db.table("tier_members")' in stats
    helper = (ROOT / "repositories" / "supabase.py").read_text(encoding="utf-8")
    assert "make_query().range(len(rows), len(rows) + page_size - 1)" in helper
    assert "if len(batch) < page_size:" in helper
