"""Timeline data endpoint."""

from fastapi import APIRouter, HTTPException, Query

from src.db import novel_store
from src.services.visualization_service import get_timeline_data, get_analyzed_range

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("")
async def get_timeline(
    novel_id: str,
    chapter_start: int | None = Query(None),
    chapter_end: int | None = Query(None),
):
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    first, last = await get_analyzed_range(novel_id)
    start = chapter_start if chapter_start is not None else first
    end = chapter_end if chapter_end is not None else last

    if first == 0:
        return {"events": [], "swimlanes": {}, "analyzed_range": [0, 0]}

    data = await get_timeline_data(novel_id, start, end)
    data["analyzed_range"] = [first, last]
    return data
