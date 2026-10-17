import json
from io import BytesIO
from unittest.mock import patch

import pytest

from collectors import member_youtube
from collectors.soop_videos import collect_member_vods, parse_vod
from jobs import sync_member_videos as job


def _item(no, **extra):
    return {
        "title_no": no,
        "title_name": f"방송 {no}",
        "reg_date": "2026-10-15 21:00:00",
        "ucc": {"thumb": "//videoimg.sooplive.co.kr/a.jpg", "total_file_duration": 7_205_000},
        "count": {"read_cnt": 321},
        **extra,
    }


def test_parse_vod_reads_optional_fields_and_drops_untrusted_thumb():
    assert parse_vod(_item(5)) == {
        "video_id": "5",
        "title": "방송 5",
        "published": "2026-10-15T21:00:00+09:00",
        "thumb": "https://videoimg.sooplive.co.kr/a.jpg",
        "duration_sec": 7205,
        "views": 321,
    }
    bare = parse_vod({**_item(6), "ucc": {"thumb": "https://evil.test/a.jpg"}, "count": None})
    assert bare["thumb"] is None and bare["duration_sec"] is None and bare["views"] is None
    assert parse_vod({**_item(7), "reg_date": "어제"}) is None
    assert parse_vod({**_item(9), "reg_date": "2026-02-30 00:00:00"}) is None  # 모양만 맞는 없는 날짜
    assert parse_vod({**_item(8), "title_name": ""}) is None


def test_collect_vods_reads_first_page_of_replays():
    body = json.dumps({"data": [_item(2), _item(1)]}).encode()
    with patch("collectors.soop_videos.urllib.request.urlopen", return_value=BytesIO(body)) as fetch:
        vods = collect_member_vods("member01", 20)
    assert [v["video_id"] for v in vods] == ["2", "1"]
    url = fetch.call_args.args[0].full_url
    assert url.startswith("https://chapi.sooplive.com/api/member01/vods/review?")
    assert "per_page=20" in url and "orderby=reg_date" in url


@pytest.mark.parametrize("body", [{"error": 1}, {"data": [{"title_no": "x"}]}])
def test_collect_vods_rejects_changed_format(body):
    with patch("collectors.soop_videos.urllib.request.urlopen", return_value=BytesIO(json.dumps(body).encode())):
        with pytest.raises(ValueError):
            collect_member_vods("member01", 20)


def test_invalid_soop_id_never_sends_request():
    with patch("collectors.soop_videos.urllib.request.urlopen") as fetch:
        with pytest.raises(ValueError):
            collect_member_vods("../x", 20)
    fetch.assert_not_called()


def test_youtube_keeps_latest_20_with_shorts_and_reuses_known_flags(monkeypatch):
    items = [
        {
            "id": f"v{n:010d}",
            "title": f"영상 {n}",
            "published": f"2026-10-{1 + n % 28:02d}T00:00:{n % 60:02d}Z",
            "thumb": "https://i.ytimg.com/x.jpg",
            "views": n,
            "short": None if n % 2 else False,
        }
        for n in range(30)
    ]
    monkeypatch.setattr(member_youtube, "collect_channel", lambda url, cached, archived: ({}, items, "api"))
    checked = []
    monkeypatch.setattr("collectors.youtube_videos.is_short", lambda vid: checked.append(vid) or True)
    monkeypatch.setattr("collectors.youtube_videos.DELAY", 0)
    known = {items[29]["id"]: {"short": False}}
    videos = member_youtube.collect_member_youtube("https://www.youtube.com/@a", known, 20)
    newest = sorted(items, key=lambda i: i["published"], reverse=True)[:20]
    assert [v["video_id"] for v in videos] == [i["id"] for i in newest]
    assert items[29]["id"] not in checked
    assert all(v["short"] is (bool(int(v["video_id"][1:]) % 2) and v["video_id"] != items[29]["id"]) for v in videos)


def _members(monkeypatch, members):
    monkeypatch.setattr(job, "REQUEST_DELAY", 0)
    monkeypatch.setattr(job, "load_active_members", lambda: members)
    monkeypatch.setattr(job, "load_youtube_short_flags", lambda: {})


def test_job_writes_each_kind_and_keeps_failed_members(monkeypatch):
    _members(
        monkeypatch,
        [
            {"soop_id": "a", "youtube_url": "https://www.youtube.com/@a"},
            {"soop_id": "b", "youtube_url": ""},
            {"soop_id": "c", "youtube_url": ""},
        ],
    )

    def vods(soop_id, limit):
        if soop_id == "b":
            raise ValueError("boom")
        return [parse_vod(_item(1))]

    monkeypatch.setattr(job, "collect_member_vods", vods)
    monkeypatch.setattr(job, "collect_member_youtube", lambda url, shorts, limit: [{"video_id": "y", "title": "t"}])
    calls = {}
    monkeypatch.setattr(
        job,
        "replace_member_videos",
        lambda kind, rows, scanned, active: calls.setdefault(kind, (rows, scanned, active)) and 1,
    )
    result = job.run()
    rows, scanned, active = calls["soop"]
    assert scanned == ["a", "c"] and active == ["a", "b", "c"] and {r["soop_id"] for r in rows} == {"a", "c"}
    # 유튜브는 주소를 등록한 멤버만 대상이다(주소를 지운 멤버의 목록은 지워진다)
    assert calls["youtube"] == ([{"soop_id": "a", "video_id": "y", "title": "t"}], ["a"], ["a"])
    assert result.records_skipped == 1


def test_job_fails_but_keeps_the_kind_that_could_not_be_read(monkeypatch):
    _members(
        monkeypatch,
        [{"soop_id": "a", "youtube_url": "https://www.youtube.com/@a"}, {"soop_id": "b", "youtube_url": ""}],
    )
    monkeypatch.setattr(job, "collect_member_vods", lambda soop_id, limit: (_ for _ in ()).throw(ValueError("down")))
    monkeypatch.setattr(job, "collect_member_youtube", lambda url, shorts, limit: [{"video_id": "y", "title": "t"}])
    written = []
    monkeypatch.setattr(job, "replace_member_videos", lambda kind, *a: written.append(kind) or 0)
    with pytest.raises(RuntimeError, match="soop 2명 중 2명을 받지 못해"):
        job.run()
    assert written == ["youtube"]


@pytest.mark.parametrize(
    "value,url",
    [
        ("@abc.d-e", "https://www.youtube.com/@abc.d-e"),
        ("youtube.com/@x", "https://youtube.com/@x"),
        ("https://m.youtube.com/channel/UC1", "https://m.youtube.com/channel/UC1"),
    ],
)
def test_channel_url_accepts_handle_and_address(value, url):
    assert member_youtube.channel_url(value) == url


@pytest.mark.parametrize("value", ["https://evil.test/@x", "https://youtu.be/abc", ""])
def test_channel_url_rejects_non_channel(value):
    with pytest.raises(ValueError):
        member_youtube.channel_url(value)


def test_job_does_not_wipe_a_kind_when_everyone_comes_back_empty(monkeypatch):
    _members(monkeypatch, [{"soop_id": "a", "youtube_url": ""}, {"soop_id": "b", "youtube_url": ""}])
    monkeypatch.setattr(job, "collect_member_vods", lambda soop_id, limit: [])
    written = []
    monkeypatch.setattr(job, "replace_member_videos", lambda kind, *a: written.append(kind) or 0)
    with pytest.raises(RuntimeError, match="모두 빈 목록"):
        job.run()
    assert "soop" not in written


def test_job_drops_non_channel_youtube_address(monkeypatch):
    _members(
        monkeypatch, [{"soop_id": "a", "youtube_url": "@a"}, {"soop_id": "b", "youtube_url": "https://youtu.be/xyz"}]
    )
    monkeypatch.setattr(job, "collect_member_vods", lambda soop_id, limit: [parse_vod(_item(1))])
    seen = []
    monkeypatch.setattr(
        job, "collect_member_youtube", lambda url, shorts, limit: seen.append(url) or [{"video_id": "y", "title": "t"}]
    )
    calls = {}
    monkeypatch.setattr(
        job, "replace_member_videos", lambda kind, rows, scanned, active: calls.setdefault(kind, active) and 1
    )
    result = job.run()
    # 채널이 아닌 주소의 멤버는 대상에서 빠져 예전 유튜브 목록이 지워진다
    assert seen == ["https://www.youtube.com/@a"] and calls["youtube"] == ["a"]
    assert result.metadata["youtube"]["invalid_urls"] == [{"soop_id": "b", "youtube_url": "https://youtu.be/xyz"}]


def test_youtube_drops_videos_without_a_valid_time(monkeypatch):
    items = [
        {"id": "v0000000001", "title": "a", "published": "2026-10-01T00:00:00Z", "views": 1, "short": False},
        {"id": "v0000000002", "title": "b", "published": "", "views": 1, "short": False},
    ]
    monkeypatch.setattr(member_youtube, "collect_channel", lambda url, cached, archived: ({}, items, "api"))
    assert [v["video_id"] for v in member_youtube.collect_member_youtube("https://www.youtube.com/@a", {}, 20)] == [
        "v0000000001"
    ]
