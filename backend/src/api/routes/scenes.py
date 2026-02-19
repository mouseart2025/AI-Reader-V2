"""Screenplay mode — scene extraction API."""

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/novels/{novel_id}/scenes", tags=["scenes"])


@router.get("/{chapter_num}")
async def get_chapter_scenes(
    novel_id: str,
    chapter_num: int,
):
    """Get scenes for a single chapter.

    Priority: LLM-extracted scenes (DB) → rule-based fallback.
    """
    from src.db import chapter_fact_store, novel_store
    from src.services.scene_extractor import extract_scenes

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    # Try LLM scenes first (stored in chapter_facts.scenes_json)
    # Need to find the chapter PK (id) from chapter_num
    from src.db import chapter_store
    chapter = await chapter_store.get_chapter_content(novel_id, chapter_num)
    if chapter:
        llm_scenes = await chapter_fact_store.get_chapter_scenes(
            novel_id, chapter["id"],
        )
        if llm_scenes:
            return {
                "chapter": chapter_num,
                "scenes": llm_scenes,
                "scene_count": len(llm_scenes),
                "source": "llm",
            }

    # Fallback to rule-based extraction
    scenes = await extract_scenes(novel_id, chapter_num)
    return {
        "chapter": chapter_num,
        "scenes": scenes,
        "scene_count": len(scenes),
        "source": "rule",
    }


@router.get("")
async def get_scenes_range(
    novel_id: str,
    chapter_start: int = Query(...),
    chapter_end: int = Query(...),
):
    """Get scenes for a range of chapters."""
    from src.db import novel_store
    from src.services.scene_extractor import get_chapter_scenes

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    result = await get_chapter_scenes(novel_id, chapter_start, chapter_end)

    # Flatten for response
    all_scenes = []
    for ch_num in sorted(result.keys()):
        all_scenes.extend(result[ch_num])

    return {
        "chapter_start": chapter_start,
        "chapter_end": chapter_end,
        "chapters": {str(k): v for k, v in result.items()},
        "total_scenes": len(all_scenes),
    }
