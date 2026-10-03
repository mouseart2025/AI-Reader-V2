"""Issue #78: Anthropic prompt caching + 抽取 schema 序列化缓存。

- AnthropicClient 的 system prompt 必须以带 cache_control 断点的 text 块
  形式发送(ephemeral),~22KB 静态 system 才能命中服务端缓存;
  空 system 保持原字符串语义。
- chapter_fact_extractor 的不变 schema(recall / source_pass / 主抽取)
  在进程内是缓存单例,序列化文本只算一次。
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.extraction import chapter_fact_extractor as cfe
from src.infra import config
from src.infra.anthropic_client import AnthropicClient

_SYSTEM_BLOCK = {
    "type": "text",
    "text": "sys",
    "cache_control": {"type": "ephemeral"},
}


def _client(handler) -> AnthropicClient:
    client = AnthropicClient("http://x", "k", "m")
    transport = httpx.MockTransport(handler)
    client._make_client = lambda timeout: httpx.AsyncClient(transport=transport)
    return client


@pytest.mark.asyncio
async def test_generate_sends_system_with_cache_control():
    """generate() 的 system 必须是带 ephemeral 断点的 text 块,其余字段不变。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "{}"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = _client(handler)
    await client.generate("sys", "usr", format={"type": "object"})

    assert len(seen) == 1
    body = seen[0]
    assert body["system"] == [_SYSTEM_BLOCK]
    assert body["messages"] == [{"role": "user", "content": "usr"}]
    assert body["model"] == "m"


@pytest.mark.asyncio
async def test_generate_empty_system_stays_string():
    """空 system 不包装,保持原字符串语义(与改造前逐字节一致)。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = _client(handler)
    await client.generate("", "usr")

    assert seen[0]["system"] == ""


@pytest.mark.asyncio
async def test_generate_with_tools_caches_system_block():
    """generate_with_tools() 同样给 system 加缓存断点;无 system 时不带该字段。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = _client(handler)
    await client.generate_with_tools(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}],
        [{"name": "t", "description": "", "parameters": {}}],
    )
    await client.generate_with_tools([{"role": "user", "content": "q"}], [])

    assert seen[0]["system"] == [_SYSTEM_BLOCK]
    assert "system" not in seen[1]


@pytest.mark.asyncio
async def test_generate_stream_sends_system_with_cache_control():
    """流式路径同样加缓存断点。"""
    seen: list[dict] = []
    sse = (
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"你好"}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse,
        )

    client = _client(handler)
    tokens = [t async for t in client.generate_stream("sys", "usr")]

    assert tokens == ["你好"]
    assert seen[0]["system"] == [_SYSTEM_BLOCK]
    assert seen[0]["stream"] is True


def test_schema_builders_return_cached_singletons(monkeypatch):
    """同一开关状态下 schema 是同一对象(重复构建被缓存消除)。"""
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)
    assert cfe._build_recall_schema() is cfe._build_recall_schema()
    assert cfe._build_source_pass_schema() is cfe._build_source_pass_schema()
    assert cfe._build_extraction_schema() is cfe._build_extraction_schema()


def test_schema_cache_keyed_by_grounding_flag(monkeypatch):
    """证据锚定开关变化时必须得到不同 schema(缓存键含开关)。"""
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)
    on = cfe._build_recall_schema()
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)
    off = cfe._build_recall_schema()

    assert on is not off
    assert "default" not in on["$defs"]["EventFact"]["properties"]["evidence"]
    assert off["$defs"]["EventFact"]["properties"]["evidence"].get("default") == ""


def test_schema_text_serialized_once_per_object():
    """同一 schema 对象的序列化文本只算一次;文本与直接 dumps 一致。"""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    expected = json.dumps(schema, ensure_ascii=False, indent=2)

    text1 = cfe._schema_text(schema)
    text2 = cfe._schema_text(schema)

    assert text1 == expected
    assert text2 is text1
