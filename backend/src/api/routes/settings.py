"""Settings and environment health-check endpoints."""

import httpx
from fastapi import APIRouter

from src.infra.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    get_model_name,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

REQUIRED_MODEL = "qwen2.5:7b"


@router.get("")
async def get_settings():
    return {
        "settings": {
            "llm_provider": LLM_PROVIDER,
            "llm_model": get_model_name(),
            "ollama_base_url": OLLAMA_BASE_URL,
            "required_model": REQUIRED_MODEL,
        }
    }


@router.get("/health-check")
async def health_check():
    """Check LLM connectivity based on current provider."""
    if LLM_PROVIDER == "openai":
        return await _check_openai()
    return await _check_ollama()


async def _check_ollama() -> dict:
    """Check Ollama connectivity and required model availability."""
    result = {
        "llm_provider": "ollama",
        "llm_model": get_model_name(),
        "ollama_running": False,
        "ollama_url": OLLAMA_BASE_URL,
        "required_model": REQUIRED_MODEL,
        "model_available": False,
        "available_models": [],
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                result["ollama_running"] = True
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                result["available_models"] = models
                result["model_available"] = any(
                    m == REQUIRED_MODEL or m.startswith(REQUIRED_MODEL + ":")
                    or REQUIRED_MODEL.startswith(m.split(":")[0])
                    and m.split(":")[0] == REQUIRED_MODEL.split(":")[0]
                    for m in models
                )
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        pass

    return result


async def _check_openai() -> dict:
    """Check OpenAI-compatible API connectivity."""
    result = {
        "llm_provider": "openai",
        "llm_model": get_model_name(),
        "llm_base_url": LLM_BASE_URL,
        "api_available": False,
    }

    if not LLM_API_KEY or not LLM_BASE_URL:
        result["error"] = "LLM_API_KEY 和 LLM_BASE_URL 未配置"
        return result

    try:
        base = LLM_BASE_URL.rstrip("/")
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"}
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            resp = await client.get(f"{base}/models", headers=headers)
            result["api_available"] = resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, Exception) as e:
        result["error"] = str(e)[:200]

    return result
