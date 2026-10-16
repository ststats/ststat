from __future__ import annotations

import time

from collectors.soop_vods import collect_member_vods
from models.sync_job import JobResult
from repositories.member_vods import load_active_member_ids, replace_member_vods

REQUEST_DELAY = 0.5


def run() -> JobResult:
    active = load_active_member_ids()
    if not active:
        raise RuntimeError("활동 멤버가 없습니다.")

    rows: list[dict] = []
    scanned: list[str] = []
    errors: list[dict] = []
    for i, soop_id in enumerate(active):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            vods = collect_member_vods(soop_id)
        except Exception as exc:
            errors.append({"soop_id": soop_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        scanned.append(soop_id)
        rows.extend({"soop_id": soop_id, **vod} for vod in vods)

    # 절반 넘게 못 받으면 SOOP 쪽 문제로 보고 아무것도 바꾸지 않는다(못 받은 멤버의 목록은 원래 그대로 둔다)
    if len(scanned) < len(active) / 2:
        raise RuntimeError(f"다시보기 {len(active)}명 중 {len(errors)}명을 받지 못했습니다. errors={errors[:5]}")

    written = replace_member_vods(rows, scanned, active)
    return JobResult(
        records_read=len(rows),
        records_written=written,
        records_skipped=len(errors),
        metadata={"members": len(active), "members_succeeded": len(scanned), "errors": errors[:20]},
    )
