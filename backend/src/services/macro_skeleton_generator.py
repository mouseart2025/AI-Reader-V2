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

# Confidence → vote weight. v0.67: increased from 5/3 to 15/8 so skeleton
# relationships survive against chapter-level baseline votes.
# The skeleton represents top-down structural knowledge that should anchor
# the bottom-up extraction votes, especially for intermediate layers.
_CONFIDENCE_WEIGHT = {"high": 15, "medium": 8}

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
        "directions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": [
                            "north_of", "south_of", "east_of", "west_of",
                            "northeast_of", "northwest_of", "southeast_of", "southwest_of",
                        ],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium"],
                    },
                },
                "required": ["source", "target", "direction", "confidence"],
            },
        },
    },
    "required": ["uber_root", "skeleton"],
}

# Valid direction values for validation
_VALID_DIRECTIONS = {
    "north_of", "south_of", "east_of", "west_of",
    "northeast_of", "northwest_of", "southeast_of", "southwest_of",
}


def _load_prompt_template() -> str:
    from src.extraction.prompt_registry import get_prompt
    return get_prompt("macro_skeleton")


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
        location_frequencies: Counter | None = None,
    ) -> tuple[dict[str, Counter], list[tuple[str, str]], list[dict]]:
        """Generate skeleton votes from LLM.

        Args:
            novel_title: Title of the novel.
            novel_genre_hint: Genre hint string or None.
            location_tiers: ``{location_name: tier}`` for all known locations.
            current_parents: ``{child: parent}`` current parent assignments.
            location_frequencies: Optional ``Counter({name: mention_count})``.
                When provided, skeleton input is filtered to locations with
                freq≥3 (regular+core), reducing LLM input size and improving
                output quality by focusing on structurally important locations.

        Returns:
            Tuple of (skeleton_votes, synonym_pairs, direction_constraints) where:
            - skeleton_votes: ``{child: Counter({parent: weight})}`` — votes to inject.
            - synonym_pairs: ``[(canonical, alias), ...]`` — synonym location pairs.
            - direction_constraints: ``[{source, target, relation_type, value, ...}]``
              — macro direction constraints for the solver.
        """
        all_locs_full = set(location_tiers.keys())  # full set for hallucination check
        if len(all_locs_full) < 3:
            logger.debug("Too few locations (%d), skipping skeleton", len(all_locs_full))
            return {}, [], []

        # ── v0.67.1: Frequency-based input filtering ──
        # When frequency data is available, only include locations with freq≥3
        # (core + regular) in the LLM prompt. This dramatically reduces input
        # size (e.g., 791→115 for 西游记) and lets the LLM focus on
        # structurally important locations.
        _SKELETON_MIN_FREQ = 3
        all_locs = set(all_locs_full)  # prompt input set (may be filtered)
        if location_frequencies:
            freq_filtered = {
                loc for loc in all_locs
                if location_frequencies.get(loc, 0) >= _SKELETON_MIN_FREQ
            }
            # Always keep uber_root and continent+ locations regardless of freq
            for loc, tier in location_tiers.items():
                if tier in ("world", "continent", "kingdom"):
                    freq_filtered.add(loc)
            _orig_count = len(all_locs)
            all_locs = freq_filtered
            logger.info(
                "Skeleton freq filter: %d → %d locations (freq≥%d + world/continent/kingdom)",
                _orig_count, len(all_locs), _SKELETON_MIN_FREQ,
            )

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
                c for c, p in current_parents.items()
                if p == uber_root and c in all_locs
            )

        # Tier-grouped locations (only city-level and above, filtered by freq)
        tiered: dict[str, list[str]] = {}
        for loc, tier in sorted(location_tiers.items()):
            if tier in _SKELETON_TIERS and loc in all_locs:
                tiered.setdefault(tier, []).append(loc)

        tiered_lines: list[str] = []
        tier_order = ["world", "continent", "kingdom", "region", "city"]
        for t in tier_order:
            locs = tiered.get(t, [])
            if locs:
                tiered_lines.append(f"【{t}】{', '.join(locs[:60])}")
                if len(locs) > 60:
                    tiered_lines.append(f"  ...及其余 {len(locs) - 60} 个")

        # Orphan list (no parent, not world/continent, filtered by freq)
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
            return {}, [], []

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
                max_tokens=16384,  # v0.67.1: 12K→16K to prevent finish_reason=length truncation
                timeout=300,  # v0.67: 5 min for deeper 4-5 level skeleton prompts
                num_ctx=budget.context_window,  # use budget default (cloud ignores this anyway)
            )
        except Exception:
            logger.warning("Macro skeleton LLM call failed", exc_info=True)
            return {}, [], []

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                logger.warning("Failed to parse macro skeleton result as JSON")
                return {}, [], []

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
            if child not in all_locs_full or parent not in all_locs_full:
                logger.debug(
                    "Skeleton: skipping hallucinated pair %s → %s", child, parent
                )
                continue

            weight = _CONFIDENCE_WEIGHT.get(confidence, 3)
            # Boost weight for kingdom-level locations currently orphaned under uber_root.
            # Without this boost, per-chapter votes (累积 20+) overwhelm skeleton votes (5),
            # keeping kingdoms like 车迟国 stuck under 天下 instead of 西牛贺洲.
            child_tier = location_tiers.get(child, "city")
            child_current_parent = current_parents.get(child)
            if child_tier == "kingdom" and (child_current_parent == uber_root or child_current_parent is None):
                weight = max(weight, 10)
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
                if canonical in all_locs_full and alias in all_locs_full:
                    synonym_pairs.append((canonical, alias))
                    logger.debug("Skeleton synonym: %s ← %s", canonical, alias)
                else:
                    logger.debug(
                        "Skeleton synonym skipped (not in known locs): %s ← %s",
                        canonical, alias,
                    )

        # --- Parse directions (macro anchor constraints) ---
        direction_constraints: list[dict] = []
        for d in result.get("directions", []):
            source = d.get("source", "")
            target = d.get("target", "")
            direction = d.get("direction", "")
            confidence = d.get("confidence", "medium")

            if not source or not target or source == target:
                continue
            if direction not in _VALID_DIRECTIONS:
                logger.debug("Skeleton direction: invalid direction %r", direction)
                continue
            if source not in all_locs_full or target not in all_locs_full:
                logger.debug(
                    "Skeleton direction: skipping hallucinated %s → %s", source, target
                )
                continue

            conf_score = {"high": 1.0, "medium": 0.8}.get(confidence, 0.6)
            direction_constraints.append({
                "source": source,
                "target": target,
                "relation_type": "direction",
                "value": direction,
                "confidence": confidence,
                "confidence_score": conf_score,
                "source_type": "llm_anchor",
            })
            logger.debug(
                "Skeleton direction: %s %s %s (confidence=%s)",
                source, direction, target, confidence,
            )

        logger.info(
            "Macro skeleton: %d suggestions, %d valid votes, %d synonyms, %d directions for novel %s",
            len(suggestions), len(votes), len(synonym_pairs), len(direction_constraints),
            novel_title,
        )
        return votes, synonym_pairs, direction_constraints
