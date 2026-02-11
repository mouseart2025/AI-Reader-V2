from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/entities", tags=["entities"])


@router.get("/{name}")
async def get_entity(novel_id: str, name: str):
    return {"entity": name}
