"""World map data endpoint."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.db import novel_store
from src.services.visualization_service import (
    get_map_data,
    get_analyzed_range,
    save_user_override,
)

router = APIRouter(prefix="/api/novels/{novel_id}/map", tags=["map"])


@router.get("")
async def get_map(
    novel_id: str,
    chapter_start: int | None = Query(None),
    chapter_end: int | None = Query(None),
    layer_id: str | None = Query(None),
):
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    first, last = await get_analyzed_range(novel_id)
    start = chapter_start if chapter_start is not None else first
    end = chapter_end if chapter_end is not None else last

    if first == 0:
        return {
            "locations": [], "trajectories": {},
            "spatial_constraints": [], "layout": [],
            "layout_mode": "hierarchy", "terrain_url": None,
            "analyzed_range": [0, 0],
        }

    data = await get_map_data(novel_id, start, end, layer_id=layer_id)
    data["analyzed_range"] = [first, last]
    return data


class OverrideRequest(BaseModel):
    x: float = 0.0
    y: float = 0.0
    lat: float | None = None
    lng: float | None = None


@router.put("/layout/{location_name}")
async def update_location_override(
    novel_id: str,
    location_name: str,
    body: OverrideRequest,
):
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    await save_user_override(novel_id, location_name, body.x, body.y,
                             lat=body.lat, lng=body.lng)
    return {"status": "ok", "message": "位置已保存"}


@router.get("/terrain")
async def get_terrain(novel_id: str):
    """Serve the generated terrain PNG image."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    from src.infra.config import DATA_DIR
    terrain_path = DATA_DIR / "maps" / novel_id / "terrain.png"
    if not terrain_path.exists():
        raise HTTPException(status_code=404, detail="地形图尚未生成")

    return FileResponse(
        str(terrain_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
