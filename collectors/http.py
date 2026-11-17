"""수집기 공통 HTTP 설정."""

from __future__ import annotations

# 모든 수집기는 일반 데스크톱 크롬 브라우저처럼 요청한다.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
