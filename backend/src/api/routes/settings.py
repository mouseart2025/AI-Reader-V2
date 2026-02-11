"""Settings and environment health-check endpoints."""

import httpx
from fastapi import APIRouter

from src.infra.config import OLLAMA_BASE_URL

router = APIRouter(prefix="/api/settings", tags=["settings"])

REQUIRED_MODEL = "qwen2.5:7b"


@router.get("")
async def get_settings():
    return {"settings": {"ollama_base_url": OLLAMA_BASE_URL, "required_model": REQUIRED_MODEL}}


@router.get("/health-check")
async def health_check():
    """Check Ollama connectivity and required model availability."""
    result = {
        "ollama_running": False,
        "ollama_url": OLLAMA_BASE_URL,
        "required_model": REQUIRED_MODEL,
        "model_available": False,
        "available_models": [],
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check Ollama is running
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                result["ollama_running"] = True
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                result["available_models"] = models
                # Check if required model is available (match by prefix)
                result["model_available"] = any(
                    m == REQUIRED_MODEL or m.startswith(REQUIRED_MODEL + ":")
                    or REQUIRED_MODEL.startswith(m.split(":")[0])
                    and m.split(":")[0] == REQUIRED_MODEL.split(":")[0]
                    for m in models
                )
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        pass

    return result
