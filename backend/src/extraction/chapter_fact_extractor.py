"""ChapterFact extractor: sends chapter text to LLM and parses structured output."""

import copy
import json
import logging
from pathlib import Path

from src.infra.llm_client import LLMClient, LLMError, get_llm_client
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Max chapter text length sent to LLM (chars) to avoid token overflow
_MAX_CHAPTER_LEN = 8000


class ExtractionError(Exception):
    """Raised when chapter fact extraction fails after retries."""


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

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or get_llm_client()
        self.system_template = _load_system_prompt()
        self.examples = _load_examples()
        self._schema = _build_extraction_schema()

    async def extract(
        self,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        context_summary: str = "",
    ) -> ChapterFact:
        """Extract ChapterFact from chapter text. Retries once on failure."""
        system = self.system_template.replace("{context}", context_summary or "（无前序上下文）")

        # Truncate very long chapters to avoid token overflow
        if len(chapter_text) > _MAX_CHAPTER_LEN:
            chapter_text = chapter_text[:_MAX_CHAPTER_LEN]

        # Build user prompt with example and explicit instructions
        example_text = ""
        if self.examples:
            example_text = (
                "## 参考示例\n"
                f"```json\n{json.dumps(self.examples[0], ensure_ascii=False, indent=2)}\n```\n\n"
            )

        user_prompt = (
            f"{example_text}"
            f"## 第 {chapter_id} 章\n\n{chapter_text}\n\n"
            "【关键要求】\n"
            "1. characters 数组必须包含所有出现的有名字的人物\n"
            "2. relationships 数组必须包含人物之间的关系，evidence 引用原文\n"
            "3. locations 数组必须包含所有地名\n"
            "4. events 数组中每个事件的 participants 必须列出参与者姓名，location 必须填写地点\n"
            "5. spatial_relationships 提取地点间的方位、距离、包含、相邻、分隔、地形、夹在中间(in_between)关系\n"
            "6. world_declarations 仅在文中有世界宏观结构描述时提取（区域划分、空间层、传送通道），没有则输出空列表\n"
            "7. 只提取原文明确出现的内容，禁止编造\n"
        )

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

        # Retry: truncate text more aggressively to reduce LLM workload
        truncated_text = chapter_text[:6000] if len(chapter_text) > 6000 else chapter_text
        retry_prompt = (
            f"{example_text}"
            f"## 第 {chapter_id} 章\n\n{truncated_text}\n\n"
            "【关键要求】\n"
            "1. characters 数组必须包含所有出现的有名字的人物\n"
            "2. relationships 数组必须包含人物之间的关系，evidence 引用原文\n"
            "3. locations 数组必须包含所有地名\n"
            "4. events 数组中每个事件的 participants 必须列出参与者姓名，location 必须填写地点\n"
            "5. spatial_relationships 提取地点间的方位、距离、包含、相邻、分隔、地形、夹在中间(in_between)关系\n"
            "6. world_declarations 仅在文中有世界宏观结构描述时提取，没有则输出空列表\n"
            "7. 只提取原文明确出现的内容，禁止编造\n"
            "【重要】请输出严格的 JSON，不要输出多余文本。"
        )
        try:
            return await self._call_and_parse(
                system, retry_prompt, novel_id, chapter_id,
            )
        except Exception as second_err:
            raise ExtractionError(
                f"Extraction failed for chapter {chapter_id} after 2 attempts: {second_err}"
            ) from second_err

    async def _call_and_parse(
        self,
        system: str,
        prompt: str,
        novel_id: str,
        chapter_id: int,
        timeout: int = 600,
    ) -> ChapterFact:
        """Call LLM and parse response into ChapterFact."""
        result = await self.llm.generate(
            system=system,
            prompt=prompt,
            format=self._schema,
            temperature=0.1,
            max_tokens=8192,
            timeout=timeout,
            num_ctx=16384,
        )

        if isinstance(result, str):
            raise ExtractionError(f"Expected dict from structured output, got str")

        # Override novel_id and chapter_id to ensure correctness
        result["novel_id"] = novel_id
        result["chapter_id"] = chapter_id

        return ChapterFact.model_validate(result)
