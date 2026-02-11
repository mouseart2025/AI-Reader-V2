from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/timeline", tags=["timeline"])


@router.get("")
async def get_timeline(novel_id: str):
    return {"events": [], "swimlanes": {}}
