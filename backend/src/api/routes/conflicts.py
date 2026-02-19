"""Conflict detection API endpoint."""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/novels/{novel_id}/conflicts", tags=["conflicts"])


@router.get("")
async def get_conflicts(
    novel_id: str,
    chapter_start: int | None = Query(None),
    chapter_end: int | None = Query(None),
):
    """Detect and return setting conflicts for a novel."""
    from src.db import novel_store
    from src.services.conflict_detector import detect_conflicts

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    conflicts = await detect_conflicts(
        novel_id,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )

    # Summary counts
    severity_counts = {"严重": 0, "一般": 0, "提示": 0}
    type_counts: dict[str, int] = {}
    for c in conflicts:
        severity_counts[c.get("severity", "")] = severity_counts.get(c.get("severity", ""), 0) + 1
        type_counts[c.get("type", "")] = type_counts.get(c.get("type", ""), 0) + 1

    return {
        "conflicts": conflicts,
        "total": len(conflicts),
        "severity_counts": severity_counts,
        "type_counts": type_counts,
    }
