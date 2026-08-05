"""Agentic QA: tool-use forensics loop + shared final answer generation.

The agent loop only does forensics — non-streaming tool calls that gather
evidence from the knowledge base. The final answer reuses query_service's
existing streaming path (_stream_final_answer). Intermediate steps are
surfaced to the client as {"type": "status", ...} frames.

Fallback layers (see query_service.query_stream entry branch):
  1. QA_MODE=rag or Ollama provider → agent mode never entered
  2. generate_with_tools raises (e.g. NotImplementedError) → caller falls back
  3. A single tool failure → fed back to the model as an observation
"""

import json
import logging
from collections.abc import AsyncIterator

from src.db import chapter_fact_store, chapter_store, conversation_store
from src.infra.llm_client import ToolCall, get_llm_client
from src.services import entity_aggregator, query_service

logger = logging.getLogger(__name__)

# Max tool-use iterations before forcing final generation
MAX_ITER = 4

# Provider-neutral tool schemas (wrapped per-provider in generate_with_tools)
TOOLS: list[dict] = [
    {
        "name": "get_entity_profile",
        "description": (
            "查询实体的聚合档案（跨章节合并的人物/地点/物品/组织信息，"
            "含别名、关系、能力、经历等）。当问题涉及具体人物、地点、物品或组织名时优先使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "实体原名，如「孙悟空」"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_text",
        "description": (
            "在已分析章节的原文中按关键词全文搜索，返回匹配章节与上下文片段。"
            "适合查找具体情节、台词、物品出现位置。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词（尽量简短精确）"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_chapter_excerpt",
        "description": (
            "获取指定章节的事件摘要和原文片段。适合回答「第 X 章发生了什么」"
            "或需要通读某章细节的问题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chapter_num": {"type": "integer", "description": "章节号"},
            },
            "required": ["chapter_num"],
        },
    },
]

_AGENT_SYSTEM_PROMPT = """你是一个小说知识库的取证助手。你的任务是通过调用工具收集回答问题所需的证据，而不是直接回答。

## 可用工具
- get_entity_profile(name): 查询人物/地点/物品/组织的聚合档案
- search_text(keyword): 在已分析章节原文中全文搜索
- get_chapter_excerpt(chapter_num): 获取某章的事件摘要与原文片段

## 规则
1. 根据用户问题，选择最相关的工具查证，一次可以调用多个工具
2. 工具结果会反馈给你；如果信息不足，可以换关键词或换工具继续查证
3. 当你认为证据足够回答问题时，停止调用工具，直接回复一句话说明你已收集到的要点
4. 不要编造工具结果中没有的内容"""


def _status_text(tc: ToolCall) -> str:
    """Human-readable status line for a tool call (shown in the chat UI)."""
    if tc.name == "get_entity_profile":
        return f"正在查证「{tc.arguments.get('name', '')}」的档案..."
    if tc.name == "search_text":
        return f"正在全文搜索「{tc.arguments.get('keyword', '')}」..."
    if tc.name == "get_chapter_excerpt":
        return f"正在查阅第 {tc.arguments.get('chapter_num', '?')} 章..."
    return "正在查证..."


async def _tool_get_entity_profile(novel_id: str, name: str) -> tuple[str, set[int]]:
    """Aggregate entity profile; try person first, then location/item/org."""
    # Person: reuse query_service's profile formatting (~2000 chars)
    ctx, person_chapters = await query_service._build_profile_context(
        novel_id, [name], max_chars=2000
    )
    if ctx:
        return ctx, set(person_chapters)

    chapters: set[int] = set()

    # Location
    try:
        loc = await entity_aggregator.aggregate_location(novel_id, name)
        if loc.descriptions or loc.events or loc.visitors:
            parts = [f"## 地点「{loc.name}」类型={loc.location_type}"]
            if loc.parent:
                parts.append(f"父级地点: {loc.parent}")
            for d in loc.descriptions[:5]:
                parts.append(f"[第{d.chapter}章] 描述: {d.description[:80]}")
                chapters.add(d.chapter)
            for e in loc.events[:8]:
                parts.append(f"[第{e.chapter}章] 事件[{e.type}]: {e.summary[:80]}")
                chapters.add(e.chapter)
            return "\n".join(parts)[:2000], chapters
    except Exception as e:
        logger.debug("aggregate_location(%s) failed: %s", name, e)

    # Item
    try:
        item = await entity_aggregator.aggregate_item(novel_id, name)
        if item.flow:
            parts = [f"## 物品「{item.name}」类型={item.item_type}"]
            for f in item.flow[:10]:
                parts.append(
                    f"[第{f.chapter}章] {f.actor} {f.action} {item.name}"
                    f" ({f.description[:60]})"
                )
                chapters.add(f.chapter)
            return "\n".join(parts)[:2000], chapters
    except Exception as e:
        logger.debug("aggregate_item(%s) failed: %s", name, e)

    # Org
    try:
        org = await entity_aggregator.aggregate_org(novel_id, name)
        if org.member_events or org.org_relations:
            parts = [f"## 组织「{org.name}」类型={org.org_type}"]
            for me in org.member_events[:10]:
                parts.append(
                    f"[第{me.chapter}章] {me.member} {me.action} (角色={me.role or ''})"
                )
                chapters.add(me.chapter)
            return "\n".join(parts)[:2000], chapters
    except Exception as e:
        logger.debug("aggregate_org(%s) failed: %s", name, e)

    return f"未找到实体「{name}」的档案信息。", set()


async def _tool_search_text(novel_id: str, keyword: str) -> tuple[str, set[int]]:
    """Full-text search over analyzed chapters (≤5 hits)."""
    results = await chapter_store.search_chapters(novel_id, keyword, limit=5)
    if not results:
        return f"未在已分析章节中搜索到「{keyword}」。", set()
    chapters: set[int] = set()
    lines = []
    for r in results:
        chapters.add(r["chapter_num"])
        lines.append(f"[第{r['chapter_num']}章] {r.get('title', '')}: {r['snippet']}")
    return "\n".join(lines), chapters


async def _tool_get_chapter_excerpt(
    novel_id: str, chapter_num: int
) -> tuple[str, set[int]]:
    """Chapter event summaries + raw excerpt, capped at ~1500 chars."""
    parts: list[str] = [f"## 第{chapter_num}章"]
    total = len(parts[0])

    fact_row = await chapter_fact_store.get_chapter_fact(novel_id, chapter_num)
    if fact_row:
        events = fact_row["fact"].get("events", [])
        for evt in events[:10]:
            line = f"事件[{evt.get('type', '')}]: {evt.get('summary', '')[:100]}"
            if total + len(line) > 700:
                break
            parts.append(line)
            total += len(line)

    chapter = await chapter_store.get_chapter_content(novel_id, chapter_num)
    if chapter:
        excerpt = chapter.get("content", "")[: 1500 - total]
        if excerpt:
            parts.append(f"原文片段: {excerpt}")
    if not fact_row and not chapter:
        return f"第 {chapter_num} 章不存在或尚未分析。", set()

    return "\n".join(parts)[:1500], {chapter_num}


async def _execute_tool(novel_id: str, tc: ToolCall) -> tuple[str, set[int]]:
    """Dispatch a tool call. Returns (observation text, source chapters)."""
    if tc.name == "get_entity_profile":
        return await _tool_get_entity_profile(novel_id, str(tc.arguments.get("name", "")))
    if tc.name == "search_text":
        return await _tool_search_text(novel_id, str(tc.arguments.get("keyword", "")))
    if tc.name == "get_chapter_excerpt":
        try:
            chapter_num = int(tc.arguments.get("chapter_num", 0))
        except (TypeError, ValueError):
            chapter_num = 0
        return await _tool_get_chapter_excerpt(novel_id, chapter_num)
    return f"未知工具: {tc.name}", set()


async def agent_query_stream(
    novel_id: str,
    question: str,
    conversation_id: str | None = None,
) -> AsyncIterator[dict]:
    """Agentic QA stream: tool-use forensics loop then shared final generation.

    Yields {"type": "status"} frames during the loop, then the standard
    token/sources/done frames from query_service._stream_final_answer.
    """
    llm = get_llm_client()

    all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    if not all_facts:
        yield {"type": "token", "content": "该小说尚未进行分析，请先分析后再提问。"}
        yield {"type": "sources", "chapters": []}
        yield {"type": "done"}
        return

    analyzed_count = len(all_facts)

    # Small talk: fixed reply, never touch the tool loop
    if query_service._is_greeting(question):
        yield {
            "type": "token",
            "content": (
                f"你好！我是这本小说的知识库助手，当前已分析 {analyzed_count} 章。"
                "你可以问我已分析内容里的人物、地点、事件等问题，"
                "比如「孙悟空和唐僧是什么关系」「第 5 章发生了什么」。"
            ),
        }
        yield {"type": "sources", "chapters": []}
        yield {"type": "done"}
        return

    # Assemble messages: system + recent history + question
    messages: list[dict] = [{"role": "system", "content": _AGENT_SYSTEM_PROMPT}]
    if conversation_id:
        recent = await conversation_store.get_recent_messages(conversation_id, limit=6)
        for msg in recent:
            messages.append({"role": msg["role"], "content": msg["content"][:200]})
    messages.append({"role": "user", "content": question})

    source_chapters: set[int] = set()
    context_parts: list[str] = []

    for _ in range(MAX_ITER):
        text, tool_calls = await llm.generate_with_tools(messages, TOOLS)
        if not tool_calls:
            break

        call_desc = ", ".join(
            f"{tc.name}({json.dumps(tc.arguments, ensure_ascii=False)})"
            for tc in tool_calls
        )
        messages.append({
            "role": "assistant",
            "content": f"{text or ''}\n[调用工具] {call_desc}".strip(),
        })

        observations: list[str] = []
        for tc in tool_calls:
            yield {"type": "status", "content": _status_text(tc)}
            try:
                result, chapters = await _execute_tool(novel_id, tc)
                source_chapters.update(chapters)
                if result:
                    context_parts.append(result)
                observations.append(f"### 工具 {tc.name} 结果\n{result}")
            except Exception as e:
                # Tool failure is an observation, not a fatal error
                logger.warning("Agent tool %s failed: %s", tc.name, e)
                observations.append(f"### 工具 {tc.name} 执行失败: {e}")
        messages.append({
            "role": "user",
            "content": "工具查询结果如下：\n\n" + "\n\n".join(observations),
        })

    if not context_parts:
        yield {
            "type": "token",
            "content": (
                f"当前已分析 {analyzed_count} 章，但未从已分析内容中检索到与你问题相关的信息。"
                "相关内容可能在尚未分析的章节，也可以换种问法（提及具体人物/地点名）再试。"
            ),
        }
        yield {"type": "sources", "chapters": []}
        yield {"type": "done"}
        return

    context = "\n\n".join(context_parts)
    async for chunk in query_service._stream_final_answer(
        novel_id=novel_id,
        question=question,
        conversation_id=conversation_id,
        context=context,
        all_source_chapters=source_chapters,
        analyzed_count=analyzed_count,
    ):
        yield chunk
