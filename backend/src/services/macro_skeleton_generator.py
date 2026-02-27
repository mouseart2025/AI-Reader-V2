"""MacroSkeletonGenerator: LLM-based top-down geographic skeleton for hierarchy anchoring.

Generates a 2-3 level core geographic skeleton from the novel's global location
inventory, injected as high-weight baseline votes to anchor the bottom-up
per-chapter extraction system with top-down structural knowledge.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from src.infra.context_budget import get_budget
from src.infra.llm_client import get_llm_client

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "extraction" / "prompts"

# Confidence → vote weight (high enough to anchor, not enough to override 50+ chapter votes)
_CONFIDENCE_WEIGHT = {"high": 5, "medium": 3}

# Only consider city-level and above for skeleton input
_SKELETON_TIERS = {"world", "continent", "kingdom", "region", "city"}

_SKELETON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "uber_root": {"type": "string"},
        "skeleton": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "child": {"type": "string"},
                    "parent": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium"],
                    },
                },
                "required": ["child", "parent", "confidence"],
            },
        },
        "synonyms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "alias": {"type": "string"},
                },
                "required": ["canonical", "alias"],
            },
        },
    },
    "required": ["uber_root", "skeleton"],
}


def _load_prompt_template() -> str:
    path = _PROMPTS_DIR / "macro_skeleton.txt"
    return path.read_text(encoding="utf-8")


class MacroSkeletonGenerator:
    """Generate a macro geographic skeleton via LLM for hierarchy anchoring."""

    def __init__(self, llm=None):
        self.llm = llm or get_llm_client()

    async def generate(
        self,
        novel_title: str,
        novel_genre_hint: str | None,
        location_tiers: dict[str, str],
        current_parents: dict[str, str],
    ) -> tuple[dict[str, Counter], list[tuple[str, str]]]:
        """Generate skeleton votes from LLM.

        Args:
            novel_title: Title of the novel.
            novel_genre_hint: Genre hint string or None.
            location_tiers: ``{location_name: tier}`` for all known locations.
            current_parents: ``{child: parent}`` current parent assignments.

        Returns:
            Tuple of (skeleton_votes, synonym_pairs) where:
            - skeleton_votes: ``{child: Counter({parent: weight})}`` — votes to inject.
            - synonym_pairs: ``[(canonical, alias), ...]`` — synonym location pairs.
        """
        all_locs = set(location_tiers.keys())
        if len(all_locs) < 3:
            logger.debug("Too few locations (%d), skipping skeleton", len(all_locs))
            return {}, []

        # --- Build prompt inputs ---

        # Find uber-root
        children_set = set(current_parents.keys())
        parent_counts: Counter = Counter()
        for p in current_parents.values():
            if p not in children_set:
                parent_counts[p] += 1
        uber_root = parent_counts.most_common(1)[0][0] if parent_counts else None

        # Root children (direct children of uber-root)
        root_children: list[str] = []
        if uber_root:
            root_children = sorted(
                c for c, p in current_parents.items() if p == uber_root
            )

        # Tier-grouped locations (only city-level and above)
        tiered: dict[str, list[str]] = {}
        for loc, tier in sorted(location_tiers.items()):
            if tier in _SKELETON_TIERS:
                tiered.setdefault(tier, []).append(loc)

        tiered_lines: list[str] = []
        tier_order = ["world", "continent", "kingdom", "region", "city"]
        for t in tier_order:
            locs = tiered.get(t, [])
            if locs:
                tiered_lines.append(f"【{t}】{', '.join(locs[:60])}")
                if len(locs) > 60:
                    tiered_lines.append(f"  ...及其余 {len(locs) - 60} 个")

        # Orphan list (no parent, not world/continent)
        orphans = [
            loc for loc in all_locs
            if loc not in children_set
            and location_tiers.get(loc, "city") not in ("world", "continent")
            and loc != uber_root
        ]
        # Limit orphan display to avoid token bloat
        orphan_display = ", ".join(orphans[:80])
        if len(orphans) > 80:
            orphan_display += f" ...等共 {len(orphans)} 个"

        if not tiered_lines and not orphans:
            logger.debug("No skeleton-relevant locations found")
            return {}, []

        # --- Build prompt ---
        template = _load_prompt_template()
        prompt = template.format(
            novel_title=novel_title or "未知",
            genre_hint=novel_genre_hint or "未知",
            uber_root=uber_root or "（未检测到）",
            root_children=", ".join(root_children[:30]) if root_children else "无",
            tiered_locations="\n".join(tiered_lines) if tiered_lines else "无",
            orphan_list=orphan_display if orphans else "无",
        )

        system = "你是一个小说地理分析专家。请严格按照 JSON 格式输出。"

        budget = get_budget()
        try:
            result, _usage = await self.llm.generate(
                system=system,
                prompt=prompt,
                format=_SKELETON_SCHEMA,
                temperature=0.1,
                max_tokens=4096,
                timeout=budget.hierarchy_timeout,
                num_ctx=min(budget.context_window, 8192),
            )
        except Exception:
            logger.warning("Macro skeleton LLM call failed", exc_info=True)
            return {}, []

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                logger.warning("Failed to parse macro skeleton result as JSON")
                return {}, []

        # --- Parse and validate ---
        votes: dict[str, Counter] = {}
        suggestions = result.get("skeleton", [])

        for sug in suggestions:
            child = sug.get("child", "")
            parent = sug.get("parent", "")
            confidence = sug.get("confidence", "medium")

            # Safety filters
            if not child or not parent:
                continue
            if child == parent:
                continue
            if child not in all_locs or parent not in all_locs:
                logger.debug(
                    "Skeleton: skipping hallucinated pair %s → %s", child, parent
                )
                continue

            weight = _CONFIDENCE_WEIGHT.get(confidence, 3)
            votes.setdefault(child, Counter())[parent] += weight

            logger.debug(
                "Skeleton: %s → %s (confidence=%s, weight=%d)",
                child, parent, confidence, weight,
            )

        # --- Parse synonyms ---
        synonym_pairs: list[tuple[str, str]] = []
        for syn in result.get("synonyms", []):
            canonical = syn.get("canonical", "")
            alias = syn.get("alias", "")
            if canonical and alias and canonical != alias:
                if canonical in all_locs and alias in all_locs:
                    synonym_pairs.append((canonical, alias))
                    logger.debug("Skeleton synonym: %s ← %s", canonical, alias)
                else:
                    logger.debug(
                        "Skeleton synonym skipped (not in known locs): %s ← %s",
                        canonical, alias,
                    )

        logger.info(
            "Macro skeleton: %d suggestions, %d valid votes, %d synonyms for novel %s",
            len(suggestions), len(votes), len(synonym_pairs), novel_title,
        )
        return votes, synonym_pairs
