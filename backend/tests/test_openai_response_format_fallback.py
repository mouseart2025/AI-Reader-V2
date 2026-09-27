"""Issue #84: llama.cpp / LM Studio reject response_format=json_object (400).

Newer llama.cpp and LM Studio only accept `response_format.type` in
{json_schema, text}; the legacy `{"type": "json_object"}` now comes back as
400 "'response_format.type' must be 'json_schema' or 'text'". The client must
degrade — retry once without the field — instead of failing the whole call
(which surfaced to users as a 400 on 开始分析 and a wrapped 503 on 模型测试).
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.infra.llm_client import LLMError
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


def _client(handler, base_url: str) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(base_url, "k", "m")
    transport = httpx.MockTransport(handler)
    client._make_client = lambda timeout: httpx.AsyncClient(transport=transport)
    return client


@pytest.mark.asyncio
async def test_retries_without_response_format_when_endpoint_rejects_it():
    """The exact #84 failure: 400 while json_object is present, 200 once dropped."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        if "response_format" in body:
            return httpx.Response(
                400,
                json={
                    "error": "'response_format.type' must be "
                    "'json_schema' or 'text'"
                },
            )
        return httpx.Response(200, json=_reply({"characters": []}))

    client = _client(handler, "http://127.0.0.1:1234/v1")
    result, _usage = await client.generate(
        "sys", "usr", format={"type": "json_object"}
    )

    assert result == {"characters": []}
    assert len(seen) == 2, "must retry exactly once"
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in seen[1], "the retry must drop the field"


@pytest.mark.asyncio
async def test_compatible_endpoint_still_gets_one_request():
    """No capability probing: a happy endpoint must see exactly one request."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_reply({"ok": True}))

    client = _client(handler, "https://api.deepseek.com/v1")
    result, _usage = await client.generate(
        "sys", "usr", format={"type": "json_object"}
    )

    assert result == {"ok": True}
    assert len(seen) == 1
    assert seen[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_unrelated_400_is_not_downgraded():
    """Only a response_format-related 400 falls back; other 400s must surface."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "context length exceeded"})

    client = _client(handler, "http://127.0.0.1:1234/v1")
    with pytest.raises(LLMError):
        await client.generate("sys", "usr", format={"type": "json_object"})

    assert calls["n"] == 1, "an unrelated 400 must not trigger a retry"
