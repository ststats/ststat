from pathlib import Path
from unittest.mock import patch

import pytest

from jobs import healthcheck
from repositories.schema_stamp import read_stamp


def test_stamp_matches_content():
    name, written, actual = read_stamp(Path("supabase/ststat.sql"))
    assert name == "ststat"
    assert written == actual, f"ststat.sql 맨 끝 지문을 '{actual}'로 바꾸세요"


def test_healthcheck_stops_when_sql_not_applied():
    _, stamp, _ = read_stamp(healthcheck.SQL)
    with patch.object(healthcheck, "applied_stamp", return_value=stamp):
        assert healthcheck.run().metadata["stamp"] == stamp
    with (
        patch.object(healthcheck, "applied_stamp", return_value="old"),
        pytest.raises(RuntimeError, match="ststat.sql"),
    ):
        healthcheck.run()
