from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


@router.get("")
async def list_chapters(novel_id: str):
    return {"chapters": []}
