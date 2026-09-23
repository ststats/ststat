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
