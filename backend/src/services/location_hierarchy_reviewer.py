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

    _BATCH_SIZE = 70
    _MAX_BATCHES = 3

    async def review(
        self,
        location_tiers: dict[str, str],
        current_parents: dict[str, str],
        scene_analysis: dict,
        novel_genre_hint: str | None,
    ) -> dict[str, Counter]:
        """Review hierarchy and return vote suggestions.

        When orphan count > 80, automatically splits into batches of ~70,
        with each batch receiving the previous batch's results as context.
        Maximum 3 batches (covers ~210 orphans).

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

        # Single batch if within limit
        if len(orphans) <= 80:
            return await self._review_batch(
                location_tiers, current_parents, scene_analysis,
                novel_genre_hint, orphans, all_locs,
            )

        # Multi-batch processing
        logger.info(
            "Large orphan set (%d), splitting into batches of %d (max %d batches)",
            len(orphans), self._BATCH_SIZE, self._MAX_BATCHES,
        )
        all_votes: dict[str, Counter] = {}
        remaining = list(orphans)
        previous_suggestions: list[dict] = []

        for batch_idx in range(self._MAX_BATCHES):
            if not remaining:
                break
            batch = remaining[:self._BATCH_SIZE]
            remaining = remaining[self._BATCH_SIZE:]

            logger.info(
                "Batch %d/%d: reviewing %d orphans (%d remaining)",
                batch_idx + 1, self._MAX_BATCHES, len(batch), len(remaining),
            )
            batch_votes = await self._review_batch(
                location_tiers, current_parents, scene_analysis,
                novel_genre_hint, batch, all_locs,
                previous_suggestions=previous_suggestions,
            )

            # Merge votes
            for child, counter in batch_votes.items():
                if child not in all_votes:
                    all_votes[child] = Counter()
                all_votes[child] += counter

            # Collect suggestions for next batch context
            for child, counter in batch_votes.items():
                if counter:
                    best_parent = counter.most_common(1)[0][0]
                    previous_suggestions.append({
                        "child": child, "parent": best_parent,
                    })

        logger.info(
            "Multi-batch review complete: %d total votes across %d batches",
            sum(len(c) for c in all_votes.values()),
            min(len(orphans) // self._BATCH_SIZE + 1, self._MAX_BATCHES),
        )
        return all_votes

    async def _review_batch(
        self,
        location_tiers: dict[str, str],
        current_parents: dict[str, str],
        scene_analysis: dict,
        novel_genre_hint: str | None,
        orphans: list[str],
        all_locs: set[str],
        previous_suggestions: list[dict] | None = None,
    ) -> dict[str, Counter]:
        """Execute a single LLM review batch for a subset of orphans."""
        template = _load_review_prompt_template()

        # Hierarchy tree
        hierarchy_lines = []
        for loc, tier in sorted(location_tiers.items()):
            parent = current_parents.get(loc, "（无 parent）")
            hierarchy_lines.append(f"  {loc} [tier={tier}] → parent: {parent}")

        # Truncate to ~200 locations for token budget
        all_entries = hierarchy_lines
        if len(all_entries) > 200:
            priority_locs = set(orphans)
            for group in scene_analysis.get("sibling_groups", []):
                priority_locs.update(group)
            for hub, neighbors in scene_analysis.get("hub_nodes", {}).items():
                priority_locs.add(hub)
                priority_locs.update(neighbors)
            priority_lines = [
                line for line in hierarchy_lines
                if any(loc in line for loc in priority_locs)
            ]
            remaining_lines = [l for l in hierarchy_lines if l not in priority_lines]
            max_remaining = 200 - len(priority_lines)
            all_entries = priority_lines + remaining_lines[:max(0, max_remaining)]

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

        # Append previous batch results as additional context
        if previous_suggestions:
            prev_lines = [f"  {s['child']} → {s['parent']}" for s in previous_suggestions]
            prompt += (
                "\n\n## 前序批次已确认的归属关系（请勿重复建议，可作为参考）\n"
                + "\n".join(prev_lines)
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
            logger.warning("LLM hierarchy review batch failed", exc_info=True)
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

        for sug in suggestions:
            child = sug.get("child", "")
            parent = sug.get("parent", "")
            confidence = sug.get("confidence", "low")

            if not child or not parent or child == parent:
                continue
            if child not in all_locs or parent not in all_locs:
                continue

            weight = _CONFIDENCE_WEIGHT.get(confidence, 1)
            votes.setdefault(child, Counter())[parent] += weight

            reason = sug.get("reason", "")
            logger.debug(
                "Hierarchy review: %s → %s (confidence=%s, reason=%s)",
                child, parent, confidence, reason,
            )

        logger.info(
            "Hierarchy review batch: %d suggestions, %d valid votes",
            len(suggestions), sum(len(c) for c in votes.values()),
        )

        return votes
