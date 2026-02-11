from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/factions", tags=["factions"])


@router.get("")
async def get_factions(novel_id: str):
    return {"orgs": [], "relations": [], "members": {}}
