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

from src.api.routes.settings import _benchmark_error_status, _model_known_flags
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
    assert cap.response_format == "json_object"


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
    # Official parameter reference lists json_object as supported; json_schema
    # is deferred until the extraction schema is verified against strict mode.
    assert body["response_format"] == {"type": "json_object"}


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


def test_benchmark_error_status_passes_payment_and_size_codes():
    """402 (balance) and 413 (prompt too large) must reach the user verbatim."""
    assert _benchmark_error_status(LLMHTTPError("x", status_code=402)) == 402
    assert _benchmark_error_status(LLMHTTPError("x", status_code=413)) == 413


def test_retired_model_id_is_flagged_with_suggestion():
    flags = _model_known_flags("moonshot", "kimi-latest")
    assert flags == {"model_known": False, "suggested_model": "kimi-k3.1-pro"}
    assert _model_known_flags("moonshot", "kimi-k3.1-pro") == {"model_known": True}
    assert _model_known_flags("custom", "whatever") == {"model_known": True}
    assert _model_known_flags("moonshot", "") == {"model_known": True}


def test_legacy_id_is_not_reported_as_healthy():
    """deepseek-chat still routes but vanished from GET /models.

    Claiming ``model_known: True`` alone would hide that the vendor stopped
    listing it; the UI needs the extra flag to suggest the new default.
    """
    flags = _model_known_flags("deepseek", "deepseek-chat")
    assert flags == {
        "model_known": True,
        "model_legacy": True,
        "suggested_model": "deepseek-flash",
    }
    assert _model_known_flags("deepseek", "deepseek-flash") == {"model_known": True}


def test_deepseek_preset_matches_official_models_endpoint():
    """GET /models on 2026-11-17 returned exactly deepseek-flash + deepseek-v4-pro."""
    deepseek = next(p for p in model_catalog.PROVIDERS if p["id"] == "deepseek")
    assert deepseek["default_model"] == "deepseek-flash"
    assert "deepseek-v4-pro" in deepseek["models"]
    assert "deepseek-v4.1-flash" not in deepseek["models"]


@pytest.mark.asyncio
async def test_json_schema_branch_still_works(monkeypatch):
    """No shipped model uses it today, but the branch must not rot."""
    monkeypatch.setattr(
        model_catalog,
        "_MODEL_RULES",
        (
            (
                "js-test-model",
                model_catalog.ModelCapability(response_format="json_schema"),
            ),
        ),
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_reply({"ok": True}))

    client = _client(handler, "https://example.test/v1", "js-test-model")
    await client.generate("sys", "usr", format={"type": "object"})

    assert seen[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "extraction", "schema": {"type": "object"}},
    }


@pytest.mark.asyncio
async def test_providers_endpoint_exposes_catalogue_provenance():
    from src.api.routes.settings import get_cloud_providers

    payload = await get_cloud_providers()
    assert payload["preset_version"] == model_catalog.PRESET_VERSION
    assert payload["model_data_as_of"] == model_catalog.MODEL_DATA_AS_OF
    assert len(payload["providers"]) >= 10


def test_qwen_preset_matches_official_model_page():
    """官方模型大全（更新 2026-11-16）首推 qwen4-max；qwen3.8-max 未出现。"""
    qwen = next(p for p in model_catalog.PROVIDERS if p["id"] == "qwen")
    assert qwen["default_model"] == "qwen4-max"
    assert "qwen3.8-flash" in qwen["models"]
    assert "qwen3.8-max" not in qwen["models"]


def test_minimax_preset_matches_official_docs():
    mm = next(p for p in model_catalog.PROVIDERS if p["id"] == "minimax")
    assert mm["default_model"] == "MiniMax-M3.1-Flash"
    assert "MiniMax-M3" in mm["models"]
    assert "MiniMax-M2.7" in mm["legacy_models"]
    assert "MiniMax-M2.7" not in mm["models"]


def test_gemini_preset_excludes_preview_models():
    """3.1 Pro is preview per Vertex AI docs; keep only GA ids in the list."""
    gemini = next(p for p in model_catalog.PROVIDERS if p["id"] == "gemini")
    assert "gemini-3.1-pro" not in gemini["models"]
    assert gemini["default_model"] == "gemini-3.8-flash"


def test_minimax_omits_sampling_params():
    """Official docs: temperature is ignored (always 1), top_p fixed at 0.95."""
    cap = model_catalog.resolve_capability("MiniMax-M3.1-Flash")
    assert cap.sampling_params is False


def test_moonshot_matches_real_models_endpoint():
    """GET /models on 2026-11-17 returned these five ids exactly."""
    moonshot = next(p for p in model_catalog.PROVIDERS if p["id"] == "moonshot")
    assert moonshot["models"] == [
        "kimi-k3.1-pro",
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
    ]
    # Documented by the vendor but 404 for a normal account — must not be offered.
    assert "kimi-k3.1-pro-highspeed" not in moonshot["models"]
