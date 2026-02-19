"""LocationHierarchyReviewer: single LLM call to review and fix orphan root nodes.

Post-analysis step that globally reviews the location hierarchy tree and
suggests parent assignments for orphan root nodes (locations without parents
that are not top-level tiers like world/continent).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from src.infra.config import LLM_PROVIDER
from src.infra.llm_client import get_llm_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "extraction" / "prompts"

# Confidence → vote weight mapping
_CONFIDENCE_WEIGHT = {"high": 5, "medium": 3, "low": 1}

# LLM output schema for structured output
_REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "child": {"type": "string"},
                    "parent": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["child", "parent", "confidence"],
            },
        },
    },
    "required": ["suggestions"],
}


def _load_review_prompt_template() -> str:
    path = _PROMPTS_DIR / "hierarchy_review.txt"
    return path.read_text(encoding="utf-8")


class LocationHierarchyReviewer:
    """Single LLM call to globally review location hierarchy."""

    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()

    async def review(
        self,
        location_tiers: dict[str, str],
        current_parents: dict[str, str],
        scene_analysis: dict,
        novel_genre_hint: str | None,
    ) -> dict[str, Counter]:
        """Review hierarchy and return vote suggestions.

        Args:
            location_tiers: ``{location_name: tier}``
            current_parents: ``{child: parent}``
            scene_analysis: Output from ``SceneTransitionAnalyzer.analyze()``
            novel_genre_hint: Genre string or None

        Returns:
            ``{child: Counter({parent: weight})}`` — votes to inject
        """
        # Build orphan list
        children = set(current_parents.keys())
        all_locs = set(location_tiers.keys())
        orphans = [
            loc for loc in (all_locs - children)
            if location_tiers.get(loc, "city") not in ("world", "continent")
        ]

        if not orphans:
            logger.debug("No orphan root nodes, skipping LLM review")
            return {}

        # Build prompt
        template = _load_review_prompt_template()

        # Hierarchy tree
        hierarchy_lines = []
        for loc, tier in sorted(location_tiers.items()):
            parent = current_parents.get(loc, "（无 parent）")
            hierarchy_lines.append(f"  {loc} [tier={tier}] → parent: {parent}")

        # Truncate to ~200 locations for token budget
        all_entries = hierarchy_lines
        if len(all_entries) > 200:
            # Keep orphans + their potential parents (hubs, siblings)
            priority_locs = set(orphans)
            for group in scene_analysis.get("sibling_groups", []):
                priority_locs.update(group)
            for hub, neighbors in scene_analysis.get("hub_nodes", {}).items():
                priority_locs.add(hub)
                priority_locs.update(neighbors)
            # Filter hierarchy lines for priority locations, plus fill remaining
            priority_lines = [
                line for line in hierarchy_lines
                if any(loc in line for loc in priority_locs)
            ]
            remaining = [l for l in hierarchy_lines if l not in priority_lines]
            max_remaining = 200 - len(priority_lines)
            all_entries = priority_lines + remaining[:max(0, max_remaining)]

        # Format scene analysis
        sibling_text = "无" if not scene_analysis.get("sibling_groups") else "\n".join(
            f"  组: {', '.join(g)}" for g in scene_analysis["sibling_groups"]
        )
        hub_text = "无" if not scene_analysis.get("hub_nodes") else "\n".join(
            f"  {hub} → 连接: {', '.join(neighbors)}"
            for hub, neighbors in scene_analysis["hub_nodes"].items()
        )

        prompt = template.format(
            genre_hint=novel_genre_hint or "未知",
            hierarchy_tree="\n".join(all_entries),
            orphan_list=", ".join(orphans[:100]),
            sibling_groups=sibling_text,
            hub_nodes=hub_text,
        )

        system = "你是一个小说地理分析专家。请严格按照 JSON 格式输出。"

        is_cloud = LLM_PROVIDER == "openai"
        try:
            result, _usage = await self.llm.generate(
                system=system,
                prompt=prompt,
                format=_REVIEW_SCHEMA,
                temperature=0.1,
                max_tokens=4096,
                timeout=120 if is_cloud else 90,
                num_ctx=8192,
            )
        except Exception:
            logger.warning("LLM hierarchy review call failed", exc_info=True)
            return {}

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                logger.warning("Failed to parse LLM review result as JSON")
                return {}

        # Parse suggestions into votes
        votes: dict[str, Counter] = {}
        suggestions = result.get("suggestions", [])
        valid_locs = all_locs

        for sug in suggestions:
            child = sug.get("child", "")
            parent = sug.get("parent", "")
            confidence = sug.get("confidence", "low")

            # Validate: both must exist in location_tiers, and not self-referencing
            if not child or not parent or child == parent:
                continue
            if child not in valid_locs or parent not in valid_locs:
                continue

            weight = _CONFIDENCE_WEIGHT.get(confidence, 1)
            votes.setdefault(child, Counter())[parent] += weight

            reason = sug.get("reason", "")
            logger.debug(
                "Hierarchy review: %s → %s (confidence=%s, reason=%s)",
                child, parent, confidence, reason,
            )

        logger.info(
            "Hierarchy review: %d suggestions from LLM, %d valid votes",
            len(suggestions), sum(len(c) for c in votes.values()),
        )

        return votes
