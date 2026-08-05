"""Agentic QA 测试（issue #26）：工具循环、status 帧、三层降级、provider 解析。"""

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.infra.anthropic_client import AnthropicClient
from src.infra.llm_client import LLMClient, ToolCall
from src.infra.openai_client import OpenAICompatibleClient
from src.services import agent_qa_service, query_service


def _facts() -> list[dict]:
    return [
        {
            "chapter_id": 1,
            "fact": {
                "chapter_id": 1,
                "characters": [],
                "relationships": [],
                "locations": [],
                "item_events": [],
                "org_events": [],
                "events": [{"type": "plot", "summary": "取经 队伍出发"}],
                "new_concepts": [],
            },
        }
    ]


class _StreamLlm:
    """Mock LLM whose generate_stream yields fixed tokens."""

    def __init__(self, tokens: list[str]):
        self._tokens = tokens

    async def generate_stream(self, system, prompt, timeout=180):
        for t in self._tokens:
            yield t


async def _collect(stream) -> list[dict]:
    return [chunk async for chunk in stream]


# ── 工具循环 ─────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_loop_executes_tools_and_streams_final_answer():
    """模型先请求 search_text，再停止调用：工具被执行、status 帧先于 token、
    最终走流式生成路径且 sources 含工具来源章节。"""
    tool_llm = MagicMock()
    tool_llm.generate_with_tools = AsyncMock(
        side_effect=[
            (None, [ToolCall(name="search_text", arguments={"keyword": "取经"})]),
            ("已收集到证据", []),
        ]
    )
    final_llm = _StreamLlm(["唐僧", "师徒取经[第3章]"])

    mock_fact_store = MagicMock()
    mock_fact_store.get_all_chapter_facts = AsyncMock(return_value=_facts())
    mock_chapter_store = MagicMock()
    mock_chapter_store.search_chapters = AsyncMock(
        return_value=[{"chapter_num": 3, "title": "三打白骨精", "snippet": "...取经..."}]
    )

    with (
        patch.object(agent_qa_service, "get_llm_client", return_value=tool_llm),
        patch.object(agent_qa_service, "chapter_fact_store", mock_fact_store),
        patch.object(agent_qa_service, "chapter_store", mock_chapter_store),
        patch.object(query_service, "get_llm_client", return_value=final_llm),
    ):
        frames = await _collect(
            agent_qa_service.agent_query_stream("n1", "取经路上发生了什么", None)
        )

    types = [f["type"] for f in frames]
    # status 帧在 token 之前
    assert types[0] == "status"
    assert "取经" in frames[0]["content"]
    assert "token" in types and types[-1] == "done"
    # 工具确实被调用
    mock_chapter_store.search_chapters.assert_awaited_once_with("n1", "取经", limit=5)
    # sources 合并了工具来源章节(3)与答案引用章节(3)
    sources = next(f for f in frames if f["type"] == "sources")
    assert 3 in sources["chapters"]
    # 两轮 generate_with_tools
    assert tool_llm.generate_with_tools.await_count == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_tool_failure_fed_back_as_observation():
    """单个工具异常不中断循环，失败信息作为观察追加进 messages 喂回模型。"""
    tool_llm = MagicMock()
    tool_llm.generate_with_tools = AsyncMock(
        side_effect=[
            (None, [ToolCall(name="search_text", arguments={"keyword": "x"})]),
            ("好", []),
        ]
    )
    mock_fact_store = MagicMock()
    mock_fact_store.get_all_chapter_facts = AsyncMock(return_value=_facts())
    mock_chapter_store = MagicMock()
    mock_chapter_store.search_chapters = AsyncMock(side_effect=RuntimeError("db down"))

    with (
        patch.object(agent_qa_service, "get_llm_client", return_value=tool_llm),
        patch.object(agent_qa_service, "chapter_fact_store", mock_fact_store),
        patch.object(agent_qa_service, "chapter_store", mock_chapter_store),
        patch.object(query_service, "get_llm_client", return_value=_StreamLlm(["答案"])),
    ):
        frames = await _collect(agent_qa_service.agent_query_stream("n1", "问题", None))

    # 第二轮调用的 messages 里包含"执行失败"观察
    second_call_messages = tool_llm.generate_with_tools.await_args_list[1].args[0]
    assert any("执行失败" in m.get("content", "") for m in second_call_messages)
    # 工具无结果 → context 为空 → 固定"未检索到"回复，不调用生成
    tokens = [f for f in frames if f["type"] == "token"]
    assert tokens and "未从已分析内容中检索到" in tokens[0]["content"]


# ── 降级 ─────────────────────────────────────────────


def _rag_patches(final_llm):
    """Mock RAG 管线在 query_service 命名空间内的全部外部依赖。"""
    mock_fact_store = MagicMock()
    mock_fact_store.get_all_chapter_facts = AsyncMock(return_value=_facts())
    mock_chapter_store = MagicMock()
    mock_chapter_store.search_chapters = AsyncMock(return_value=[])
    mock_embedding = MagicMock()
    mock_embedding.search_chapters.side_effect = RuntimeError("no chroma")
    return [
        patch.object(query_service, "chapter_fact_store", mock_fact_store),
        patch.object(query_service, "chapter_store", mock_chapter_store),
        patch.object(query_service, "embedding_service", mock_embedding),
        patch.object(query_service, "build_alias_map", AsyncMock(return_value={})),
        patch.object(query_service, "get_llm_client", return_value=final_llm),
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_failure_falls_back_to_rag():
    """generate_with_tools 抛 NotImplementedError → catch 记 warning 回落 RAG。"""
    async def _raising_stream(**kwargs):
        raise NotImplementedError("no tool calling")
        yield  # pragma: no cover — 使其成为 async generator

    with (
        patch("src.infra.config.QA_MODE", "agent"),
        patch("src.infra.config.LLM_PROVIDER", "openai"),
        patch.object(agent_qa_service, "agent_query_stream", _raising_stream),
        ExitStack() as stack,
    ):
        for p in _rag_patches(_StreamLlm(["RAG答案[第1章]"])):
            stack.enter_context(p)
        frames = await _collect(query_service.query_stream("n1", "取经 发生了什么", None))

    types = [f["type"] for f in frames]
    assert "token" in types and types[-1] == "done"
    answer = "".join(f["content"] for f in frames if f["type"] == "token")
    assert "RAG答案" in answer


@pytest.mark.asyncio(loop_scope="session")
async def test_rag_mode_never_enters_agent_loop():
    """QA_MODE=rag（默认）→ 不进入 agent 循环。"""
    spy = MagicMock()
    with (
        patch("src.infra.config.QA_MODE", "rag"),
        patch.object(agent_qa_service, "agent_query_stream", spy),
        ExitStack() as stack,
    ):
        for p in _rag_patches(_StreamLlm(["答案"])):
            stack.enter_context(p)
        frames = await _collect(query_service.query_stream("n1", "取经 发生了什么", None))

    spy.assert_not_called()
    assert frames[-1]["type"] == "done"


# ── Provider tools 解析 ──────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_openai_generate_with_tools_parses_tool_calls():
    """OpenAI 格式：choices[0].message.tool_calls → ToolCall 列表。"""
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "search_text",
                                "arguments": json.dumps({"keyword": "取经"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    client = OpenAICompatibleClient("http://x", "k", "m")
    client._make_client = lambda timeout: httpx.AsyncClient(transport=transport)

    text, calls = await client.generate_with_tools(
        [{"role": "user", "content": "q"}], agent_qa_service.TOOLS
    )
    assert text is None
    assert calls == [ToolCall(name="search_text", arguments={"keyword": "取经"})]


@pytest.mark.asyncio(loop_scope="session")
async def test_anthropic_generate_with_tools_parses_all_blocks():
    """Anthropic 格式：遍历全部 content block，text 可不在 content[0]，
    多个 tool_use block 都要解析。"""
    payload = {
        "content": [
            {"type": "tool_use", "id": "t1", "name": "get_entity_profile",
             "input": {"name": "孙悟空"}},
            {"type": "text", "text": "先查档案"},
            {"type": "tool_use", "id": "t2", "name": "search_text",
             "input": {"keyword": "金箍棒"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    client = AnthropicClient("http://x", "k", "m")
    client._make_client = lambda timeout: httpx.AsyncClient(transport=transport)

    text, calls = await client.generate_with_tools(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}],
        agent_qa_service.TOOLS,
    )
    assert text == "先查档案"
    assert calls == [
        ToolCall(name="get_entity_profile", arguments={"name": "孙悟空"}),
        ToolCall(name="search_text", arguments={"keyword": "金箍棒"}),
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_ollama_generate_with_tools_not_implemented():
    """Ollama 直接 NotImplementedError，触发上层降级。"""
    client = LLMClient()
    with pytest.raises(NotImplementedError):
        await client.generate_with_tools([], [])
