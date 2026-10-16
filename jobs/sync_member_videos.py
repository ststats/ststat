from __future__ import annotations

import time

from collectors.member_youtube import collect_member_youtube
from collectors.soop_vods import collect_member_vods
from models.sync_job import JobResult
from repositories.member_videos import load_active_members, load_youtube_short_flags, replace_member_videos

REQUEST_DELAY = 0.5


def _sync(kind: str, targets: dict[str, str], collect) -> dict:
    """targets: SOOP ID → 수집 대상(SOOP ID 또는 유튜브 주소). 절반 넘게 못 받으면 이 종류는 바꾸지 않는다."""
    rows: list[dict] = []
    scanned: list[str] = []
    errors: list[dict] = []
    for i, (soop_id, source) in enumerate(targets.items()):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            videos = collect(source)
        except Exception as exc:
            errors.append({"soop_id": soop_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        scanned.append(soop_id)
        rows.extend({"soop_id": soop_id, **video} for video in videos)
    if len(scanned) < len(targets) / 2:
        return {"members": len(targets), "succeeded": len(scanned), "written": 0, "errors": errors[:20],
                "failed": f"{kind} {len(targets)}명 중 {len(errors)}명을 받지 못해 바꾸지 않았습니다."}
    # 못 받은 멤버의 목록은 그대로 두고, 대상에서 빠진 멤버(퇴단·유튜브 주소 삭제)의 목록은 지운다
    written = replace_member_videos(kind, rows, scanned, list(targets))
    return {"members": len(targets), "succeeded": len(scanned), "read": len(rows), "written": written,
            "errors": errors[:20]}


def run() -> JobResult:
    members = load_active_members()
    if not members:
        raise RuntimeError("활동 멤버가 없습니다.")
    shorts = load_youtube_short_flags()
    results = {
        "soop": _sync("soop", {m["soop_id"]: m["soop_id"] for m in members}, collect_member_vods),
        "youtube": _sync("youtube", {m["soop_id"]: m["youtube_url"] for m in members if m["youtube_url"]},
                         lambda url: collect_member_youtube(url, shorts)),
    }
    failed = [r["failed"] for r in results.values() if r.get("failed")]
    if failed:
        raise RuntimeError(" ".join(failed) + f" results={results}")
    return JobResult(
        records_read=sum(r.get("read", 0) for r in results.values()),
        records_written=sum(r["written"] for r in results.values()),
        records_skipped=sum(len(r["errors"]) for r in results.values()),
        metadata=results,
    )
