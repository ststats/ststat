import json
from io import BytesIO
from unittest.mock import patch

import pytest

from collectors.soop_vods import collect_member_vods, parse_vod
from jobs import sync_member_vods as job


def _item(no, **extra):
    return {"title_no": no, "title_name": f"방송 {no}", "reg_date": "2026-10-15 21:00:00",
            "ucc": {"thumb": "//videoimg.sooplive.co.kr/a.jpg", "total_file_duration": 7_205_000},
            "count": {"read_cnt": 321}, **extra}


def test_parse_reads_optional_fields_and_drops_untrusted_thumb():
    assert parse_vod(_item(5)) == {"title_no": 5, "title": "방송 5", "reg_date": "2026-10-15 21:00:00",
                                   "thumb": "https://videoimg.sooplive.co.kr/a.jpg", "duration_sec": 7205, "views": 321}
    bare = parse_vod({**_item(6), "ucc": {"thumb": "https://evil.test/a.jpg"}, "count": None})
    assert bare["thumb"] is None and bare["duration_sec"] is None and bare["views"] is None
    assert parse_vod({**_item(7), "reg_date": "어제"}) is None
    assert parse_vod({**_item(8), "title_name": ""}) is None


def test_collect_reads_first_page_of_replays():
    body = json.dumps({"data": [_item(2), _item(1)]}).encode()
    with patch("collectors.soop_vods.urllib.request.urlopen", return_value=BytesIO(body)) as fetch:
        vods = collect_member_vods("member01")
    assert [v["title_no"] for v in vods] == [2, 1]
    url = fetch.call_args.args[0].full_url
    assert url.startswith("https://chapi.sooplive.com/api/member01/vods/review?")
    assert "per_page=20" in url and "orderby=reg_date" in url


@pytest.mark.parametrize("body", [{"error": 1}, {"data": [{"title_no": "x"}]}])
def test_collect_rejects_changed_format(body):
    with patch("collectors.soop_vods.urllib.request.urlopen", return_value=BytesIO(json.dumps(body).encode())):
        with pytest.raises(ValueError):
            collect_member_vods("member01")


def test_invalid_id_never_sends_request():
    with patch("collectors.soop_vods.urllib.request.urlopen") as fetch:
        with pytest.raises(ValueError):
            collect_member_vods("../x")
    fetch.assert_not_called()


def test_job_keeps_failed_members_and_writes_the_rest(monkeypatch):
    monkeypatch.setattr(job, "REQUEST_DELAY", 0)
    monkeypatch.setattr(job, "load_active_member_ids", lambda: ["a", "b", "c"])

    def collect(soop_id):
        if soop_id == "b":
            raise ValueError("boom")
        return [parse_vod(_item(1))]

    monkeypatch.setattr(job, "collect_member_vods", collect)
    calls = []
    monkeypatch.setattr(job, "replace_member_vods", lambda rows, scanned, active: calls.append((rows, scanned, active)) or 2)
    result = job.run()
    rows, scanned, active = calls[0]
    assert scanned == ["a", "c"] and active == ["a", "b", "c"]
    assert {r["soop_id"] for r in rows} == {"a", "c"}
    assert result.records_written == 2 and result.records_skipped == 1


def test_job_writes_nothing_when_most_members_fail(monkeypatch):
    monkeypatch.setattr(job, "REQUEST_DELAY", 0)
    monkeypatch.setattr(job, "load_active_member_ids", lambda: ["a", "b", "c"])
    monkeypatch.setattr(job, "collect_member_vods", lambda soop_id: (_ for _ in ()).throw(ValueError("down")))
    monkeypatch.setattr(job, "replace_member_vods", lambda *a: pytest.fail("must not write"))
    with pytest.raises(RuntimeError, match="3명을 받지 못했습니다"):
        job.run()
