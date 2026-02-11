"""Analysis task management endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import analysis_task_store, novel_store
from src.services.analysis_service import get_analysis_service

router = APIRouter(prefix="/api", tags=["analysis"])


class AnalyzeRequest(BaseModel):
    chapter_start: int | None = None
    chapter_end: int | None = None
    force: bool = False  # True to re-analyze already-completed chapters


class PatchTaskRequest(BaseModel):
    status: str  # "paused" | "running" | "cancelled"


@router.post("/novels/{novel_id}/analyze")
async def start_analysis(novel_id: str, req: AnalyzeRequest | None = None):
    """Trigger full or range analysis for a novel."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    chapter_start = 1
    chapter_end = novel["total_chapters"]
    if req:
        if req.chapter_start is not None:
            chapter_start = req.chapter_start
        if req.chapter_end is not None:
            chapter_end = req.chapter_end

    if chapter_start < 1 or chapter_end > novel["total_chapters"] or chapter_start > chapter_end:
        raise HTTPException(status_code=400, detail="无效的章节范围")

    force = req.force if req else False

    service = get_analysis_service()
    try:
        task_id = await service.start(novel_id, chapter_start, chapter_end, force=force)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {"task_id": task_id, "status": "running"}


@router.patch("/analysis/{task_id}")
async def patch_task(task_id: str, req: PatchTaskRequest):
    """Pause, resume, or cancel an analysis task."""
    task = await analysis_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    service = get_analysis_service()
    try:
        if req.status == "paused":
            await service.pause(task_id)
        elif req.status == "running":
            await service.resume(task_id)
        elif req.status == "cancelled":
            await service.cancel(task_id)
        else:
            raise HTTPException(status_code=400, detail=f"无效的状态: {req.status}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"task_id": task_id, "status": req.status}


@router.get("/analysis/{task_id}")
async def get_task(task_id: str):
    """Query task status and progress."""
    task = await analysis_task_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/novels/{novel_id}/analysis/latest")
async def get_latest_task(novel_id: str):
    """Get the most recent analysis task for a novel."""
    task = await analysis_task_store.get_latest_task(novel_id)
    if not task:
        return {"task": None}
    return {"task": task}
