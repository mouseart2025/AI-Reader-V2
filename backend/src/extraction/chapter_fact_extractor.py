"""ChapterFact extractor: sends chapter text to LLM and parses structured output."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.infra.config import LLM_MAX_TOKENS, LLM_PROVIDER
from src.infra.llm_client import LLMError, get_llm_client
from src.infra.openai_client import OpenAICompatibleClient
from src.models.chapter_fact import ChapterFact, CharacterFact

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Max chapter text length sent to LLM (chars).
# Ollama (local 8B, 16K ctx): 8000 chars ≈ 12K tokens input budget
# Cloud (256K ctx): 50000 chars ≈ 75K tokens — plenty of headroom
_MAX_CHAPTER_LEN_LOCAL = 8000
_MAX_CHAPTER_LEN_CLOUD = 50000
_MAX_CHAPTER_LEN = _MAX_CHAPTER_LEN_CLOUD if LLM_PROVIDER == "openai" else _MAX_CHAPTER_LEN_LOCAL

# Retry truncation: more aggressive cut on failure
_RETRY_LEN_LOCAL = 6000
_RETRY_LEN_CLOUD = 30000
_RETRY_LEN = _RETRY_LEN_CLOUD if LLM_PROVIDER == "openai" else _RETRY_LEN_LOCAL

# Segment splitting thresholds (chars). Cloud-mode only.
_SEGMENT_THRESHOLD_2 = 7000   # >7000 chars → split into 2 segments
_SEGMENT_THRESHOLD_3 = 12000  # >12000 chars → split into 3 segments


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


def _load_system_prompt() -> str:
    path = _PROMPTS_DIR / "extraction_system.txt"
    return path.read_text(encoding="utf-8")


def _load_examples() -> list[dict]:
    path = _PROMPTS_DIR / "extraction_examples.json"
    return json.loads(path.read_text(encoding="utf-8"))


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
        self.examples = _load_examples()
        self._schema = _build_extraction_schema()
        self._is_cloud = isinstance(self.llm, OpenAICompatibleClient)

    def _build_example_text(self) -> str:
        """Build the few-shot examples section for the user prompt."""
        if not self.examples:
            return ""
        examples_to_show = [self.examples[0]]
        if len(self.examples) >= 4:
            examples_to_show.append(self.examples[3])
        examples_json = json.dumps(examples_to_show, ensure_ascii=False, indent=2)
        return f"## 参考示例\n```json\n{examples_json}\n```\n\n"

    def _build_user_prompt(
        self, chapter_id: int, chapter_text: str, example_text: str,
        segment_hint: str = "",
    ) -> str:
        """Build the user prompt for a chapter or chapter segment."""
        return (
            f"{example_text}"
            f"## 第 {chapter_id} 章{segment_hint}\n\n{chapter_text}\n\n"
            "【关键要求】\n"
            "1. characters：宁多勿漏！包含所有有名字或固定称呼的人物。种族/物种名称作为称呼且有具体行为的角色也算（如赤尻马猴、通背猿猴）\n"
            "2. relationships：任何两个人物有互动或提及关系都必须提取，evidence 引用原文。命令/差遣/听令也是关系\n"
            "3. locations：宁多勿漏！所有具体地名都必须提取，即使只被简短提及也不可跳过\n"
            "4. events：每个事件的 participants 列出参与者姓名，location 填写地点，都不可为空\n"
            "5. spatial_relationships：提取地点间的方位(direction)、距离(distance)、包含(contains)、相邻(adjacent)、分隔(separated_by)、地形(terrain)、夹在中间(in_between)关系\n"
            "6. world_declarations：当文中有世界宏观结构描述时必须提取（区域划分region_division、区域方位region_position、空间层layer_exists如天界/地府/海底、传送通道portal），没有则输出空列表\n"
            "7. new_concepts：功法、丹药、修炼体系、世界观规则等首次出现或有详细介绍的概念，definition 必须详细（2-5句话）\n"
            "8. 只提取原文明确出现的内容，禁止编造\n"
        )

    async def extract(
        self,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        context_summary: str = "",
    ) -> ChapterFact:
        """Extract ChapterFact from chapter text.

        Long chapters (cloud mode) are automatically split into segments
        and merged to avoid output truncation.
        """
        system = self.system_template.replace("{context}", context_summary or "（无前序上下文）")

        # Truncate very long chapters to avoid token overflow
        if len(chapter_text) > _MAX_CHAPTER_LEN:
            chapter_text = chapter_text[:_MAX_CHAPTER_LEN]

        # Split long chapters into segments (cloud mode only)
        if self._is_cloud:
            segments = _split_chapter_text(chapter_text)
        else:
            segments = [chapter_text]

        if len(segments) > 1:
            logger.info(
                "Chapter %d: splitting %d chars into %d segments (%s)",
                chapter_id, len(chapter_text), len(segments),
                ", ".join(f"{len(s)}c" for s in segments),
            )
            return await self._extract_segmented(
                system, novel_id, chapter_id, segments,
            )

        # Single segment — original flow with retry
        return await self._extract_single(
            system, novel_id, chapter_id, chapter_text,
        )

    async def _extract_single(
        self,
        system: str,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
    ) -> ChapterFact:
        """Extract from a single (non-split) chapter text with retry."""
        example_text = self._build_example_text()
        user_prompt = self._build_user_prompt(chapter_id, chapter_text, example_text)

        # First attempt
        try:
            return await self._call_and_parse(
                system, user_prompt, novel_id, chapter_id,
            )
        except (LLMError, ExtractionError, Exception) as first_err:
            logger.warning(
                "First extraction attempt failed for chapter %d: %s",
                chapter_id, first_err,
            )

        # Retry: truncate text more aggressively
        truncated = chapter_text[:_RETRY_LEN] if len(chapter_text) > _RETRY_LEN else chapter_text
        retry_prompt = self._build_user_prompt(chapter_id, truncated, example_text)
        retry_prompt += "【重要】请输出严格的 JSON，不要输出多余文本。"
        try:
            return await self._call_and_parse(
                system, retry_prompt, novel_id, chapter_id,
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
    ) -> ChapterFact:
        """Extract from multiple segments and merge results."""
        example_text = self._build_example_text()
        segment_facts: list[ChapterFact] = []

        for idx, seg_text in enumerate(segments):
            seg_label = f"（第 {idx + 1}/{len(segments)} 部分）"
            logger.info(
                "Chapter %d segment %d/%d: %d chars",
                chapter_id, idx + 1, len(segments), len(seg_text),
            )
            user_prompt = self._build_user_prompt(
                chapter_id, seg_text, example_text, segment_hint=seg_label,
            )

            # Each segment gets its own retry
            try:
                fact = await self._call_and_parse(
                    system, user_prompt, novel_id, chapter_id,
                )
                segment_facts.append(fact)
            except Exception as err:
                logger.warning(
                    "Chapter %d segment %d/%d failed: %s — retrying",
                    chapter_id, idx + 1, len(segments), err,
                )
                # Retry once
                try:
                    retry_prompt = self._build_user_prompt(
                        chapter_id, seg_text, example_text, segment_hint=seg_label,
                    )
                    retry_prompt += "【重要】请输出严格的 JSON，不要输出多余文本。"
                    fact = await self._call_and_parse(
                        system, retry_prompt, novel_id, chapter_id,
                    )
                    segment_facts.append(fact)
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
        return merged

    async def _call_and_parse(
        self,
        system: str,
        prompt: str,
        novel_id: str,
        chapter_id: int,
        timeout: int = 600,
    ) -> ChapterFact:
        """Call LLM and parse response into ChapterFact."""
        effective_system = system
        if self._is_cloud:
            # Cloud APIs only support json_object mode, not schema-level enforcement.
            # Embed the JSON schema in the system prompt so the model knows the structure.
            schema_text = json.dumps(self._schema, ensure_ascii=False, indent=2)
            effective_system += (
                f"\n\n## 输出 JSON Schema\n"
                f"你必须严格按照以下 JSON Schema 输出，不要输出多余字段或文本：\n"
                f"```json\n{schema_text}\n```"
            )

        max_out = LLM_MAX_TOKENS if self._is_cloud else 8192
        result = await self.llm.generate(
            system=effective_system,
            prompt=prompt,
            format=self._schema,
            temperature=0.1,
            max_tokens=max_out,
            timeout=timeout,
            num_ctx=16384,
        )

        if isinstance(result, str):
            raise ExtractionError(f"Expected dict from structured output, got str")

        # Override novel_id and chapter_id to ensure correctness
        result["novel_id"] = novel_id
        result["chapter_id"] = chapter_id

        return ChapterFact.model_validate(result)
