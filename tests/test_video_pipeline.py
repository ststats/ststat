from collectors.youtube_videos import iso_duration_sec
from unittest.mock import MagicMock, patch

from repositories.videos import upsert_collected_videos


def test_iso_duration():
    assert iso_duration_sec("PT1H2M3S") == 3723
    assert iso_duration_sec("PT2M59S") == 179
    assert iso_duration_sec("PT45S") == 45


def test_invalid_duration_is_zero():
    assert iso_duration_sec("") == 0
    assert iso_duration_sec("bad") == 0


def test_video_upsert_never_writes_admin_hidden_field():
    db = MagicMock()
    with patch("repositories.videos.get_supabase", return_value=db):
        upsert_collected_videos("channel", [{"id": "v1", "title": "T"}], {})
    payload = db.table.return_value.upsert.call_args.args[0]
    assert "hidden" not in payload[0]


def test_iso_duration_counts_days():
    assert iso_duration_sec("P1DT2H") == 26 * 3600
    assert iso_duration_sec("P2D") == 2 * 86400


def test_short_check_failure_is_unknown_and_not_saved_as_false(monkeypatch):
    from collectors import youtube_videos as yt
    monkeypatch.setattr(yt, "DELAY", 0)
    responses = {"a": 503, "b": 303, "c": 200}
    monkeypatch.setattr(yt, "http_get", lambda url, **kw: (responses[url.rsplit("/", 1)[1]], ""))
    assert yt.is_short("a") is None
    assert yt.is_short("b") is False
    assert yt.is_short("c") is True
    items = [{"id": "a", "short": None}, {"id": "b", "short": None}, {"id": "c", "short": None},
             {"id": "d", "short": None}]
    responses["d"] = 0
    out = yt.resolve_short_flags(items, previous={"d": {"short": True}})
    # a: 판별 실패 → 이번엔 저장하지 않음(다음 실행에 다시 판별), d: 저장된 값 사용
    assert {x["id"]: x["short"] for x in out} == {"b": False, "c": True, "d": True}


def test_missing_view_count_keeps_existing_value():
    db = MagicMock()
    with patch("repositories.videos.get_supabase", return_value=db):
        upsert_collected_videos("ch", [{"id": "v1", "title": "T", "views": None, "short": False},
                                       {"id": "v2", "title": "T", "views": 7, "short": False}],
                                {"v1": {"views": 1234}})
    payload = db.table.return_value.upsert.call_args.args[0]
    assert {p["id"]: p["views"] for p in payload} == {"v1": 1234, "v2": 7}


def test_api_detail_missing_video_is_not_zeroed(monkeypatch):
    from collectors import youtube_videos as yt
    monkeypatch.setattr(yt, "API_KEY", "k")
    monkeypatch.setattr(yt, "api_channel", lambda url, cached: {"id": "UC", "uploads": "UU"})
    monkeypatch.setattr(yt, "api_uploads", lambda pid, pages: [{"id": "v1"}, {"id": "v2"}])
    monkeypatch.setattr(yt, "api_stats", lambda ids: {"v2": (50, 600)})
    _, videos, method = yt.collect_channel("https://youtube.com/@x", {}, 5)
    assert method == "api"
    assert {v["id"]: (v["views"], v["short"]) for v in videos} == {"v1": (None, None), "v2": (50, False)}


def test_unknown_short_is_rechecked_on_the_next_run_and_stored_correctly(monkeypatch):
    """1회차: 쇼츠 판별 실패(503) → DB에 저장하지 않음. 2회차: 다시 판별(200) → short=True로 저장."""
    from jobs import sync_videos as job
    from collectors import youtube_videos as yt

    db_videos: dict[str, dict] = {}
    monkeypatch.setattr(job, "load_active_channels", lambda: [{"channel_url": "https://youtube.com/@x"}])
    monkeypatch.setattr(job, "load_existing_videos", lambda: {k: dict(v) for k, v in db_videos.items()})
    monkeypatch.setattr(job, "update_channel_metadata", lambda url, info: None)

    def upsert(url, items, existing):
        for it in items:
            db_videos[it["id"]] = {"id": it["id"], "channel_url": url, "short": it["short"], "views": it.get("views")}
        return len(items)
    monkeypatch.setattr(job, "upsert_collected_videos", upsert)
    monkeypatch.setattr(job, "collect_channel", lambda url, ch, n, full=False: (
        {"id": "UC"}, [{"id": "vid00000001", "views": 5, "short": None},
                       {"id": "vid00000002", "views": 9, "short": False}], "api"))
    monkeypatch.setattr(yt, "DELAY", 0)
    status = {"code": 503}
    monkeypatch.setattr(yt, "http_get", lambda url, **kw: (status["code"], ""))

    job.run()
    assert "vid00000001" not in db_videos          # 모름 → 저장 안 함(False로 굳지 않음)
    assert db_videos["vid00000002"]["short"] is False

    status["code"] = 200
    job.run()
    assert db_videos["vid00000001"]["short"] is True
