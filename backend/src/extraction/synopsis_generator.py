"""Generate a novel synopsis using LLM based on extracted facts."""

from __future__ import annotations

import json
import logging

from src.infra.llm_client import get_llm_client
from src.infra.context_budget import get_budget

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一位专业的文学编辑。请根据提供的小说分析数据，撰写一段简洁的小说简介。

要求：
- 100-200字
- 不剧透结局
- 侧重世界观和核心冲突
- 介绍主要人物和核心关系
- 语言简洁优美，适合作为小说推荐语
- 只输出简介文本，不要任何标题或前缀"""


class SynopsisGenerator:
    """Generate a brief synopsis for a novel based on analysis data."""

    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()

    async def generate(
        self,
        title: str,
        author: str | None,
        high_importance_events: list[str],
        main_characters: list[str],
        main_locations: list[str],
    ) -> str | None:
        """Generate a synopsis from high-importance events and key entities.

        Returns the synopsis text, or None if generation fails.
        """
        if not high_importance_events and not main_characters:
            logger.info("No analysis data available for synopsis generation")
            return None

        # Build user prompt
        parts = [f"小说：《{title}》"]
        if author:
            parts.append(f"作者：{author}")

        if main_characters:
            parts.append(f"\n主要人物：{', '.join(main_characters[:20])}")
        if main_locations:
            parts.append(f"主要地点：{', '.join(main_locations[:15])}")
        if high_importance_events:
            parts.append("\n重要事件摘要：")
            for evt in high_importance_events[:30]:
                parts.append(f"- {evt}")

        user_prompt = "\n".join(parts)
        budget = get_budget()

        try:
            content, _usage = await self.llm.generate(
                system=_SYSTEM_PROMPT,
                prompt=user_prompt,
                temperature=0.7,
                max_tokens=budget.max_tokens_scene,
            )
            text = content.strip() if isinstance(content, str) else str(content).strip()
            # Clean up potential quotes or prefixes
            if text.startswith(("\"", "\"")):
                text = text.strip("\"\"")
            if text.startswith("简介：") or text.startswith("简介:"):
                text = text[3:].strip()
            logger.info("Synopsis generated: %d chars", len(text))
            return text
        except Exception as e:
            logger.warning("Synopsis generation failed: %s", e)
            return None
