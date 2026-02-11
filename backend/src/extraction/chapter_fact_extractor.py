"""ChapterFact extractor: sends chapter text to LLM and parses structured output."""

import json
import logging
from pathlib import Path

from src.infra.llm_client import LLMClient, LLMError, get_llm_client
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class ExtractionError(Exception):
    """Raised when chapter fact extraction fails after retries."""


def _load_system_prompt() -> str:
    path = _PROMPTS_DIR / "extraction_system.txt"
    return path.read_text(encoding="utf-8")


def _load_examples() -> list[dict]:
    path = _PROMPTS_DIR / "extraction_examples.json"
    return json.loads(path.read_text(encoding="utf-8"))


class ChapterFactExtractor:
    """Extract structured ChapterFact from a single chapter using LLM."""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or get_llm_client()
        self.system_template = _load_system_prompt()
        self.examples = _load_examples()

    async def extract(
        self,
        novel_id: str,
        chapter_id: int,
        chapter_text: str,
        context_summary: str = "",
    ) -> ChapterFact:
        """Extract ChapterFact from chapter text. Retries once on failure."""
        system = self.system_template.replace("{context}", context_summary or "（无前序上下文）")
        user_prompt = f"## 第 {chapter_id} 章\n\n{chapter_text}"

        schema = ChapterFact.model_json_schema()

        # First attempt
        try:
            return await self._call_and_parse(
                system, user_prompt, schema, novel_id, chapter_id
            )
        except (LLMError, ExtractionError, Exception) as first_err:
            logger.warning(
                "First extraction attempt failed for chapter %d: %s",
                chapter_id, first_err,
            )

        # Retry with corrective hint
        retry_prompt = (
            f"{user_prompt}\n\n"
            "【重要提示】请确保输出严格的 JSON 格式，"
            "完全匹配给定的 JSON Schema，不要包含任何额外文字。"
        )
        try:
            return await self._call_and_parse(
                system, retry_prompt, schema, novel_id, chapter_id
            )
        except Exception as second_err:
            raise ExtractionError(
                f"Extraction failed for chapter {chapter_id} after 2 attempts: {second_err}"
            ) from second_err

    async def _call_and_parse(
        self,
        system: str,
        prompt: str,
        schema: dict,
        novel_id: str,
        chapter_id: int,
    ) -> ChapterFact:
        """Call LLM and parse response into ChapterFact."""
        result = await self.llm.generate(
            system=system,
            prompt=prompt,
            format=schema,
            temperature=0.1,
            max_tokens=4096,
            timeout=120,
        )

        if isinstance(result, str):
            raise ExtractionError(f"Expected dict from structured output, got str")

        # Override novel_id and chapter_id to ensure correctness
        result["novel_id"] = novel_id
        result["chapter_id"] = chapter_id

        return ChapterFact.model_validate(result)
