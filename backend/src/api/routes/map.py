from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/map", tags=["map"])


@router.get("")
async def get_map(novel_id: str):
    return {"locations": [], "trajectories": {}}
