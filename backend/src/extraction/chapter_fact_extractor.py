"""ChapterFact extractor: sends chapter text to LLM and parses structured output."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.infra.anthropic_client import AnthropicClient
from src.infra.context_budget import get_budget
from src.infra.llm_client import LLMError, LlmUsage, get_llm_client
from src.infra.openai_client import OpenAICompatibleClient
from src.models.chapter_fact import ChapterFact, CharacterFact, RelationshipFact
from src.services.relation_utils import derive_category_from_dimensions

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Segment splitting thresholds (chars). Only used when budget.segment_enabled.
_SEGMENT_THRESHOLD_2 = 7000   # >7000 chars -> split into 2 segments
_SEGMENT_THRESHOLD_3 = 12000  # >12000 chars -> split into 3 segments

# ── Relation dimension schema v1 (FR-1.2/FR-1.3) ──
# Value tables frozen in docs/analysis/relation-dimension-schema-v1.md.
# rel_subtype validity is checked via derive_category_from_dimensions (single
# source of truth in relation_utils._REL_SUBTYPE_CATEGORY).
_VALID_POLARITY = frozenset({"positive", "negative", "neutral"})
_VALID_CLOSENESS = frozenset({"close", "distant", "unknown"})

# Conservative class for vote ties (FR-1.3): the lowest-priority generic social
# default "朋友-社交". On a tie, the candidate latest in this order wins, so
# 朋友-社交 (last) is the terminal conservative fallback.
_SUBTYPE_TIE_BREAK_ORDER: list[str] = [
    "辈分-亲属", "结拜", "婚恋", "师门-师徒", "主从", "君臣-上下级",
    "师门-同门", "爱慕", "同盟", "恩怨-报恩", "敌对", "其他", "朋友-社交",
]
CONSERVATIVE_SUBTYPE = "朋友-社交"

# Structured-output schema for the lightweight rel_subtype vote call (FR-1.3).
_VOTE_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "votes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "rel_subtype": {"type": "string"},
                },
                "required": ["index", "rel_subtype"],
            },
        }
    },
    "required": ["votes"],
}


@dataclass
class ExtractionMeta:
    """Quality metadata about the extraction process."""
    is_truncated: bool = False
    original_len: int = 0
    truncated_len: int = 0
    segment_count: int = 1
    # FR-3.1 证据锚定统计(副产物标记,仅 EVIDENCE_GROUNDING_ENABLED 时填充)
    evidence_missing_relations: int = 0
    evidence_missing_events: int = 0
    evidence_unlocated_spans: int = 0


class ExtractionError(Exception):
    """Raised when chapter fact extraction fails after retries."""


def _split_chapter_text(text: str) -> list[str]:
    """Split long chapter text into segments at paragraph boundaries.

    Returns a list of 1-3 segments depending on text length.
    """
    text_len = len(text)
    if text_len <= _SEGMENT_THRESHOLD_2:
        return [text]

    num_parts = 3 if text_len > _SEGMENT_THRESHOLD_3 else 2

    # Find paragraph break points (double newline or single newline)
    breaks: list[int] = []
    for i, ch in enumerate(text):
        if ch == "\n" and i > 0:
            breaks.append(i)

    if not breaks:
        # No paragraph breaks — split by character count
        seg_len = text_len // num_parts
        return [text[i * seg_len: (i + 1) * seg_len if i < num_parts - 1 else text_len]
                for i in range(num_parts)]

    # Pick break points closest to ideal split positions
    segments: list[str] = []
    prev = 0
    for part_idx in range(1, num_parts):
        ideal = text_len * part_idx // num_parts
        # Find the paragraph break closest to ideal position
        best = min(breaks, key=lambda b: abs(b - ideal))
        segments.append(text[prev:best].strip())
        prev = best
    segments.append(text[prev:].strip())

    return [s for s in segments if s]  # remove empty segments


def _merge_chapter_facts(
    facts: list[ChapterFact],
    novel_id: str,
    chapter_id: int,
) -> ChapterFact:
    """Merge multiple segment ChapterFacts into one, deduplicating entries."""
    if len(facts) == 1:
        return facts[0]

    # Characters: merge by name, combine aliases/locations/abilities
    char_map: dict[str, CharacterFact] = {}
    for fact in facts:
        for ch in fact.characters:
            if ch.name in char_map:
                existing = char_map[ch.name]
                char_map[ch.name] = CharacterFact(
                    name=ch.name,
                    new_aliases=list(dict.fromkeys(existing.new_aliases + ch.new_aliases)),
                    appearance=existing.appearance or ch.appearance,
                    abilities_gained=existing.abilities_gained + ch.abilities_gained,
                    locations_in_chapter=list(dict.fromkeys(
                        existing.locations_in_chapter + ch.locations_in_chapter
                    )),
                )
            else:
                char_map[ch.name] = ch

    # Relationships: deduplicate by (person_a, person_b, relation_type)
    rel_seen: set[tuple[str, str, str]] = set()
    relationships = []
    for fact in facts:
        for rel in fact.relationships:
            key = (rel.person_a, rel.person_b, rel.relation_type)
            if key not in rel_seen:
                rel_seen.add(key)
                relationships.append(rel)

    # Locations: deduplicate by name, prefer entry with more info
    loc_map: dict[str, object] = {}
    for fact in facts:
        for loc in fact.locations:
            if loc.name not in loc_map:
                loc_map[loc.name] = loc
            elif loc.description and not loc_map[loc.name].description:
                loc_map[loc.name] = loc

    # Spatial relationships: deduplicate by (source, target, relation_type)
    sp_seen: set[tuple[str, str, str]] = set()
    spatial_relationships = []
    for fact in facts:
        for sr in fact.spatial_relationships:
            key = (sr.source, sr.target, sr.relation_type)
            if key not in sp_seen:
                sp_seen.add(key)
                spatial_relationships.append(sr)

    # Events: deduplicate by summary similarity (exact match)
    event_seen: set[str] = set()
    events = []
    for fact in facts:
        for ev in fact.events:
            if ev.summary not in event_seen:
                event_seen.add(ev.summary)
                events.append(ev)

    # Simple concatenation for item_events, org_events (rare duplicates)
    item_events = []
    for fact in facts:
        item_events.extend(fact.item_events)
    org_events = []
    for fact in facts:
        org_events.extend(fact.org_events)

    # New concepts: deduplicate by name
    concept_map: dict[str, object] = {}
    for fact in facts:
        for c in fact.new_concepts:
            if c.name not in concept_map or (c.definition and len(c.definition) > len(concept_map[c.name].definition)):
                concept_map[c.name] = c

    # World declarations: deduplicate by (type, key content)
    wd_seen: set[str] = set()
    world_declarations = []
    for fact in facts:
        for wd in fact.world_declarations:
            key = f"{wd.declaration_type}:{json.dumps(wd.content, sort_keys=True, ensure_ascii=False)}"
            if key not in wd_seen:
                wd_seen.add(key)
                world_declarations.append(wd)

    return ChapterFact(
        chapter_id=chapter_id,
        novel_id=novel_id,
        characters=list(char_map.values()),
        relationships=relationships,
        locations=list(loc_map.values()),
        spatial_relationships=spatial_relationships,
        item_events=item_events,
        org_events=org_events,
        events=events,
        new_concepts=list(concept_map.values()),
        world_declarations=world_declarations,
    )


# ── 两遍制 recall pass (FR-4.1) ──
# 首遍抽取后对每章再跑一次"查漏"调用:输入首遍结果清单 + 原文,只补漏。
# 补漏记录标记 source="recall_pass",经与首遍相同的 sanitize 后并入。

_RECALL_SYSTEM_PROMPT = (
    "你是小说章节信息抽取的查漏专家。给你首遍抽取的结果清单与章节原文,"
    "你的任务是【只补漏】:找出首遍遗漏的人物、人物关系、事件。\n"
    "规则:\n"
    "1. 只输出首遍清单中没有的新记录,禁止重复已有内容,禁止修改或删除已有结论。\n"
    "2. 每条新记录必须有原文依据;没有遗漏就输出空列表。\n"
    "3. relationships 与 events 每条都必须附 evidence 字段,逐字引用原文片段(不得改写)。\n"
    "4. 只输出 JSON,不要输出多余文本。"
)


def _build_recall_schema() -> dict:
    """查漏调用的输出 schema:ChapterFact 全形,但允许空列表(无遗漏是常态),
    并隐藏内部字段(subtype_vote/source),LLM 只产出人物/关系/事件内容。"""
    schema = ChapterFact.model_json_schema()
    defs = schema.get("$defs", {})
    if "RelationshipFact" in defs:
        defs["RelationshipFact"].get("properties", {}).pop("subtype_vote", None)
    for model in ("CharacterFact", "RelationshipFact", "EventFact"):
        if model in defs:
            defs[model].get("properties", {}).pop("source", None)
    # 与首遍 schema 口径一致 (FR-3.1):证据锚定开启时去掉 evidence 默认值,
    # 让 LLM 把 evidence 当作必产字段。
    from src.infra.config import EVIDENCE_GROUNDING_ENABLED
    if EVIDENCE_GROUNDING_ENABLED:
        for model in ("RelationshipFact", "EventFact"):
            if model in defs:
                props = defs[model].get("properties", {})
                if "evidence" in props:
                    props["evidence"].pop("default", None)
    return schema


def _build_recall_user_prompt(
    chapter_id: int, chapter_text: str, fact: ChapterFact,
) -> str:
    """构造查漏 user prompt:首遍结果清单(紧凑) + 原文。"""
    char_names = "、".join(ch.name for ch in fact.characters) or "(无)"
    rel_lines = "\n".join(
        f"  - {rel.person_a} —{rel.relation_type}→ {rel.person_b}"
        for rel in fact.relationships
    ) or "  (无)"
    event_lines = "\n".join(
        f"  - {ev.summary}" for ev in fact.events
    ) or "  (无)"
    return (
        "## 首遍抽取结果清单\n"
        f"人物: {char_names}\n"
        f"关系:\n{rel_lines}\n"
        f"事件:\n{event_lines}\n\n"
        f"## 第 {chapter_id} 章原文\n\n{chapter_text}\n\n"
        "【任务】对照原文检查上述清单,只输出遗漏的 characters / relationships / events;"
        "已在清单中的内容不要重复输出;没有遗漏的类别输出空列表。"
    )


def _merge_recall_additions(
    fact: ChapterFact, recall_fact: ChapterFact, chapter_id: int,
) -> dict[str, int]:
    """把查漏结果并入首遍结果 (FR-4.1):只增不改。

    - 首遍已有记录一律不动(首遍结果不被改写);
    - 新增记录打上 source="recall_pass" 标记,可追溯来源;
    - 去重口径与 _merge_chapter_facts 一致(人物按名、关系按三元组、事件按 summary)。
    """
    counts = {"characters": 0, "relationships": 0, "events": 0}

    existing_names = {ch.name for ch in fact.characters}
    for ch in recall_fact.characters:
        if ch.name and ch.name not in existing_names:
            fact.characters.append(ch.model_copy(update={"source": "recall_pass"}))
            existing_names.add(ch.name)
            counts["characters"] += 1

    seen_rels = {
        (rel.person_a, rel.person_b, rel.relation_type) for rel in fact.relationships
    }
    for rel in recall_fact.relationships:
        key = (rel.person_a, rel.person_b, rel.relation_type)
        if key not in seen_rels:
            fact.relationships.append(rel.model_copy(update={"source": "recall_pass"}))
            seen_rels.add(key)
            counts["relationships"] += 1

    seen_events = {ev.summary for ev in fact.events}
    for ev in recall_fact.events:
        if ev.summary and ev.summary not in seen_events:
            fact.events.append(ev.model_copy(update={"source": "recall_pass"}))
            seen_events.add(ev.summary)
            counts["events"] += 1

    if any(counts.values()):
        logger.info(
            "Chapter %d: recall pass 补漏 %d 人物 / %d 关系 / %d 事件",
            chapter_id, counts["characters"], counts["relationships"], counts["events"],
        )
    return counts


# ── 独立二审 source pass (multi-pass Epic 2, issue #70) ──
# 对已分析完的小说做一遍独立重读:独立 system prompt(prompts/source_pass_system.txt)
# + 与主抽取同构的 schema(保证 diff 可比) + 独立 user prompt。产物统一打
# source="source_pass" 溯源标记,落入 pass_chapter_facts 影子表,不进主表。

def _build_source_pass_schema() -> dict:
    """独立二审的输出 schema:与主抽取同构(字段集一致,diff 前提),
    但不加 minItems 非空约束 —— 二审宁可报告不确定也不猜测,空列表是合法输出。
    内部字段(subtype_vote/source)对 LLM 隐藏,由管线打点。"""
    schema = ChapterFact.model_json_schema()
    defs = schema.get("$defs", {})
    if "RelationshipFact" in defs:
        defs["RelationshipFact"].get("properties", {}).pop("subtype_vote", None)
    for model in ("CharacterFact", "RelationshipFact", "EventFact"):
        if model in defs:
            defs[model].get("properties", {}).pop("source", None)
    # 与主抽取 schema 口径一致 (FR-3.1):证据锚定开启时去掉 evidence 默认值
    from src.infra.config import EVIDENCE_GROUNDING_ENABLED
    if EVIDENCE_GROUNDING_ENABLED:
        for model in ("RelationshipFact", "EventFact"):
            if model in defs:
                props = defs[model].get("properties", {})
                if "evidence" in props:
                    props["evidence"].pop("default", None)
    return schema


def _build_source_pass_user_prompt(
    chapter_id: int, chapter_text: str, example_text: str,
    segment_hint: str = "",
) -> str:
    """构造独立二审 user prompt:原文 + 二审口径要求(独立阅读、报告不确定)。

    签名与 ChapterFactExtractor._build_user_prompt 一致,可直接作为
    prompt_builder 注入 _extract_single / _extract_segmented。
    """
    from src.infra.config import EVIDENCE_GROUNDING_ENABLED
    evidence_req = (
        "6. evidence：relationships 和 events 每条都必须附 evidence 字段，"
        "逐字引用原文片段（不得改写），无原文依据的条目不得输出\n"
        if EVIDENCE_GROUNDING_ENABLED else ""
    )
    return (
        f"{example_text}"
        f"## 第 {chapter_id} 章{segment_hint}\n\n{chapter_text}\n\n"
        "【二审要求】\n"
        "1. 你在进行【独立二审】：只依据本章原文与给定的二审上下文，不要推测任何未给出的信息\n"
        "2. characters / relationships / locations / events 等只提取原文明确写到的内容\n"
        "3. 不确定的条目宁可不写，也不要猜测；没有把握的关系/事件直接省略\n"
        "4. 如果某类内容本章确实没有，输出空列表，不要为凑数而编造\n"
        "5. spatial_relationships / world_declarations / new_concepts 口径同常规提取，没有则输出空列表\n"
        f"{evidence_req}"
    )


# ── Citation-grounded 证据锚定 (FR-3.1) ──

def span_located(span: str, chapter_text: str) -> bool:
    """证据 span 是否能在原章节文本中定位(子串匹配,归一化空白与引号)。

    归一化方式:去除所有空白字符与引号字符后做子串匹配,容忍 LLM 引用时
    的换行/空格差异与引号样式改写(“” ↔ '' 等,真实冒烟实测高发)。
    judge 脚本(FR-3.2)复用同一实现,保证口径唯一。
    """
    norm_span = _normalize_span_text(span)
    if not norm_span:
        return False
    return norm_span in _normalize_span_text(chapter_text)


# LLM 逐字引用时经常改写引号样式(“” ↔ '' ↔ ""),定位时与空白一样归一化掉。
_SPAN_QUOTE_CHARS = "“”‘’\"'「」『』"


def _normalize_span_text(s: str) -> str:
    """span 定位归一化:去空白 + 去引号(span_located 唯一口径)。"""
    return "".join(
        ch for ch in s if not ch.isspace() and ch not in _SPAN_QUOTE_CHARS
    )


# 事件置信度(importance)降级次序:无证据的事件降一级,low 不再降
_IMPORTANCE_DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}


def _sanitize_evidence_grounding(
    fact: ChapterFact,
    chapter_id: int,
    chapter_text: str,
    meta: ExtractionMeta | None = None,
) -> None:
    """证据锚定清洗 (FR-3.1):无 evidence 的记录降置信度并标记。

    - 空 evidence:打 warning 日志并计入 meta 统计;事件额外将 importance
      降一级(high→medium→low)作为降置信度。关系无置信度字段,标记走
      日志 + ExtractionMeta 计数(副产物)。
    - 非空但无法在原文定位的 span:打 warning 日志并计数(不删改记录,
      留待 judge/人工复核)。
    """
    for rel in fact.relationships:
        rel.evidence = rel.evidence.strip()
        if not rel.evidence:
            if meta is not None:
                meta.evidence_missing_relations += 1
            logger.warning(
                "Chapter %d: relationship %s-%s(%s) 缺少 evidence,已标记",
                chapter_id, rel.person_a, rel.person_b, rel.relation_type,
            )
        elif not span_located(rel.evidence, chapter_text):
            if meta is not None:
                meta.evidence_unlocated_spans += 1
            logger.warning(
                "Chapter %d: relationship %s-%s evidence 无法在原文定位: %r",
                chapter_id, rel.person_a, rel.person_b, rel.evidence[:50],
            )
    for ev in fact.events:
        ev.evidence = ev.evidence.strip()
        if not ev.evidence:
            if meta is not None:
                meta.evidence_missing_events += 1
            downgraded = _IMPORTANCE_DOWNGRADE.get(ev.importance, "low")
            logger.warning(
                "Chapter %d: event %r 缺少 evidence,importance %s→%s 并标记",
                chapter_id, ev.summary[:30], ev.importance, downgraded,
            )
            ev.importance = downgraded
        elif not span_located(ev.evidence, chapter_text):
            if meta is not None:
                meta.evidence_unlocated_spans += 1
            logger.warning(
                "Chapter %d: event %r evidence 无法在原文定位: %r",
                chapter_id, ev.summary[:30], ev.evidence[:50],
            )


def _sanitize_relation_dimensions(fact: ChapterFact, chapter_id: int) -> None:
    """Reject out-of-vocabulary dimension values on relationships (FR-1.2).

    Value tables are frozen in docs/analysis/relation-dimension-schema-v1.md.
    Invalid values are reset to None (so downstream falls back to the legacy
    relation_type path) and logged — dirty values never reach the store.
    """
    for rel in fact.relationships:
        if rel.polarity is not None:
            rel.polarity = rel.polarity.strip()
            if rel.polarity not in _VALID_POLARITY:
                logger.warning(
                    "Chapter %d: invalid polarity %r for %s-%s, reset to None",
                    chapter_id, rel.polarity, rel.person_a, rel.person_b,
                )
                rel.polarity = None
        if rel.rel_subtype is not None:
            rel.rel_subtype = rel.rel_subtype.strip()
            if derive_category_from_dimensions(rel.rel_subtype) is None:
                logger.warning(
                    "Chapter %d: invalid rel_subtype %r for %s-%s, reset to None",
                    chapter_id, rel.rel_subtype, rel.person_a, rel.person_b,
                )
                rel.rel_subtype = None
        if rel.closeness is not None:
            rel.closeness = rel.closeness.strip()
            if rel.closeness not in _VALID_CLOSENESS:
                logger.warning(
                    "Chapter %d: invalid closeness %r for %s-%s, reset to None",
                    chapter_id, rel.closeness, rel.person_a, rel.person_b,
                )
                rel.closeness = None


def _pick_majority_subtype(counts: dict[str, int]) -> str:
    """Majority vote winner; ties go to the most conservative candidate.

    Conservative = latest in _SUBTYPE_TIE_BREAK_ORDER, with the generic social
    default 朋友-社交 (CONSERVATIVE_SUBTYPE) as the terminal fallback (FR-1.3).
    """
    top = max(counts.values())
    tied = [s for s, c in counts.items() if c == top]
    if len(tied) == 1:
        return tied[0]
    return max(
        tied,
        key=lambda s: _SUBTYPE_TIE_BREAK_ORDER.index(s)
        if s in _SUBTYPE_TIE_BREAK_ORDER else -1,
    )


def _parse_vote_response(
    result: object, n_relations: int, chapter_id: int,
) -> dict[int, str]:
    """Parse one vote-sample response into {relation_index: rel_subtype}.

    Out-of-vocabulary subtypes and out-of-range indexes are dropped and logged.
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            logger.warning(
                "Chapter %d: vote response not parseable as JSON, sample dropped",
                chapter_id,
            )
            return {}
    if isinstance(result, dict):
        items = result.get("votes", [])
    elif isinstance(result, list):
        items = result
    else:
        return {}

    votes: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        subtype = item.get("rel_subtype")
        if not isinstance(idx, int) or not (0 <= idx < n_relations):
            continue
        if not isinstance(subtype, str) or derive_category_from_dimensions(subtype.strip()) is None:
            logger.warning(
                "Chapter %d: vote sample returned invalid rel_subtype %r, dropped",
                chapter_id, subtype,
            )
            continue
        votes[idx] = subtype.strip()
    return votes


# Genre-specific context injected into the extraction system prompt.
# Helps the LLM understand domain-specific naming patterns.
_GENRE_CONTEXT: dict[str, str] = {
    "fantasy": "\n- **题材提示**：这是一部修仙/奇幻小说。仙人、妖兽、灵兽、魔尊等是常见角色类型，应当提取。洞府、秘境、仙界、魔界等是合法地点。\n",
    "wuxia": "\n- **题材提示**：这是一部武侠小说。大侠、掌门、帮主等可能是有名字的角色。总舵、分舵、密道等是合法地点。\n",
    "historical": "\n- **题材提示**：这是一部历史/古典小说。太尉、知府等官职后有姓氏时是角色名。衙门、宫殿等是合法地点。\n",
    "realistic": "\n- **题材提示**：这是一部现实主义小说。队长、书记等通常是职务泛称而非人名。注意区分真实人名和职务称呼。\n",
    "urban": "\n- **题材提示**：这是一部都市小说。注意区分公司名、品牌名和地点名。\n",
}


def _load_system_prompt() -> str:
    from src.extraction.prompt_registry import get_prompt
    return get_prompt("extraction_system")


def _load_vot_guide() -> str:
    """Load VoT spatial reasoning guide. Returns empty string if file missing."""
    from src.extraction.prompt_registry import get_prompt
    try:
        return get_prompt("vot_spatial_guide")
    except FileNotFoundError:
        return ""


def _load_dimension_guide() -> str:
    """Load relation dimension schema guide (FR-1.2). Returns empty string if file missing."""
    from src.extraction.prompt_registry import get_prompt
    try:
        return get_prompt("relation_dimensions_guide")
    except FileNotFoundError:
        return ""


def _load_evidence_guide() -> str:
    """Load evidence grounding guide (FR-3.1). Returns empty string if file missing."""
    from src.extraction.prompt_registry import get_prompt
    try:
        return get_prompt("evidence_grounding_guide")
    except FileNotFoundError:
        return ""


def _load_examples() -> list[dict]:
    from src.extraction.prompt_registry import get_prompt_json
    return get_prompt_json("extraction_examples")


def _load_source_pass_system_prompt() -> str:
    """Load source-pass (独立二审, multi-pass Epic 2) system prompt.

    缺失时回退到主抽取 prompt(打包兜底)。
    """
    from src.extraction.prompt_registry import get_prompt
    try:
        return get_prompt("source_pass_system")
    except FileNotFoundError:
        return _load_system_prompt()


def _build_extraction_schema() -> dict:
    """Build a customized JSON schema with stricter constraints for better LLM output."""
    schema = ChapterFact.model_json_schema()

    # Remove $defs reference layer if present — flatten for simpler LLM consumption
    # Add minItems hints to encourage non-empty arrays
    defs = schema.get("$defs", {})

    # Patch EventFact: require participants with minItems=1
    if "EventFact" in defs:
        props = defs["EventFact"].get("properties", {})
        if "participants" in props:
            props["participants"]["minItems"] = 1
            props["participants"].pop("default", None)
        if "location" in props:
            # Remove default null to encourage filling
            props["location"].pop("default", None)

    # Patch RelationshipFact: subtype_vote is internal vote metadata (FR-1.3),
    # not something the LLM should produce — hide it from the output schema.
    if "RelationshipFact" in defs:
        defs["RelationshipFact"].get("properties", {}).pop("subtype_vote", None)

    # Patch source fields (FR-4.1): source 是管线内部溯源标记(main/recall_pass),
    # 由合并逻辑打点,LLM 不应输出 — 对所有模型隐藏,schema 与 v0.73 口径一致。
    for model in ("CharacterFact", "RelationshipFact", "EventFact"):
        if model in defs:
            defs[model].get("properties", {}).pop("source", None)

    # Patch evidence fields (FR-3.1): remove the "" default so the LLM treats
    # evidence as expected output rather than optional-with-default. Only when
    # the grounding switch is on — off keeps the v0.73 schema byte-identical.
    from src.infra.config import EVIDENCE_GROUNDING_ENABLED
    if EVIDENCE_GROUNDING_ENABLED:
        for model in ("RelationshipFact", "EventFact"):
            if model in defs:
                props = defs[model].get("properties", {})
                if "evidence" in props:
                    props["evidence"].pop("default", None)

    # Patch ChapterFact: require non-empty characters, relationships, locations, events
    root_props = schema.get("properties", {})
    for field in ("characters", "relationships", "locations", "events"):
        if field in root_props:
            root_props[field]["minItems"] = 1
            root_props[field].pop("default", None)

    return schema


class ChapterFactExtractor:
    """Extract structured ChapterFact from a single chapter using LLM."""

    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()
        self.system_template = _load_system_prompt()
        self._vot_guide = _load_vot_guide()
        self._dimension_guide = _load_dimension_guide()
        self._evidence_guide = _load_evidence_guide()
        self.examples = _load_examples()
        self.source_pass_template = _load_source_pass_system_prompt()
        self._schema = _build_extraction_schema()
        self._is_cloud = isinstance(self.llm, (OpenAICompatibleClient, AnthropicClient))

    def _build_example_text(self) -> str:
        """Build the few-shot examples section for the user prompt.

        For small context windows (≤16K), only 1 example is sent to save ~1.2K
        tokens of input budget. When RELATION_DIMENSIONS_ENABLED is off, the
        dimension fields are stripped from the examples so the prompt stays
        identical to the pre-dimension version (NFR-3). Likewise, when
        EVIDENCE_GROUNDING_ENABLED is off, the event evidence fields (FR-3.1)
        are stripped — v0.73 examples had no event evidence.
        """
        if not self.examples:
            return ""
        from src.infra import config as _cfg
        examples = self.examples
        if not _cfg.RELATION_DIMENSIONS_ENABLED or not _cfg.EVIDENCE_GROUNDING_ENABLED:
            examples = json.loads(json.dumps(self.examples))  # deep copy
            if not _cfg.RELATION_DIMENSIONS_ENABLED:
                for ex in examples:
                    for rel in ex.get("relationships", []):
                        for key in ("polarity", "rel_subtype", "closeness"):
                            rel.pop(key, None)
            if not _cfg.EVIDENCE_GROUNDING_ENABLED:
                for ex in examples:
                    for ev in ex.get("events", []):
                        ev.pop("evidence", None)
        budget = get_budget()
        examples_to_show = [examples[0]]
        if len(examples) >= 4 and budget.context_window > 16384:
            examples_to_show.append(examples[3])
        examples_json = json.dumps(examples_to_show, ensure_ascii=False, indent=2)
        return f"## 参考示例\n```json\n{examples_json}\n```\n\n"

    def _build_user_prompt(
        self, chapter_id: int, chapter_text: str, example_text: str,
        segment_hint: str = "",
    ) -> str:
        """Build the user prompt for a chapter or chapter segment."""
        from src.infra.config import EVIDENCE_GROUNDING_ENABLED
        evidence_req = (
            "9. evidence：relationships 和 events 每条都必须附 evidence 字段，"
            "逐字引用原文片段（不得改写），无原文依据的条目不得输出\n"
            if EVIDENCE_GROUNDING_ENABLED else ""
        )
        return (
            f"{example_text}"
            f"## 第 {chapter_id} 章{segment_hint}\n\n{chapter_text}\n\n"
            "【关键要求】\n"
            "1. characters：宁多勿漏！包含所有有名字或固定称呼的人物。种族/物种名称作为称呼且有具体行为的角色也算（如赤尻马猴、通背猿猴）\n"
            "2. relationships：任何两个人物有互动或提及关系都必须提取，evidence 引用原文。命令/差遣/听令也是关系\n"
            "3. locations：宁多勿漏！所有具体地名都必须提取，即使只被简短提及也不可跳过\n"
            "4. events：每个事件的 participants 列出参与者姓名，location 填写地点，都不可为空\n"
            "5. spatial_relationships：提取地点间的方位(direction)、距离(distance)、包含(contains)、相邻(adjacent)、分隔(separated_by)、地形(terrain)、夹在中间(in_between)、移动路径(travel_path)、规模对比(relative_scale)、聚类(cluster)关系。可选填 distance_class(near/medium/far/very_far) 和 confidence_score(0.0-1.0)\n"
            "6. world_declarations：当文中有世界宏观结构描述时必须提取（区域划分region_division、区域方位region_position、空间层layer_exists如天界/地府/海底、传送通道portal），没有则输出空列表\n"
            "7. new_concepts：功法、丹药、修炼体系、世界观规则等首次出现或有详细介绍的概念，definition 必须详细（2-5句话）\n"
            "8. 只提取原文明确出现的内容，禁止编造\n"
            f"{evidence_req}"
        )

    async def extract(
        self,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        context_summary: str = "",
        genre_hint: str | None = None,
    ) -> tuple[ChapterFact, LlmUsage, ExtractionMeta]:
        """Extract ChapterFact from chapter text. Returns (fact, usage, meta).

        Long chapters (cloud mode) are automatically split into segments
        and merged to avoid output truncation.
        """
        # Inject genre context into system prompt
        genre_context = _GENRE_CONTEXT.get(genre_hint, "") if genre_hint else ""
        system = self.system_template.replace("{genre_context}", genre_context)
        system = system.replace("{context}", context_summary or "（无前序上下文）")

        budget = get_budget()
        vot_injected = False

        # Inject VoT spatial reasoning guide — gated by context window size
        from src.infra.config import VOT_SPATIAL_ENABLED
        if VOT_SPATIAL_ENABLED and self._vot_guide:
            if budget.context_window <= 8192:
                logger.info("VoT spatial guide skipped: context window %d <= 8192", budget.context_window)
            else:
                marker = "## 空间关系提取规则"
                if marker in system:
                    system = system.replace(
                        marker,
                        self._vot_guide + "\n\n" + marker,
                    )
                    vot_injected = True
                else:
                    logger.warning("VoT injection skipped: marker %r not found in system prompt", marker)

        # Inject relation dimension guide (FR-1.2) — gated by config switch (NFR-3).
        # When RELATION_DIMENSIONS_ENABLED is False the prompt stays byte-identical
        # to the pre-dimension version.
        from src.infra.config import RELATION_DIMENSIONS_ENABLED
        if RELATION_DIMENSIONS_ENABLED and self._dimension_guide:
            marker = "## 地点提取规则"
            if marker in system:
                system = system.replace(marker, self._dimension_guide + "\n\n" + marker, 1)
            else:
                logger.warning("Dimension guide injection skipped: marker %r not found in system prompt", marker)

        # Inject evidence grounding guide (FR-3.1) — gated by config switch (NFR-3).
        # When EVIDENCE_GROUNDING_ENABLED is False the prompt stays byte-identical
        # to the v0.73 version.
        from src.infra.config import EVIDENCE_GROUNDING_ENABLED
        if EVIDENCE_GROUNDING_ENABLED and self._evidence_guide:
            marker = "## 地点提取规则"
            if marker in system:
                system = system.replace(marker, self._evidence_guide + "\n\n" + marker, 1)
            else:
                logger.warning("Evidence guide injection skipped: marker %r not found in system prompt", marker)

        original_len = len(chapter_text)
        meta = ExtractionMeta(original_len=original_len)

        # VoT-aware chapter truncation: subtract VoT guide chars from budget
        effective_max_len = budget.max_chapter_len
        if vot_injected:
            effective_max_len = max(budget.max_chapter_len - len(self._vot_guide), budget.max_chapter_len // 2)
            logger.debug("VoT-aware truncation: max_chapter_len %d -> effective %d", budget.max_chapter_len, effective_max_len)

        # Truncate very long chapters to avoid token overflow
        if len(chapter_text) > effective_max_len:
            chapter_text = chapter_text[:effective_max_len]
            meta.is_truncated = True
            meta.truncated_len = len(chapter_text)

        # Split long chapters into segments (enabled for large context windows)
        if budget.segment_enabled:
            segments = _split_chapter_text(chapter_text)
        else:
            segments = [chapter_text]

        meta.segment_count = len(segments)

        if len(segments) > 1:
            logger.info(
                "Chapter %d: splitting %d chars into %d segments (%s)",
                chapter_id, len(chapter_text), len(segments),
                ", ".join(f"{len(s)}c" for s in segments),
            )
            fact, usage = await self._extract_segmented(
                system, novel_id, chapter_id, segments,
            )
        else:
            # Single segment — original flow with retry
            fact, usage = await self._extract_single(
                system, novel_id, chapter_id, chapter_text,
            )

        # Dimension post-processing (FR-1.2 sanitize + FR-1.3 vote)
        if RELATION_DIMENSIONS_ENABLED and fact.relationships:
            _sanitize_relation_dimensions(fact, chapter_id)
            from src.infra.config import RELATION_SUBTYPE_VOTE_SAMPLES
            if RELATION_SUBTYPE_VOTE_SAMPLES >= 2:
                vote_usage = await self._vote_rel_subtypes(
                    fact, novel_id, chapter_id,
                )
                usage.prompt_tokens += vote_usage.prompt_tokens
                usage.completion_tokens += vote_usage.completion_tokens
                usage.total_tokens += vote_usage.total_tokens

        # Evidence grounding post-processing (FR-3.1): 无证据记录降置信度并标记
        if EVIDENCE_GROUNDING_ENABLED:
            _sanitize_evidence_grounding(fact, chapter_id, chapter_text, meta)

        # Recall pass (FR-4.1): 第二遍"查漏"调用,只补漏;补漏记录经同样的
        # sanitize 后标记 source="recall_pass" 并入。失败不影响首遍结果。
        from src.infra.config import RECALL_PASS_ENABLED
        if RECALL_PASS_ENABLED:
            recall_usage = await self._recall_pass(
                novel_id, chapter_id, chapter_text, fact,
            )
            usage.prompt_tokens += recall_usage.prompt_tokens
            usage.completion_tokens += recall_usage.completion_tokens
            usage.total_tokens += recall_usage.total_tokens

        return fact, usage, meta

    async def _recall_pass(
        self,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        fact: ChapterFact,
    ) -> LlmUsage:
        """第二遍"查漏"调用 (FR-4.1)。

        输入首遍结果清单 + 原文,要求只补漏;补漏记录先经与首遍相同的
        sanitize(维度值校验 / 证据锚定),再由 _merge_recall_additions
        只增不改地并入首遍结果。单章恰好 1 次额外调用 (NFR-2 ≤2 倍);
        任何失败都只记日志,首遍结果原样返回。
        """
        total_usage = LlmUsage()
        try:
            budget = get_budget()
            from src.infra import config as _cfg
            system = _RECALL_SYSTEM_PROMPT
            recall_schema = _build_recall_schema()
            if self._is_cloud:
                schema_text = json.dumps(recall_schema, ensure_ascii=False, indent=2)
                system += (
                    f"\n\n## 输出 JSON Schema\n"
                    f"你必须严格按照以下 JSON Schema 输出,不要输出多余字段或文本:\n"
                    f"```json\n{schema_text}\n```"
                )
            user_prompt = _build_recall_user_prompt(chapter_id, chapter_text, fact)
            max_out = _cfg.LLM_MAX_TOKENS if self._is_cloud else 8192
            result, call_usage = await self.llm.generate(
                system=system,
                prompt=user_prompt,
                format=recall_schema,
                temperature=0.1,
                max_tokens=max_out,
                timeout=600,
                num_ctx=budget.extraction_num_ctx,
            )
            total_usage.prompt_tokens += call_usage.prompt_tokens
            total_usage.completion_tokens += call_usage.completion_tokens
            total_usage.total_tokens += call_usage.total_tokens

            if not isinstance(result, dict):
                logger.warning(
                    "Chapter %d: recall pass 返回非 dict,本次补漏丢弃", chapter_id,
                )
                return total_usage
            _normalize_field_names(result)
            result["novel_id"] = novel_id
            result["chapter_id"] = chapter_id
            recall_fact = ChapterFact.model_validate(result)

            # 与首遍相同的 sanitize(开关口径一致,NFR-3)
            from src.infra.config import (
                EVIDENCE_GROUNDING_ENABLED,
                RELATION_DIMENSIONS_ENABLED,
            )
            if RELATION_DIMENSIONS_ENABLED and recall_fact.relationships:
                _sanitize_relation_dimensions(recall_fact, chapter_id)
            if EVIDENCE_GROUNDING_ENABLED:
                _sanitize_evidence_grounding(recall_fact, chapter_id, chapter_text)

            _merge_recall_additions(fact, recall_fact, chapter_id)
        except Exception as err:
            logger.warning(
                "Chapter %d: recall pass 失败(不影响首遍结果): %s",
                chapter_id, err,
            )
        return total_usage

    async def extract_source_pass(
        self,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        context_summary: str = "",
        genre_hint: str | None = None,
    ) -> tuple[ChapterFact, LlmUsage, ExtractionMeta]:
        """独立二审抽取 (multi-pass Epic 2):独立 system prompt + 同构 schema。

        与 extract() 的差异:不做 recall 补漏、不做 rel_subtype 投票
        (二者是一审的增量层,二审自身就是独立的一遍,单章恰好 1 次 LLM
        调用);维度清洗与证据锚定清洗保持与一审同口径,保证 diff 可比。
        所有产出记录打 source="source_pass" 溯源标记。
        """
        genre_context = _GENRE_CONTEXT.get(genre_hint, "") if genre_hint else ""
        system = self.source_pass_template.replace("{genre_context}", genre_context)
        system = system.replace("{context}", context_summary or "（无前序上下文）")

        budget = get_budget()
        original_len = len(chapter_text)
        meta = ExtractionMeta(original_len=original_len)

        # Truncate very long chapters to avoid token overflow
        if len(chapter_text) > budget.max_chapter_len:
            chapter_text = chapter_text[:budget.max_chapter_len]
            meta.is_truncated = True
            meta.truncated_len = len(chapter_text)

        if budget.segment_enabled:
            segments = _split_chapter_text(chapter_text)
        else:
            segments = [chapter_text]
        meta.segment_count = len(segments)

        schema = _build_source_pass_schema()
        if len(segments) > 1:
            logger.info(
                "Chapter %d (source pass): splitting %d chars into %d segments",
                chapter_id, len(chapter_text), len(segments),
            )
            fact, usage = await self._extract_segmented(
                system, novel_id, chapter_id, segments,
                prompt_builder=_build_source_pass_user_prompt,
                schema=schema,
            )
        else:
            fact, usage = await self._extract_single(
                system, novel_id, chapter_id, chapter_text,
                prompt_builder=_build_source_pass_user_prompt,
                schema=schema,
            )

        # 与一审同口径的规则清洗(开关口径一致,NFR-3);投票与补漏跳过
        from src.infra.config import (
            EVIDENCE_GROUNDING_ENABLED,
            RELATION_DIMENSIONS_ENABLED,
        )
        if RELATION_DIMENSIONS_ENABLED and fact.relationships:
            _sanitize_relation_dimensions(fact, chapter_id)
        if EVIDENCE_GROUNDING_ENABLED:
            _sanitize_evidence_grounding(fact, chapter_id, chapter_text, meta)

        # provenance: 二审产出统一打 source="source_pass"
        for ch in fact.characters:
            ch.source = "source_pass"
        for rel in fact.relationships:
            rel.source = "source_pass"
        for ev in fact.events:
            ev.source = "source_pass"

        return fact, usage, meta

    async def _extract_single(
        self,
        system: str,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        prompt_builder=None,
        schema: dict | None = None,
    ) -> tuple[ChapterFact, LlmUsage]:
        """Extract from a single (non-split) chapter text with retry.

        prompt_builder: 可选的 user prompt 构造函数(默认一审口径
        _build_user_prompt;独立二审传 _build_source_pass_user_prompt)。
        """
        build_prompt = prompt_builder or self._build_user_prompt
        example_text = self._build_example_text()
        user_prompt = build_prompt(chapter_id, chapter_text, example_text)

        # First attempt
        try:
            return await self._call_and_parse(
                system, user_prompt, novel_id, chapter_id, schema=schema,
            )
        except (LLMError, ExtractionError, Exception) as first_err:
            logger.warning(
                "First extraction attempt failed for chapter %d: %s",
                chapter_id, first_err,
            )

        # Retry: truncate text more aggressively
        retry_len = get_budget().retry_len
        truncated = chapter_text[:retry_len] if len(chapter_text) > retry_len else chapter_text
        retry_prompt = build_prompt(chapter_id, truncated, example_text)
        retry_prompt += "【重要】请输出严格的 JSON，不要输出多余文本。"
        try:
            return await self._call_and_parse(
                system, retry_prompt, novel_id, chapter_id, schema=schema,
            )
        except Exception as second_err:
            raise ExtractionError(
                f"Extraction failed for chapter {chapter_id} after 2 attempts: {second_err}"
            ) from second_err

    async def _extract_segmented(
        self,
        system: str,
        novel_id: str,
        chapter_id: int,
        segments: list[str],
        prompt_builder=None,
        schema: dict | None = None,
    ) -> tuple[ChapterFact, LlmUsage]:
        """Extract from multiple segments and merge results."""
        build_prompt = prompt_builder or self._build_user_prompt
        example_text = self._build_example_text()
        segment_facts: list[ChapterFact] = []
        total_usage = LlmUsage()

        for idx, seg_text in enumerate(segments):
            seg_label = f"（第 {idx + 1}/{len(segments)} 部分）"
            logger.info(
                "Chapter %d segment %d/%d: %d chars",
                chapter_id, idx + 1, len(segments), len(seg_text),
            )
            user_prompt = build_prompt(
                chapter_id, seg_text, example_text, segment_hint=seg_label,
            )

            # Each segment gets its own retry
            try:
                fact, seg_usage = await self._call_and_parse(
                    system, user_prompt, novel_id, chapter_id, schema=schema,
                )
                segment_facts.append(fact)
                total_usage.prompt_tokens += seg_usage.prompt_tokens
                total_usage.completion_tokens += seg_usage.completion_tokens
                total_usage.total_tokens += seg_usage.total_tokens
            except Exception as err:
                logger.warning(
                    "Chapter %d segment %d/%d failed: %s — retrying",
                    chapter_id, idx + 1, len(segments), err,
                )
                # Retry once
                try:
                    retry_prompt = build_prompt(
                        chapter_id, seg_text, example_text, segment_hint=seg_label,
                    )
                    retry_prompt += "【重要】请输出严格的 JSON，不要输出多余文本。"
                    fact, seg_usage = await self._call_and_parse(
                        system, retry_prompt, novel_id, chapter_id, schema=schema,
                    )
                    segment_facts.append(fact)
                    total_usage.prompt_tokens += seg_usage.prompt_tokens
                    total_usage.completion_tokens += seg_usage.completion_tokens
                    total_usage.total_tokens += seg_usage.total_tokens
                except Exception as retry_err:
                    logger.error(
                        "Chapter %d segment %d/%d failed after retry: %s",
                        chapter_id, idx + 1, len(segments), retry_err,
                    )
                    # Continue with other segments — partial data is better than none

        if not segment_facts:
            raise ExtractionError(
                f"All {len(segments)} segments failed for chapter {chapter_id}"
            )

        merged = _merge_chapter_facts(segment_facts, novel_id, chapter_id)
        logger.info(
            "Chapter %d: merged %d segments → %d chars, %d locs, %d events",
            chapter_id, len(segment_facts),
            len(merged.characters), len(merged.locations), len(merged.events),
        )
        return merged, total_usage

    async def _vote_rel_subtypes(
        self,
        fact: ChapterFact,
        novel_id: str,
        chapter_id: int,
    ) -> LlmUsage:
        """Self-consistency vote for rel_subtype (FR-1.3).

        The main extraction pass counts as the first vote; this method makes
        RELATION_SUBTYPE_VOTE_SAMPLES - 1 additional *lightweight* calls that
        classify only the rel_subtype dimension for the already-extracted
        relationships (relation_type + evidence as input, no full chapter
        re-extraction — NFR-2). Majority wins; ties go to the conservative
        class (see _SUBTYPE_TIE_BREAK_ORDER). The vote distribution is stored
        on each RelationshipFact.subtype_vote as confidence metadata.

        Failures in individual vote calls are non-fatal: the relationship
        keeps its main-pass rel_subtype.
        """
        from src.infra import config as _cfg

        total_usage = LlmUsage()
        relations = fact.relationships
        relations_payload = [
            {
                "index": i,
                "person_a": rel.person_a,
                "person_b": rel.person_b,
                "relation_type": rel.relation_type,
                "evidence": rel.evidence,
            }
            for i, rel in enumerate(relations)
        ]
        system = (
            "你是小说人物关系类型判定专家。根据给定的人物、关系类型与原文依据，"
            "为每条关系判定 rel_subtype（细化关系类型，13 选 1）。只输出 JSON，不要输出多余文本。\n\n"
            + self._dimension_guide
        )
        user_prompt = (
            "请为以下人物关系逐条判定 rel_subtype：\n"
            f"```json\n{json.dumps(relations_payload, ensure_ascii=False, indent=2)}\n```\n"
            '输出格式：{"votes": [{"index": 0, "rel_subtype": "结拜"}, ...]}，'
            "必须覆盖所有 index，rel_subtype 必须是取值表中的值。"
        )

        budget = get_budget()
        n_samples = _cfg.RELATION_SUBTYPE_VOTE_SAMPLES
        extra_votes: list[dict[int, str]] = []
        for sample_idx in range(n_samples - 1):
            try:
                result, usage = await self.llm.generate(
                    system=system,
                    prompt=user_prompt,
                    format=_VOTE_RESPONSE_SCHEMA,
                    temperature=0.7,  # higher temp for vote diversity (self-consistency)
                    max_tokens=4096,
                    timeout=300,
                    num_ctx=budget.extraction_num_ctx,
                )
                total_usage.prompt_tokens += usage.prompt_tokens
                total_usage.completion_tokens += usage.completion_tokens
                total_usage.total_tokens += usage.total_tokens
                extra_votes.append(
                    _parse_vote_response(result, len(relations), chapter_id)
                )
            except Exception as err:
                logger.warning(
                    "Chapter %d: subtype vote sample %d/%d failed: %s",
                    chapter_id, sample_idx + 2, n_samples, err,
                )

        # Aggregate votes per relationship (main extraction = first vote)
        for i, rel in enumerate(relations):
            counts: dict[str, int] = {}
            if rel.rel_subtype:
                counts[rel.rel_subtype] = 1
            for votes in extra_votes:
                v = votes.get(i)
                if v:
                    counts[v] = counts.get(v, 0) + 1
            if not counts:
                continue
            rel.subtype_vote = counts
            winner = _pick_majority_subtype(counts)
            if winner != rel.rel_subtype:
                logger.info(
                    "Chapter %d: rel_subtype vote override %s-%s: %s -> %s (votes=%s)",
                    chapter_id, rel.person_a, rel.person_b,
                    rel.rel_subtype, winner, counts,
                )
                rel.rel_subtype = winner

        logger.info(
            "Chapter %d: subtype vote done, %d relations, %d/%d extra samples ok, %d extra tokens",
            chapter_id, len(relations), len(extra_votes), n_samples - 1,
            total_usage.total_tokens,
        )
        return total_usage

    @staticmethod
    def _is_transient_error(err: Exception) -> bool:
        """Check if error is transient (network/rate-limit/server) vs permanent (parse/validation)."""
        msg = str(err).lower()
        return any(kw in msg for kw in (
            "429", "rate_limit", "rate limit",
            "500", "502", "503", "server_error",
            "timeout", "timed out",
            "nodename", "name resolution", "connection",
            "network", "reset by peer", "broken pipe",
        ))

    async def _call_and_parse(
        self,
        system: str,
        prompt: str,
        novel_id: str,
        chapter_id: int,
        timeout: int = 600,
        schema: dict | None = None,
    ) -> tuple[ChapterFact, LlmUsage]:
        """Call LLM and parse response into ChapterFact.

        Transient errors (429/500/network) get exponential backoff retries.
        Only permanent errors (parse/validation) count as real failures.

        schema: 输出 JSON schema,默认主抽取 schema(独立二审传
        _build_source_pass_schema() 的同构 schema)。
        """
        active_schema = schema if schema is not None else self._schema
        effective_system = system
        if self._is_cloud:
            schema_text = json.dumps(active_schema, ensure_ascii=False, indent=2)
            effective_system += (
                f"\n\n## 输出 JSON Schema\n"
                f"你必须严格按照以下 JSON Schema 输出，不要输出多余字段或文本：\n"
                f"```json\n{schema_text}\n```"
            )

        budget = get_budget()
        from src.infra import config as _cfg
        max_out = _cfg.LLM_MAX_TOKENS if self._is_cloud else 8192

        # Exponential backoff for transient errors (429/500/network)
        max_transient_retries = 5
        backoff_base = 30  # seconds

        for attempt in range(max_transient_retries + 1):
            try:
                result, usage = await self.llm.generate(
                    system=effective_system,
                    prompt=prompt,
                    format=active_schema,
                    temperature=0.1,
                    max_tokens=max_out,
                    timeout=timeout,
                    num_ctx=budget.extraction_num_ctx,
                )
                break  # success
            except Exception as err:
                if self._is_transient_error(err) and attempt < max_transient_retries:
                    wait = backoff_base * (2 ** attempt)  # 30, 60, 120, 240, 480s
                    logger.warning(
                        "Chapter %d transient error (attempt %d/%d), retrying in %ds: %s",
                        chapter_id, attempt + 1, max_transient_retries, wait, str(err)[:100],
                    )
                    await asyncio.sleep(wait)
                    continue
                raise  # permanent error or max retries exhausted

        if isinstance(result, str):
            raise ExtractionError(f"Expected dict from structured output, got str")

        # Handle LLM returning array [...] instead of object {...}
        if isinstance(result, list):
            dict_items = [item for item in result if isinstance(item, dict)]
            if dict_items:
                logger.warning(
                    "LLM returned array instead of object for chapter %d, using first dict element",
                    chapter_id,
                )
                result = dict_items[0]
            else:
                raise ExtractionError(
                    f"Expected dict from structured output, got list with no dict elements"
                )

        # Normalize LLM field name variants before Pydantic validation
        _normalize_field_names(result)

        # Override novel_id and chapter_id to ensure correctness
        result["novel_id"] = novel_id
        result["chapter_id"] = chapter_id

        return ChapterFact.model_validate(result), usage


def _normalize_field_names(data: dict) -> None:
    """Fix common LLM field name variants in-place before Pydantic validation.

    LLMs sometimes use shorter field names (e.g. 'name' instead of 'item_name')
    or return non-dict items in array fields. This normalizes them.
    """
    # Array fields that must contain dicts
    array_fields = (
        "characters", "relationships", "locations", "item_events",
        "org_events", "events", "spatial_relationships", "new_concepts",
    )
    for field in array_fields:
        if field in data and isinstance(data[field], list):
            # Filter out non-dict items (strings, nulls, etc.)
            data[field] = [item for item in data[field] if isinstance(item, dict)]

    # item_events: LLM may use 'name'/'type' instead of 'item_name'/'item_type'
    for item in data.get("item_events", []):
        if "item_name" not in item and "name" in item:
            item["item_name"] = item.pop("name")
        if "item_type" not in item and "type" in item:
            item["item_type"] = item.pop("type")

    # org_events: LLM may use 'name'/'type' instead of 'org_name'/'org_type'
    for item in data.get("org_events", []):
        if "org_name" not in item and "name" in item:
            item["org_name"] = item.pop("name")
        if "org_type" not in item and "type" in item:
            item["org_type"] = item.pop("type")
