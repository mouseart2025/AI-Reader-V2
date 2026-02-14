"""Chapter reading and user state endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db import chapter_store, novel_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels/{novel_id}", tags=["chapters"])


@router.get("/chapters")
async def list_chapters(novel_id: str):
    """List all chapters with analysis status and volume info."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapters = await chapter_store.list_chapters(novel_id)
    return {"chapters": chapters}


@router.get("/chapters/{chapter_num}")
async def get_chapter(novel_id: str, chapter_num: int):
    """Get a single chapter's content."""
    chapter = await chapter_store.get_chapter_content(novel_id, chapter_num)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


@router.get("/chapters/{chapter_num}/entities")
async def get_chapter_entities(novel_id: str, chapter_num: int):
    """Get entity names from a chapter's analysis for highlighting."""
    entities = await chapter_store.get_chapter_entities(novel_id, chapter_num)
    return {"entities": entities}


@router.get("/search")
async def search_chapters(
    novel_id: str,
    q: str = Query(..., min_length=1, description="Search keyword"),
):
    """Full-text search across all chapters."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    results = await chapter_store.search_chapters(novel_id, q)
    return {"results": results, "total": len(results)}


class ChapterExcludeRequest(BaseModel):
    chapter_nums: list[int]
    excluded: bool  # True=排除, False=恢复


@router.patch("/chapters/exclude")
async def exclude_chapters(novel_id: str, req: ChapterExcludeRequest):
    """Batch exclude or restore chapters.

    When excluding: sets is_excluded=1 and deletes associated chapter_facts.
    When restoring: sets is_excluded=0 and resets analysis_status to 'pending'.
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    if not req.chapter_nums:
        raise HTTPException(status_code=400, detail="章节列表不能为空")

    await chapter_store.set_chapters_excluded(novel_id, req.chapter_nums, req.excluded)

    if req.excluded:
        deleted = await chapter_store.delete_chapter_facts(novel_id, req.chapter_nums)
        logger.info(
            "Excluded chapters %s for novel %s, deleted %d facts",
            req.chapter_nums, novel_id, deleted,
        )

    chapters = await chapter_store.list_chapters(novel_id)
    return {"chapters": chapters}


class UserStateRequest(BaseModel):
    last_chapter: int
    scroll_position: float = 0.0
    chapter_range: str | None = None


@router.get("/user-state")
async def get_user_state(novel_id: str):
    """Get the user's reading state for a novel."""
    state = await chapter_store.get_user_state(novel_id)
    if not state:
        return {"novel_id": novel_id, "last_chapter": None, "scroll_position": 0.0}
    return state


@router.put("/user-state")
async def save_user_state(novel_id: str, req: UserStateRequest):
    """Save or update the user's reading position."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    await chapter_store.save_user_state(
        novel_id=novel_id,
        last_chapter=req.last_chapter,
        scroll_position=req.scroll_position,
        chapter_range=req.chapter_range,
    )
    return {"ok": True}
