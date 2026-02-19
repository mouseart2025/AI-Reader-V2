"""Settings and environment health-check endpoints."""

import asyncio
import json
import os
import platform
import shutil
import subprocess

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.infra.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    get_model_name,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

REQUIRED_MODEL = "qwen2.5:7b"

# ── Model recommendation catalog ──────────────────

MODEL_CATALOG = [
    {
        "name": "qwen3:4b",
        "display_name": "Qwen3 4B",
        "size_gb": 2.5,
        "min_ram_gb": 8,
        "description": "轻量模型，速度快，适合快速分析",
    },
    {
        "name": "qwen3:8b",
        "display_name": "Qwen3 8B",
        "size_gb": 5.0,
        "min_ram_gb": 16,
        "description": "平衡质量与速度，推荐大多数用户使用",
    },
    {
        "name": "qwen3:14b",
        "display_name": "Qwen3 14B",
        "size_gb": 9.0,
        "min_ram_gb": 32,
        "description": "最佳分析质量，适合高配机器",
    },
]


# ── Cloud provider presets ────────────────────────

CLOUD_PROVIDERS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    {
        "id": "custom",
        "name": "自定义",
        "base_url": "",
        "default_model": "",
    },
]


class PullModelRequest(BaseModel):
    model: str


class SetDefaultModelRequest(BaseModel):
    model: str


class CloudConfigRequest(BaseModel):
    provider: str
    base_url: str
    model: str
    api_key: str


class ValidateCloudRequest(BaseModel):
    base_url: str
    api_key: str


class SwitchModeRequest(BaseModel):
    mode: str  # "ollama" or "openai"
    ollama_model: str | None = None


class AdvancedSettingsRequest(BaseModel):
    max_tokens: int


class BudgetRequest(BaseModel):
    monthly_budget_cny: float


@router.get("")
async def get_settings():
    from src.infra import config

    return {
        "settings": {
            "llm_provider": config.LLM_PROVIDER,
            "llm_model": get_model_name(),
            "ollama_base_url": OLLAMA_BASE_URL,
            "ollama_model": config.OLLAMA_MODEL,
            "required_model": REQUIRED_MODEL,
            "max_tokens": config.LLM_MAX_TOKENS,
        }
    }


@router.get("/health-check")
async def health_check():
    """Check LLM connectivity — always returns both Ollama and cloud status."""
    from src.infra import config

    ollama_result = await _check_ollama()
    openai_result = await _check_openai()
    # Merge: ollama fields as base, overlay cloud fields, set active provider
    merged = {**ollama_result, **openai_result}
    merged["llm_provider"] = config.LLM_PROVIDER
    merged["llm_model"] = config.get_model_name()
    merged["llm_base_url"] = config.LLM_BASE_URL
    return merged


@router.post("/ollama/start")
async def start_ollama():
    """Attempt to start Ollama and wait for it to become available."""
    if shutil.which("ollama") is None:
        return {"success": False, "error": "Ollama 未安装"}

    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(
                ["open", "-a", "Ollama"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        return {"success": False, "error": f"启动失败: {str(e)[:200]}"}

    # Poll up to 5 seconds for Ollama to become reachable
    for _ in range(10):
        await asyncio.sleep(0.5)
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
                if resp.status_code == 200:
                    return {"success": True}
        except Exception:
            pass

    return {"success": False, "error": "Ollama 已启动但未在 5 秒内就绪"}


def _get_total_ram_gb() -> float:
    """Get total system RAM in GB using os.sysconf (macOS/Linux)."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return round(total / (1024**3), 1)
    except (ValueError, OSError):
        return 0.0


@router.get("/hardware")
async def get_hardware():
    """Return system hardware info for model recommendations."""
    return {
        "total_ram_gb": _get_total_ram_gb(),
        "platform": platform.system(),
        "arch": platform.machine(),
    }


@router.get("/ollama/recommendations")
async def get_model_recommendations():
    """Return recommended models based on system RAM."""
    ram_gb = _get_total_ram_gb()

    # Determine which models are already installed
    installed_names: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    installed_names.add(m.get("name", ""))
    except Exception:
        pass

    recommendations = []
    for model in MODEL_CATALOG:
        if model["min_ram_gb"] > ram_gb and ram_gb > 0:
            continue
        recommended = False
        if ram_gb >= 32 and model["name"] == "qwen3:14b":
            recommended = True
        elif 16 <= ram_gb < 32 and model["name"] == "qwen3:8b":
            recommended = True
        elif ram_gb < 16 and model["name"] == "qwen3:4b":
            recommended = True

        installed = any(
            n == model["name"] or n.startswith(model["name"].split(":")[0] + ":")
            and n.endswith(model["name"].split(":")[1])
            for n in installed_names
        )

        recommendations.append({
            **model,
            "recommended": recommended,
            "installed": installed,
        })

    return {
        "total_ram_gb": ram_gb,
        "recommendations": recommendations,
    }


@router.post("/ollama/pull")
async def pull_ollama_model(req: PullModelRequest):
    """Pull an Ollama model with SSE streaming progress."""

    async def event_stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/pull",
                    json={"name": req.model, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                            if data.get("status") == "success":
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'error': str(e)[:200]})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ollama/default-model")
async def set_default_model(req: SetDefaultModelRequest):
    """Set the default Ollama model for analysis."""
    from src.db.sqlite_db import get_connection
    from src.infra import config

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('ollama_default_model', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (req.model,),
        )
        await conn.commit()
    finally:
        await conn.close()

    # Also update runtime config
    config.OLLAMA_MODEL = req.model

    return {"success": True, "model": req.model}


# ── Cloud LLM configuration ─────────────────────────


@router.get("/cloud/providers")
async def get_cloud_providers():
    """Return list of cloud LLM provider presets."""
    return {"providers": CLOUD_PROVIDERS}


@router.get("/cloud/config")
async def get_cloud_config():
    """Return current cloud LLM configuration (API key masked)."""
    from src.db.sqlite_db import get_connection
    from src.infra.secret_store import load_api_key

    # Load provider/model/base_url from app_settings
    config = {"provider": "", "base_url": "", "model": "", "has_api_key": False}
    conn = await get_connection()
    try:
        for key in ("cloud_provider", "cloud_base_url", "cloud_model"):
            row = await conn.execute(
                "SELECT value FROM app_settings WHERE key=?",
                (key,),
            )
            result = await row.fetchone()
            short_key = key.replace("cloud_", "")
            config[short_key] = result[0] if result else ""
    finally:
        await conn.close()

    api_key = await load_api_key()
    config["has_api_key"] = bool(api_key)
    if api_key:
        config["api_key_masked"] = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"
    else:
        config["api_key_masked"] = ""

    return config


@router.post("/cloud/config")
async def save_cloud_config(req: CloudConfigRequest):
    """Save cloud LLM configuration and update runtime config."""
    from src.db.sqlite_db import get_connection
    from src.infra.secret_store import save_api_key

    # Save API key securely
    storage = await save_api_key(req.api_key)

    # Save provider/model/base_url to app_settings
    conn = await get_connection()
    try:
        for key, value in [
            ("cloud_provider", req.provider),
            ("cloud_base_url", req.base_url),
            ("cloud_model", req.model),
        ]:
            await conn.execute(
                """INSERT INTO app_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value),
            )
        await conn.commit()
    finally:
        await conn.close()

    # Hot-update runtime config
    from src.infra.config import update_cloud_config

    update_cloud_config(
        provider="openai",
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
    )

    return {"success": True, "storage": storage}


@router.post("/cloud/validate")
async def validate_cloud_api(req: ValidateCloudRequest):
    """Test cloud LLM API connectivity with provided credentials."""
    if not req.api_key or not req.base_url:
        return {"valid": False, "error": "API Key 和 Base URL 不能为空"}

    try:
        base = req.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {req.api_key}"}
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            if resp.status_code == 200:
                return {"valid": True}
            elif resp.status_code == 401:
                return {"valid": False, "error": "API Key 无效（401 Unauthorized）"}
            else:
                return {"valid": False, "error": f"服务器返回 {resp.status_code}"}
    except httpx.ConnectError:
        return {"valid": False, "error": f"无法连接到 {req.base_url}"}
    except httpx.TimeoutException:
        return {"valid": False, "error": "连接超时（10秒）"}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


# ── Mode switching & advanced settings ──────────────


@router.post("/llm-mode")
async def switch_llm_mode(req: SwitchModeRequest):
    """Switch between Ollama and cloud LLM mode."""
    from src.db.sqlite_db import get_connection

    if req.mode not in ("ollama", "openai"):
        return {"success": False, "error": "无效模式，请选择 ollama 或 openai"}

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('llm_mode', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (req.mode,),
        )
        await conn.commit()
    finally:
        await conn.close()

    if req.mode == "ollama":
        from src.infra.config import switch_to_ollama

        model = req.ollama_model or "qwen3:8b"
        switch_to_ollama(model)
    else:
        # Cloud mode — load saved config
        from src.infra.config import update_cloud_config
        from src.infra.secret_store import load_api_key

        conn = await get_connection()
        try:
            cloud_cfg: dict[str, str] = {}
            for key in ("cloud_base_url", "cloud_model"):
                row = await conn.execute(
                    "SELECT value FROM app_settings WHERE key=?",
                    (key,),
                )
                result = await row.fetchone()
                cloud_cfg[key] = result[0] if result else ""
        finally:
            await conn.close()

        api_key = await load_api_key() or ""
        update_cloud_config(
            provider="openai",
            api_key=api_key,
            base_url=cloud_cfg.get("cloud_base_url", ""),
            model=cloud_cfg.get("cloud_model", ""),
        )

    return {"success": True, "mode": req.mode}


@router.get("/running-tasks")
async def get_running_tasks():
    """Return the count of currently running analysis tasks."""
    from src.services.analysis_service import get_analysis_service

    service = get_analysis_service()
    return {"running_count": len(service._active_loops)}


@router.post("/advanced")
async def save_advanced_settings(req: AdvancedSettingsRequest):
    """Save advanced LLM settings."""
    from src.db.sqlite_db import get_connection
    from src.infra.config import update_max_tokens

    if req.max_tokens < 1024 or req.max_tokens > 131072:
        return {"success": False, "error": "max_tokens 需在 1024~131072 范围内"}

    conn = await get_connection()
    try:
        await conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES ('llm_max_tokens', ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (str(req.max_tokens),),
        )
        await conn.commit()
    finally:
        await conn.close()

    update_max_tokens(req.max_tokens)
    return {"success": True, "max_tokens": req.max_tokens}


@router.post("/restore-defaults")
async def restore_defaults():
    """Restore LLM config to defaults: local Ollama + qwen3:8b."""
    from src.db.sqlite_db import get_connection
    from src.infra.config import switch_to_ollama, update_max_tokens

    # Clear persisted LLM settings
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM app_settings WHERE key IN "
            "('llm_mode', 'ollama_default_model', 'llm_max_tokens')",
        )
        await conn.commit()
    finally:
        await conn.close()

    switch_to_ollama("qwen3:8b")
    update_max_tokens(8192)
    return {"success": True}


@router.get("/budget")
async def get_budget():
    """Get monthly budget and current month usage."""
    from src.services.cost_service import get_monthly_budget, get_monthly_usage

    budget = await get_monthly_budget()
    usage = await get_monthly_usage()
    return {
        "monthly_budget_cny": budget,
        "monthly_used_cny": usage.get("cny", 0.0),
        "monthly_used_usd": usage.get("usd", 0.0),
        "monthly_input_tokens": usage.get("input_tokens", 0),
        "monthly_output_tokens": usage.get("output_tokens", 0),
    }


@router.post("/budget")
async def save_budget(req: BudgetRequest):
    """Set monthly budget in CNY."""
    from src.services.cost_service import set_monthly_budget

    if req.monthly_budget_cny < 0:
        return {"success": False, "error": "预算不能为负数"}
    await set_monthly_budget(req.monthly_budget_cny)
    return {"success": True, "monthly_budget_cny": req.monthly_budget_cny}


async def _check_ollama() -> dict:
    """Check Ollama installation, connectivity, and available models."""
    ollama_installed = shutil.which("ollama") is not None

    result: dict = {
        "llm_provider": "ollama",
        "llm_model": get_model_name(),
        "ollama_running": False,
        "ollama_status": "not_installed" if not ollama_installed else "installed_not_running",
        "ollama_url": OLLAMA_BASE_URL,
        "required_model": REQUIRED_MODEL,
        "model_available": False,
        "available_models": [],
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                result["ollama_running"] = True
                result["ollama_status"] = "running"
                data = resp.json()
                models_raw = data.get("models", [])
                result["available_models"] = [
                    {
                        "name": m.get("name", ""),
                        "size": m.get("size", 0),
                        "modified_at": m.get("modified_at", ""),
                    }
                    for m in models_raw
                ]
                model_names = [m.get("name", "") for m in models_raw]
                result["model_available"] = any(
                    m == REQUIRED_MODEL
                    or m.startswith(REQUIRED_MODEL + ":")
                    or (
                        REQUIRED_MODEL.startswith(m.split(":")[0])
                        and m.split(":")[0] == REQUIRED_MODEL.split(":")[0]
                    )
                    for m in model_names
                )
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
        result["error"] = str(e)[:200]

    return result


async def _check_openai() -> dict:
    """Check OpenAI-compatible API connectivity."""
    from src.infra import config

    result: dict = {
        "llm_base_url": config.LLM_BASE_URL,
        "api_available": False,
    }

    if not config.LLM_API_KEY or not config.LLM_BASE_URL:
        result["cloud_error"] = "API Key 和 Base URL 未配置"
        return result

    try:
        base = config.LLM_BASE_URL.rstrip("/")
        headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            result["api_available"] = resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
        result["cloud_error"] = str(e)[:200]

    return result
