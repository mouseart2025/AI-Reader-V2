from fastapi import APIRouter

router = APIRouter(prefix="/api/novels", tags=["novels"])


@router.get("")
async def list_novels():
    return {"novels": []}
