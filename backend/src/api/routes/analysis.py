from fastapi import APIRouter

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{task_id}")
async def get_task(task_id: str):
    return {"task_id": task_id, "status": "pending"}
