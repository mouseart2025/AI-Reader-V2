"""Reading annotation endpoints (划线 + 批注)."""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db import annotation_store, chapter_store, novel_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels/{novel_id}", tags=["annotations"])

VALID_COLORS = {"yellow", "green", "blue", "pink"}


@router.get("/annotations")
async def list_annotations(
    novel_id: str,
    chapter_num: int | None = Query(default=None),
):
    """List annotations for a novel, optionally filtered by chapter."""
    annotations = await annotation_store.list_annotations(novel_id, chapter_num)
    return {"annotations": annotations}


class AnnotationRequest(BaseModel):
    chapter_num: int
    start_offset: int
    end_offset: int
    anchor_text: str
    color: str = "yellow"
    note: str = ""


@router.post("/annotations")
async def create_annotation(novel_id: str, req: AnnotationRequest):
    """Create an annotation anchored to chapter content offsets.

    The stored offsets are validated against the current chapter content:
    if the text at [start_offset, end_offset) does not match anchor_text,
    we re-anchor by searching anchor_text in the content. Only when the
    anchor text cannot be found at all do we reject the request.
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    if req.color not in VALID_COLORS:
        raise HTTPException(status_code=400, detail="无效的颜色")
    if not req.anchor_text:
        raise HTTPException(status_code=400, detail="锚定文本不能为空")

    chapter = await chapter_store.get_chapter_content(novel_id, req.chapter_num)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    content: str = chapter["content"]
    start, end = req.start_offset, req.end_offset
    if not (0 <= start < end <= len(content)) or content[start:end] != req.anchor_text:
        idx = content.find(req.anchor_text)
        if idx < 0:
            raise HTTPException(
                status_code=400, detail="无法在章节正文中定位所选文本"
            )
        logger.info(
            "Re-anchored annotation for novel %s chapter %d: (%d, %d) -> (%d, %d)",
            novel_id, req.chapter_num, start, end, idx, idx + len(req.anchor_text),
        )
        start, end = idx, idx + len(req.anchor_text)

    return await annotation_store.add_annotation(
        novel_id=novel_id,
        chapter_num=req.chapter_num,
        start_offset=start,
        end_offset=end,
        anchor_text=req.anchor_text,
        color=req.color,
        note=req.note,
    )


# Separate router for update/delete (no novel_id prefix needed)
annotation_router = APIRouter(prefix="/api", tags=["annotations"])


class AnnotationUpdateRequest(BaseModel):
    color: str | None = None
    note: str | None = None


@annotation_router.patch("/annotations/{annotation_id}")
async def update_annotation(annotation_id: int, req: AnnotationUpdateRequest):
    """Update an annotation's color and/or note."""
    if req.color is not None and req.color not in VALID_COLORS:
        raise HTTPException(status_code=400, detail="无效的颜色")
    updated = await annotation_store.update_annotation(
        annotation_id, color=req.color, note=req.note
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="批注不存在")
    return updated


@annotation_router.delete("/annotations/{annotation_id}")
async def remove_annotation(annotation_id: int):
    """Delete an annotation by ID."""
    deleted = await annotation_store.delete_annotation(annotation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="批注不存在")
    return {"ok": True}
