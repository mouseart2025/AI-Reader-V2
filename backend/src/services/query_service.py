"""QA Pipeline: entity/keyword retrieval + LLM generation for novel Q&A."""

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

from src.db import chapter_fact_store, chapter_store, conversation_store
from src.infra.llm_client import get_llm_client
from src.services import embedding_service

logger = logging.getLogger(__name__)

_QA_SYSTEM_PROMPT = """你是一个专业的小说分析助手。你的任务是根据提供的小说知识库信息，回答用户关于小说内容的问题。

## 规则
1. **仅基于提供的信息回答**，不要编造不存在的情节或人物
2. 回答时引用来源章节，格式为 [第X章]
3. 如果信息不足以回答问题，诚实说明"根据已分析的内容，暂未找到相关信息"
4. 回答要简洁明了，重点突出
5. 在回答中提到人物、地点、物品等实体时，用其原名

## 知识库信息
{context}

## 对话历史
{history}

请回答用户的问题。"""


def _extract_entities_from_question(question: str, all_entities: set[str]) -> list[str]:
    """Extract known entity names from a question string."""
    found = []
    for name in sorted(all_entities, key=len, reverse=True):
        if name in question:
            found.append(name)
    return found


def _build_entity_context(
    facts: list[dict],
    entity_names: list[str],
    max_chars: int = 4000,
) -> tuple[str, list[int]]:
    """Build context from chapter facts mentioning given entities."""
    chunks: list[str] = []
    source_chapters: set[int] = set()
    total = 0

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)
        relevant_parts: list[str] = []

        # Characters
        for ch in fact.get("characters", []):
            if ch.get("name") in entity_names:
                parts = [f"人物「{ch['name']}」"]
                if ch.get("new_aliases"):
                    parts.append(f"别名: {', '.join(ch['new_aliases'])}")
                if ch.get("appearance"):
                    parts.append(f"外貌: {ch['appearance']}")
                if ch.get("abilities_gained"):
                    for ab in ch["abilities_gained"]:
                        parts.append(f"能力: {ab.get('name', '')} ({ab.get('dimension', '')})")
                relevant_parts.append(" | ".join(parts))

        # Relationships
        for rel in fact.get("relationships", []):
            a, b = rel.get("person_a", ""), rel.get("person_b", "")
            if a in entity_names or b in entity_names:
                evidence = rel.get("evidence", "")
                relevant_parts.append(
                    f"关系: {a} → {b}: {rel.get('relation_type', '')} ({evidence[:80]})"
                )

        # Locations
        for loc in fact.get("locations", []):
            if loc.get("name") in entity_names:
                desc = loc.get("description", "") or ""
                relevant_parts.append(
                    f"地点「{loc['name']}」类型={loc.get('type', '')} {desc[:60]}"
                )

        # Item events
        for ie in fact.get("item_events", []):
            if ie.get("item_name") in entity_names or ie.get("actor") in entity_names:
                relevant_parts.append(
                    f"物品: {ie.get('actor', '')} {ie.get('action', '')} {ie.get('item_name', '')} "
                    f"({ie.get('description', '')[:60]})"
                )

        # Org events
        for oe in fact.get("org_events", []):
            if oe.get("org_name") in entity_names or oe.get("member") in entity_names:
                relevant_parts.append(
                    f"组织: {oe.get('member', '')} {oe.get('action', '')} {oe.get('org_name', '')} "
                    f"角色={oe.get('role', '')}"
                )

        # Events mentioning entities
        for evt in fact.get("events", []):
            participants = evt.get("participants", [])
            if any(p in entity_names for p in participants):
                relevant_parts.append(
                    f"事件[{evt.get('type', '')}]: {evt.get('summary', '')[:100]}"
                )

        # Concepts
        for nc in fact.get("new_concepts", []):
            if nc.get("name") in entity_names:
                relevant_parts.append(
                    f"概念「{nc['name']}」: {nc.get('definition', '')[:80]}"
                )

        if relevant_parts:
            block = f"[第{chapter_id}章] " + " ‖ ".join(relevant_parts)
            if total + len(block) > max_chars:
                break
            chunks.append(block)
            source_chapters.add(chapter_id)
            total += len(block)

    return "\n".join(chunks), sorted(source_chapters)


def _build_keyword_context(
    facts: list[dict],
    keywords: list[str],
    max_chars: int = 2000,
) -> tuple[str, list[int]]:
    """Build context from events/summaries matching keywords."""
    chunks: list[str] = []
    source_chapters: set[int] = set()
    total = 0

    for fact_row in facts:
        fact = fact_row["fact"]
        chapter_id = fact.get("chapter_id", 0)

        for evt in fact.get("events", []):
            summary = evt.get("summary", "")
            if any(kw in summary for kw in keywords):
                block = f"[第{chapter_id}章] 事件: {summary}"
                if total + len(block) > max_chars:
                    return "\n".join(chunks), sorted(source_chapters)
                chunks.append(block)
                source_chapters.add(chapter_id)
                total += len(block)

    return "\n".join(chunks), sorted(source_chapters)


async def _build_text_context(
    novel_id: str,
    keywords: list[str],
    max_results: int = 5,
) -> tuple[str, list[int]]:
    """Build context from chapter text search."""
    chunks: list[str] = []
    source_chapters: set[int] = set()

    for kw in keywords[:3]:
        results = await chapter_store.search_chapters(novel_id, kw, limit=max_results)
        for r in results:
            ch_num = r["chapter_num"]
            if ch_num not in source_chapters:
                block = f"[第{ch_num}章] {r.get('title', '')}: ...{r['snippet']}..."
                chunks.append(block)
                source_chapters.add(ch_num)

    return "\n".join(chunks), sorted(source_chapters)


def _collect_all_entity_names(facts: list[dict]) -> set[str]:
    """Collect all entity names from all facts."""
    names: set[str] = set()
    for fact_row in facts:
        fact = fact_row["fact"]
        for ch in fact.get("characters", []):
            if ch.get("name"):
                names.add(ch["name"])
        for loc in fact.get("locations", []):
            if loc.get("name"):
                names.add(loc["name"])
        for ie in fact.get("item_events", []):
            if ie.get("item_name"):
                names.add(ie["item_name"])
        for oe in fact.get("org_events", []):
            if oe.get("org_name"):
                names.add(oe["org_name"])
        for nc in fact.get("new_concepts", []):
            if nc.get("name"):
                names.add(nc["name"])
    return names


def _build_history_text(messages: list[dict], max_turns: int = 5) -> str:
    """Build conversation history text from recent messages."""
    if not messages:
        return "（无历史对话）"
    recent = messages[-(max_turns * 2):]
    lines = []
    for msg in recent:
        role = "用户" if msg["role"] == "user" else "助手"
        content = msg["content"][:200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _extract_source_chapters(answer: str) -> list[int]:
    """Extract [第X章] references from the answer text."""
    matches = re.findall(r"第(\d+)章", answer)
    return sorted(set(int(m) for m in matches))


async def query_stream(
    novel_id: str,
    question: str,
    conversation_id: str | None = None,
) -> AsyncIterator[dict]:
    """
    Stream QA response.

    Yields dicts:
      {"type": "token", "content": str}     — streamed answer tokens
      {"type": "sources", "chapters": [...]} — source chapters when done
      {"type": "done"}                       — signal completion
    """
    llm = get_llm_client()

    # 1. Load all chapter facts for the novel
    all_facts = await chapter_fact_store.get_all_chapter_facts(novel_id)
    if not all_facts:
        yield {"type": "token", "content": "该小说尚未进行分析，请先分析后再提问。"}
        yield {"type": "sources", "chapters": []}
        yield {"type": "done"}
        return

    # 2. Extract entities from question
    all_entity_names = _collect_all_entity_names(all_facts)
    question_entities = _extract_entities_from_question(question, all_entity_names)

    # 3. Build context from multiple sources
    context_parts: list[str] = []
    all_source_chapters: set[int] = set()

    # Entity-based retrieval (weight: high)
    if question_entities:
        entity_ctx, entity_chs = _build_entity_context(all_facts, question_entities)
        if entity_ctx:
            context_parts.append("### 实体相关信息\n" + entity_ctx)
            all_source_chapters.update(entity_chs)

    # Keyword retrieval from events
    keywords = [w for w in question.split() if len(w) >= 2]
    if not keywords:
        keywords = [question]
    keyword_ctx, kw_chs = _build_keyword_context(all_facts, keywords)
    if keyword_ctx:
        context_parts.append("### 相关事件\n" + keyword_ctx)
        all_source_chapters.update(kw_chs)

    # Semantic search via ChromaDB embeddings
    try:
        semantic_results = embedding_service.search_chapters(novel_id, question, n_results=5)
        if semantic_results:
            sem_chunks = []
            for sr in semantic_results:
                ch_num = sr["chapter_num"]
                if ch_num not in all_source_chapters:
                    # Truncate document to first 200 chars
                    doc_snippet = sr["document"][:200]
                    sem_chunks.append(f"[第{ch_num}章] {doc_snippet}")
                    all_source_chapters.add(ch_num)
            if sem_chunks:
                context_parts.append("### 语义相关段落\n" + "\n".join(sem_chunks))
    except Exception as e:
        logger.debug("Semantic search unavailable: %s", e)

    # Full-text search in chapter content
    text_ctx, text_chs = await _build_text_context(novel_id, keywords)
    if text_ctx:
        context_parts.append("### 原文片段\n" + text_ctx)
        all_source_chapters.update(text_chs)

    context = "\n\n".join(context_parts) if context_parts else "（暂无相关知识库信息）"

    # 4. Build conversation history
    history_text = "（无历史对话）"
    if conversation_id:
        recent = await conversation_store.get_recent_messages(conversation_id, limit=6)
        history_text = _build_history_text(recent)

    # 5. Build final prompt
    system_prompt = _QA_SYSTEM_PROMPT.format(
        context=context,
        history=history_text,
    )

    analyzed_count = len(all_facts)
    user_prompt = f"{question}\n\n（注：当前已分析 {analyzed_count} 章内容）"

    # 6. Stream LLM response
    full_answer = ""
    try:
        async for token in llm.generate_stream(
            system=system_prompt,
            prompt=user_prompt,
            timeout=60,
        ):
            full_answer += token
            yield {"type": "token", "content": token}
    except Exception as e:
        logger.error(f"LLM streaming error: {e}")
        error_msg = "抱歉，生成回答时出现错误，请稍后重试。"
        yield {"type": "token", "content": error_msg}
        full_answer = error_msg

    # 7. Extract source chapters from answer
    answer_sources = _extract_source_chapters(full_answer)
    # Merge with retrieval sources
    final_sources = sorted(set(answer_sources) | all_source_chapters)

    yield {"type": "sources", "chapters": final_sources}
    yield {"type": "done"}

    # 8. Save messages to DB if conversation exists
    if conversation_id:
        try:
            await conversation_store.add_message(
                conversation_id, "user", question
            )
            await conversation_store.add_message(
                conversation_id,
                "assistant",
                full_answer,
                sources_json=json.dumps(final_sources),
            )
        except Exception as e:
            logger.error(f"Failed to save messages: {e}")
