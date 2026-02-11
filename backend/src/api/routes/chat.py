from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/chat", tags=["chat"])


@router.post("")
async def chat(novel_id: str):
    return {"message": "chat stub"}
