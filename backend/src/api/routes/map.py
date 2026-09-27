"""World map data endpoint."""


from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.db import novel_store
from src.services.map_layout_service import terrain_path_for
from src.services.visualization_service import (
    get_analyzed_range,
    get_map_data,
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

    if start > end:
        start, end = end, start

    if first == 0:
        return {
            "locations": [], "trajectories": {},
            "spatial_constraints": [], "layout": [],
            "layout_mode": "hierarchy", "terrain_url": None,
            "analyzed_range": [0, 0],
            "rivers": [], "roads": [], "landmasses": [],
            "shelves": [], "region_boundaries": [],
            "portals": [], "revealed_location_names": [],
            "spatial_scale": "medium", "layer_spatial_scales": {},
            "canvas_size": [1000, 1000],
            "geography_context": None,
            "location_conflicts": [],
            "max_mention_count": 0, "suggested_min_mentions": 1,
            "geo_coords": {}, "world_structure": None,
            "layer_layouts": {}, "quality_metrics": None,
            "space_theme": False,
        }

    data = await get_map_data(novel_id, start, end, layer_id=layer_id)
    data["analyzed_range"] = [first, last]
    return data


class OverrideRequest(BaseModel):
    x: float = 0.0
    y: float = 0.0
    lat: float | None = None
    lng: float | None = None
    constraint_type: str = "position"  # "position" | "locked"
    locked_parent: str | None = None


@router.put("/layout/{location_name}")
async def update_location_override(
    novel_id: str,
    location_name: str,
    body: OverrideRequest,
):
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    await save_user_override(
        novel_id, location_name, body.x, body.y,
        lat=body.lat, lng=body.lng,
        constraint_type=body.constraint_type,
        locked_parent=body.locked_parent,
    )
    return {"status": "ok", "message": "位置已保存"}


def _serve_terrain(novel_id: str):
    """Shared body. Path and URL both come from one place (see `_TERRAIN_VERSION`)."""
    terrain_path = terrain_path_for(novel_id)
    if not terrain_path.exists():
        raise HTTPException(status_code=404, detail="地形图尚未生成")

    return FileResponse(
        str(terrain_path),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/terrain")
async def get_terrain(novel_id: str):
    """Serve the generated terrain PNG image."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return _serve_terrain(novel_id)


@router.get("/terrain/v{version}")
async def get_terrain_versioned(novel_id: str, version: int):
    """Same image, but the version is in the URL.

    Clients get this URL from `map_data.terrain_url`, so a recipe bump changes
    the URL and every cached copy is bypassed on the bump itself. Without it the
    unversioned URL keeps returning the previous recipe's bytes for up to an hour
    (`max-age=3600`) after the fix is live — which is how the field fix below
    reached the endpoint and still did not reach the screen.
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    return _serve_terrain(novel_id)
