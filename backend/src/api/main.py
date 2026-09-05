from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.db.sqlite_db import init_db
from src.db.analysis_task_store import recover_stale_tasks
from src.db.analysis_pass_store import recover_stale_passes
from src.infra.version import BACKEND_VERSION
from src.services.sample_data_service import auto_import_samples
from src.api.routes import (
    novels,
    chapters,
    annotations,
    entities,
    entity_overrides,
    graph,
    map,
    timeline,
    factions,
    chat,
    analysis,
    analysis_passes,
    audit_log,
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
    # 同理恢复二审 pass(multi-pass Epic 2): running → paused,供续跑
    await recover_stale_passes()
    yield


app = FastAPI(title="AI Reader V2", version=BACKEND_VERSION, lifespan=lifespan)


class SidecarAuthMiddleware:
    """V-01 修复：配置了 sidecar 令牌时，/api/* 与 /ws/* 一律要求鉴权。

    背景：桌面端 sidecar 监听 loopback 随机端口，但 loopback 绑定与 CORS 都不是
    认证手段——同机任意进程都能直接请求。Tauri 宿主每次启动生成随机令牌并经
    环境变量传入（src.infra.config.SIDECAR_TOKEN）；未配置（web 直跑/开发）全放行。

    - HTTP：要求 ``Authorization: Bearer <token>``
    - WebSocket（浏览器/WebView 无法自定义头）：要求 ``?token=<token>``
    - 豁免：``/api/health``（宿主健康检查，无敏感信息）与 OPTIONS（CORS 预检）
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        from src.infra import config  # 运行时读取，便于测试注入
        token = config.SIDECAR_TOKEN
        path = scope.get("path", "")
        if (
            not token
            or path == "/api/health"
            or not (path.startswith("/api/") or path.startswith("/ws/"))
            or (scope["type"] == "http" and scope.get("method") == "OPTIONS")
        ):
            return await self.app(scope, receive, send)

        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() == f"Bearer {token}":
                return await self.app(scope, receive, send)
            resp = JSONResponse(
                {"detail": "未授权：缺少或无效的 sidecar 令牌"}, status_code=401
            )
            return await resp(scope, receive, send)

        # websocket
        query = parse_qs(scope.get("query_string", b"").decode())
        if query.get("token", [""])[0] == token:
            return await self.app(scope, receive, send)
        from starlette.websockets import WebSocket
        await WebSocket(scope, receive, send).close(code=4401)


app.add_middleware(SidecarAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?|tauri)://((tauri\.)?localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
app.include_router(novels.router)
app.include_router(chapters.router)
app.include_router(chapters.bookmark_router)
app.include_router(annotations.router)
app.include_router(annotations.annotation_router)
app.include_router(entities.router)
app.include_router(entity_overrides.router)
app.include_router(graph.router)
app.include_router(map.router)
app.include_router(timeline.router)
app.include_router(factions.router)
app.include_router(chat.router)
app.include_router(analysis.router)
app.include_router(analysis_passes.router)
app.include_router(audit_log.router)
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
        "version": BACKEND_VERSION,
        "llm_provider": LLM_PROVIDER,
        "llm_model": get_model_name(),
    }
