from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from collectors.soop_videos import collect_thumbnail
from jobs import sync_videos as job
from repositories.videos import fill_soop_pick_thumbnail


def test_thumbnail_attribute_order_and_entities():
    page = b"<meta content='https://iflv14.sooplive.com/a.jpg?a=1&amp;b=2' property='og:image'>"
    with patch("collectors.soop_videos.urllib.request.urlopen", return_value=BytesIO(page)) as fetch:
        assert collect_thumbnail("soop:123") == "https://iflv14.sooplive.com/a.jpg?a=1&b=2"
    assert fetch.call_args.args[0].full_url == "https://vod.sooplive.co.kr/player/123"


@pytest.mark.parametrize("page", [b"", b"<meta property='og:image' content='https://evil.test/a.jpg'>"])
def test_missing_or_untrusted_thumbnail(page):
    with patch("collectors.soop_videos.urllib.request.urlopen", return_value=BytesIO(page)):
        with pytest.raises(ValueError, match="not found"):
            collect_thumbnail("soop:123")


def test_invalid_id_never_sends_request():
    with patch("collectors.soop_videos.urllib.request.urlopen") as fetch:
        with pytest.raises(ValueError):
            collect_thumbnail("soop:123/other")
    fetch.assert_not_called()


def test_write_rechecks_empty_thumbnail():
    query = MagicMock()
    query.update.return_value = query
    query.eq.return_value = query
    query.or_.return_value = query
    query.execute.return_value.data = []
    with patch("repositories.videos.get_supabase") as db:
        db.return_value.table.return_value = query
        assert fill_soop_pick_thumbnail("soop:123", "https://iflv14.sooplive.com/a.jpg") == 0
    query.update.assert_called_once_with({"thumb": "https://iflv14.sooplive.com/a.jpg"})
    query.or_.assert_called_once_with("thumb.is.null,thumb.eq.")
    assert [c.args for c in query.eq.call_args_list] == [("id", "soop:123"), ("kind", "soop")]


def test_soop_only_job_retries_and_preserves_manual_fields(monkeypatch):
    row = {"id": "soop:123", "thumb": None, "title": "Manual title", "hidden": True}
    monkeypatch.setattr(job, "load_active_channels", lambda: [])
    monkeypatch.setattr(job, "load_soop_picks_without_thumbnail", lambda: [row] if not row["thumb"] else [])
    calls = []

    def collect(video_id):
        calls.append(video_id)
        if len(calls) == 1:
            raise TimeoutError("temporary")
        return "https://iflv14.sooplive.com/a.jpg"

    def save(video_id, thumbnail):
        row["thumb"] = thumbnail
        return 1

    monkeypatch.setattr(job, "collect_thumbnail", collect)
    monkeypatch.setattr(job, "fill_soop_pick_thumbnail", save)
    assert job.run().records_skipped == 1
    assert row["thumb"] is None
    assert job.run().records_written == 1
    assert job.run().records_written == 0
    assert calls == ["soop:123", "soop:123"]
    assert row["title"] == "Manual title" and row["hidden"] is True
