from pathlib import Path


def test_roster_loader_pages_past_1000():
    text = (Path(__file__).resolve().parents[1] / "repositories" / "synergy_stats.py").read_text(encoding="utf-8")
    assert '.range(start, start + page_size - 1)' in text
    assert 'start += page_size' in text
