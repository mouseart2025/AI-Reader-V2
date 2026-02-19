"""LLM-driven scene extraction: splits chapter text into scenes using AI."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.infra.config import LLM_MAX_TOKENS
from src.infra.context_budget import get_budget
from src.infra.llm_client import LlmUsage, get_llm_client
from src.infra.openai_client import OpenAICompatibleClient
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class SceneLLMExtractor:
    """Extract scenes from a chapter using LLM."""

    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()
        self.system_template = self._load_system_prompt()
        self._is_cloud = isinstance(self.llm, OpenAICompatibleClient)

    @staticmethod
    def _load_system_prompt() -> str:
        path = _PROMPTS_DIR / "scene_system.txt"
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _add_paragraph_markers(text: str) -> tuple[str, int]:
        """Add 【P0】【P1】... markers to each paragraph.

        Returns (marked_text, total_paragraphs).
        """
        lines = text.split("\n")
        paragraphs: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                paragraphs.append(stripped)

        if not paragraphs:
            return text, 0

        marked = []
        for i, p in enumerate(paragraphs):
            marked.append(f"【P{i}】{p}")

        return "\n".join(marked), len(paragraphs)

    async def extract(
        self,
        chapter_text: str,
        chapter_num: int,
        fact: ChapterFact,
    ) -> list[dict]:
        """Extract scenes from chapter text using LLM.

        Returns a list of scene dicts ready for DB storage.
        """
        # 1. Add paragraph markers
        budget = get_budget()
        if len(chapter_text) > budget.scene_max_chapter_len:
            chapter_text = chapter_text[:budget.scene_max_chapter_len]

        marked_text, total_paragraphs = self._add_paragraph_markers(chapter_text)
        if total_paragraphs == 0:
            return []

        # 2. Extract known entities from ChapterFact
        characters = [c.name for c in fact.characters]
        locations = [loc.name for loc in fact.locations]

        # 3. Build system prompt with entity injection
        system = self.system_template.replace(
            "{characters}", "、".join(characters) if characters else "（无）"
        ).replace(
            "{locations}", "、".join(locations) if locations else "（无）"
        )

        # 4. Build user prompt
        user_prompt = (
            f"## 第 {chapter_num} 章\n\n{marked_text}\n\n"
            "请输出场景 JSON 数组。"
        )

        # 5. Call LLM
        max_out = min(LLM_MAX_TOKENS, 4096) if self._is_cloud else 4096
        result, usage = await self.llm.generate(
            system=system,
            prompt=user_prompt,
            temperature=0.1,
            max_tokens=max_out,
            timeout=300,
            num_ctx=budget.extraction_num_ctx,
        )

        # 6. Parse response
        raw_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        scenes = self._parse_and_validate(raw_text, chapter_num, total_paragraphs)

        return scenes

    def _parse_and_validate(
        self,
        raw_text: str,
        chapter_num: int,
        total_paragraphs: int,
    ) -> list[dict]:
        """Parse LLM output and validate/fix scene data."""
        # Try to extract JSON array from response
        scenes_raw = self._extract_json_array(raw_text)
        if not scenes_raw:
            logger.warning("Failed to parse scene JSON for chapter %d", chapter_num)
            return []

        # Validate and fix each scene
        scenes: list[dict] = []
        for i, s in enumerate(scenes_raw):
            pr = s.get("paragraph_range", [])
            if not isinstance(pr, list) or len(pr) != 2:
                continue

            start, end = int(pr[0]), int(pr[1])
            # Clamp to valid range
            start = max(0, min(start, total_paragraphs - 1))
            end = max(start, min(end, total_paragraphs - 1))

            scene = {
                "index": i,
                "chapter": chapter_num,
                "paragraph_range": [start, end],
                "title": str(s.get("title", f"场景 {i + 1}"))[:30],
                "location": str(s.get("location", "")),
                "time_of_day": str(s.get("time_of_day", "")),
                "characters": s.get("characters", []) if isinstance(s.get("characters"), list) else [],
                "emotional_tone": str(s.get("emotional_tone", "平静")),
                "event_type": str(s.get("event_type", "描写")),
                "summary": str(s.get("summary", ""))[:100],
                "key_dialogue": s.get("key_dialogue", []) if isinstance(s.get("key_dialogue"), list) else [],
                "description": str(s.get("summary", ""))[:100],
                "dialogue_count": 0,
            }
            scenes.append(scene)

        if not scenes:
            return []

        # Sort by start paragraph
        scenes.sort(key=lambda sc: sc["paragraph_range"][0])

        # Fix coverage: fill gaps and remove overlaps
        scenes = self._fix_coverage(scenes, total_paragraphs)

        # Re-index
        for i, sc in enumerate(scenes):
            sc["index"] = i

        return scenes

    @staticmethod
    def _fix_coverage(scenes: list[dict], total_paragraphs: int) -> list[dict]:
        """Fix scene coverage: fill gaps and remove overlaps."""
        if not scenes:
            return scenes

        # Ensure first scene starts at 0
        scenes[0]["paragraph_range"][0] = 0

        # Fix overlaps and gaps between consecutive scenes
        for i in range(1, len(scenes)):
            prev_end = scenes[i - 1]["paragraph_range"][1]
            cur_start = scenes[i]["paragraph_range"][0]

            if cur_start <= prev_end:
                # Overlap: truncate current scene's start
                scenes[i]["paragraph_range"][0] = prev_end + 1
            elif cur_start > prev_end + 1:
                # Gap: extend previous scene's end
                scenes[i - 1]["paragraph_range"][1] = cur_start - 1

        # Ensure last scene ends at total_paragraphs - 1
        if scenes:
            scenes[-1]["paragraph_range"][1] = total_paragraphs - 1

        # Remove degenerate scenes (start > end after overlap fixing)
        scenes = [s for s in scenes if s["paragraph_range"][0] <= s["paragraph_range"][1]]

        return scenes

    @staticmethod
    def _extract_json_array(text: str) -> list[dict] | None:
        """Try to extract a JSON array from LLM output."""
        import re

        # Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = cleaned.strip()

        # Try direct parse
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "scenes" in result:
                return result["scenes"]
        except json.JSONDecodeError:
            pass

        # Try to find JSON array
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return result
            except json.JSONDecodeError:
                pass

        return None
