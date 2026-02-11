from fastapi import APIRouter

router = APIRouter(prefix="/api/novels/{novel_id}/graph", tags=["graph"])


@router.get("")
async def get_graph(novel_id: str):
    return {"nodes": [], "edges": []}
