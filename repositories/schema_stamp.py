"""SQL 파일의 스키마 도장: 맨 끝 줄 select public.stamp_schema('이름', '지문');의 지문은 그 줄 앞 내용의 sha256 앞 12자다.

파일을 실행하면 DB schema_stamps(staruniv.sql 12번)에 지문이 남는다. 점검 작업은 DB 지문이 파일과 다르면 멈춘다.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from repositories.supabase import get_supabase

STAMP_LINE = re.compile(r"select public\.stamp_schema\('([a-z-]+)', '([0-9a-f]*)'\);\n?\Z")


def read_stamp(path: Path) -> tuple[str, str, str]:
    """(이름, 적힌 지문, 내용으로 계산한 지문)"""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = STAMP_LINE.search(text)
    if not match:
        raise ValueError(f"{path.name} 맨 끝 줄에 select public.stamp_schema(...)가 없습니다")
    return match[1], match[2], hashlib.sha256(text[: match.start()].encode("utf-8")).hexdigest()[:12]


def applied_stamp(name: str) -> str | None:
    rows = get_supabase().table("schema_stamps").select("stamp").eq("name", name).execute().data
    return rows[0]["stamp"] if rows else None
