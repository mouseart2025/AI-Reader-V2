"""WebSocket endpoint for analysis progress broadcasting."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.services.analysis_service import manager

router = APIRouter()


@router.websocket("/ws/analysis/{novel_id}")
async def analysis_ws(websocket: WebSocket, novel_id: str):
    """Client connects to receive real-time analysis progress for a novel."""
    await manager.connect(novel_id, websocket)
    try:
        while True:
            # Keep connection alive; client may send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(novel_id, websocket)
