"""Model catalogue + capability-driven request shaping (2026-11 refresh).

Motivating bug: Kimi K3.1 Pro passed 「API Key 验证」 (which only calls
GET /models) but every 模型测试 failed. The client always sent ``temperature``;
K3 pins it server-side and rejects an explicit value. The failure then got
wrapped into a generic 503, hiding the real 400.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.api.routes.settings import _benchmark_error_status
from src.infra import model_catalog
from src.infra.llm_client import LLMError, LLMHTTPError
from src.infra.openai_client import OpenAICompatibleClient


def _reply(payload: dict) -> dict:
    return {
        "choices": [
            {
                "message": {"content": json.dumps(payload, ensure_ascii=False)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _client(handler, base_url: str, model: str) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(base_url, "k", model)
    transport = httpx.MockTransport(handler)
    client._make_client = lambda timeout: httpx.AsyncClient(transport=transport)
    return client


# ── capability resolution ─────────────────────────────────────────────


def test_kimi_k3_forbids_sampling_params():
    cap = model_catalog.resolve_capability("kimi-k3.1-pro")
    assert cap.sampling_params is False
    assert cap.max_tokens_field == "max_completion_tokens"
    assert cap.response_format == "json_schema"


def test_longest_prefix_wins():
    # The highspeed variant must inherit the K3 rules, not fall back to default.
    cap = model_catalog.resolve_capability("kimi-k3.1-pro-highspeed")
    assert cap.sampling_params is False
    assert cap.max_tokens_field == "max_completion_tokens"


def test_unknown_model_keeps_legacy_behaviour():
    """Custom / self-hosted endpoints must behave exactly as before."""
    cap = model_catalog.resolve_capability("my-self-hosted-model")
    assert cap == model_catalog.DEFAULT_CAPABILITY
    assert cap.sampling_params is True
    assert cap.max_tokens_field == "max_tokens"
    assert cap.response_format == "json_object"


def test_glm_does_not_send_response_format():
    assert model_catalog.resolve_capability("glm-5.3").response_format == "none"


# ── preset integrity ─────────────────────────────────────────────────


def test_presets_are_complete_and_defaults_exist():
    ids = [p["id"] for p in model_catalog.PROVIDERS]
    assert len(ids) == len(set(ids)), "provider ids must be unique"
    for provider in model_catalog.PROVIDERS:
        for key in ("id", "name", "base_url", "default_model", "models", "api_format"):
            assert key in provider, f"{provider.get('id')} missing {key}"
        if provider["models"]:
            assert provider["default_model"] in provider["models"], (
                f"{provider['id']}: default_model not in models"
            )


def test_moonshot_no_longer_lists_retired_models():
    moonshot = next(p for p in model_catalog.PROVIDERS if p["id"] == "moonshot")
    retired = {"kimi-latest", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"}
    assert retired.isdisjoint(moonshot["models"])
    assert moonshot["default_model"] == "kimi-k3.1-pro"


def test_catalog_carries_provenance():
    assert model_catalog.PRESET_VERSION
    assert model_catalog.MODEL_DATA_AS_OF


# ── request shaping ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_kimi_payload_omits_temperature_and_uses_completion_tokens():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_reply({"characters": []}))

    client = _client(handler, "https://api.moonshot.cn/v1", "kimi-k3.1-pro")
    await client.generate("sys", "usr", format={"type": "object"})

    body = seen[0]
    assert "temperature" not in body, "K3 rejects explicit sampling params"
    assert body["max_completion_tokens"] > 0
    assert "max_tokens" not in body
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "extraction"


@pytest.mark.asyncio
async def test_unknown_model_payload_unchanged():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_reply({"ok": True}))

    client = _client(handler, "https://example.test/v1", "some-local-model")
    await client.generate("sys", "usr", format={"type": "object"}, temperature=0.1)

    body = seen[0]
    assert body["temperature"] == 0.1
    assert body["max_tokens"] > 0
    assert body["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_upstream_status_is_preserved_in_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "temperature must be 1.0"})

    client = _client(handler, "https://api.moonshot.cn/v1", "kimi-k3.1-pro")
    with pytest.raises(LLMHTTPError) as excinfo:
        await client.generate("sys", "usr", format={"type": "object"})

    assert excinfo.value.status_code == 400
    assert "temperature must be 1.0" in str(excinfo.value)
    assert isinstance(excinfo.value, LLMError)


# ── benchmark status mapping ─────────────────────────────────────────


def test_benchmark_error_status_passes_upstream_code_through():
    assert _benchmark_error_status(LLMHTTPError("x", status_code=429)) == 429
    assert _benchmark_error_status(LLMHTTPError("x", status_code=400)) == 400
    assert _benchmark_error_status(LLMHTTPError("x", status_code=503)) == 503


def test_benchmark_error_status_hides_unknown_codes():
    """418/451 etc. become 502 rather than leaking a confusing code."""
    assert _benchmark_error_status(LLMHTTPError("x", status_code=418)) == 502
    assert _benchmark_error_status(RuntimeError("x")) == 502
