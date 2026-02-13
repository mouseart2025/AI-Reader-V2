from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db.sqlite_db import init_db
from src.db.analysis_task_store import recover_stale_tasks
from src.api.routes import (
    novels,
    chapters,
    entities,
    graph,
    map,
    timeline,
    factions,
    chat,
    analysis,
    settings,
    encyclopedia,
    export_import,
    world_structure,
    prescan,
)
from src.api.websocket import analysis_ws, chat_ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Recover tasks left in 'running' state from a previous server session
    await recover_stale_tasks()
    yield


app = FastAPI(title="AI Reader V2", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
app.include_router(novels.router)
app.include_router(chapters.router)
app.include_router(entities.router)
app.include_router(graph.router)
app.include_router(map.router)
app.include_router(timeline.router)
app.include_router(factions.router)
app.include_router(chat.router)
app.include_router(analysis.router)
app.include_router(settings.router)
app.include_router(encyclopedia.router)
app.include_router(export_import.router)
app.include_router(world_structure.router)
app.include_router(prescan.router)

# WebSocket routes
app.include_router(analysis_ws.router)
app.include_router(chat_ws.router)


@app.get("/api/health")
async def health():
    from src.infra.config import LLM_PROVIDER, get_model_name
    return {
        "status": "ok",
        "llm_provider": LLM_PROVIDER,
        "llm_model": get_model_name(),
    }
