from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db.sqlite_db import init_db
from src.db.analysis_task_store import recover_stale_tasks
from src.services.sample_data_service import auto_import_samples
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
    series_bible,
    backup,
    conflicts,
    scenes,
    usage,
)
from src.api.websocket import analysis_ws, chat_ws


async def _restore_persisted_settings() -> None:
    """Restore LLM mode and settings from app_settings on startup."""
    from src.db.sqlite_db import get_connection

    try:
        conn = await get_connection()
        try:
            settings: dict[str, str] = {}
            for key in ("llm_mode", "ollama_default_model", "llm_max_tokens",
                         "cloud_base_url", "cloud_model"):
                row = await conn.execute(
                    "SELECT value FROM app_settings WHERE key=?", (key,),
                )
                result = await row.fetchone()
                if result and result[0]:
                    settings[key] = result[0]
        finally:
            await conn.close()

        if not settings:
            return

        from src.infra import config

        if settings.get("llm_max_tokens"):
            config.update_max_tokens(int(settings["llm_max_tokens"]))

        mode = settings.get("llm_mode", "ollama")
        if mode == "openai":
            from src.infra.secret_store import load_api_key

            api_key = await load_api_key() or ""
            config.update_cloud_config(
                provider="openai",
                api_key=api_key,
                base_url=settings.get("cloud_base_url", ""),
                model=settings.get("cloud_model", ""),
            )
        else:
            model = settings.get("ollama_default_model", "qwen3:8b")
            config.switch_to_ollama(model)
    except Exception:
        pass  # Don't block startup on settings restore errors


async def _detect_context_window() -> None:
    """Detect model context window size after settings are restored."""
    try:
        from src.infra.context_budget import detect_and_update_context_window
        await detect_and_update_context_window()
    except Exception:
        pass  # Don't block startup on detection failure


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _restore_persisted_settings()
    await _detect_context_window()
    await auto_import_samples()
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
app.include_router(chapters.bookmark_router)
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
app.include_router(series_bible.router)
app.include_router(backup.router)
app.include_router(conflicts.router)
app.include_router(scenes.router)
app.include_router(usage.router)

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
