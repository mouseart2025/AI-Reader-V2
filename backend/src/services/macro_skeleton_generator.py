"""MacroSkeletonGenerator: LLM-based top-down geographic skeleton for hierarchy anchoring.

v0.67.1: Rewritten from single monolithic LLM call to phased classification.
Each phase is a small, focused LLM call (~20 locations, <3K output) that
classifies locations into their parent tier. This solves:
- Output truncation (21K chars → <3K per call)
- Timeout (300s monolithic → 60s per small call)
- Quality (focused context → fewer mistakes)
"""

from __future__ import annotations

import json
import logging
from collections import Counter

from src.infra.context_budget import get_budget
from src.infra.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# Confidence → vote weight.
_CONFIDENCE_WEIGHT = {"high": 15, "medium": 8}

# Only consider city-level and above for skeleton input
_SKELETON_TIERS = {"world", "continent", "kingdom", "region", "city"}

# JSON schema for classification response
_CLASSIFY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "assignments": {
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
    "required": ["assignments"],
}

# Valid direction values for validation
_VALID_DIRECTIONS = {
    "north_of", "south_of", "east_of", "west_of",
    "northeast_of", "northwest_of", "southeast_of", "southwest_of",
}

# Direction schema for the final directions-only call
_DIRECTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "directions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": list(_VALID_DIRECTIONS),
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
    "required": ["directions"],
}


class MacroSkeletonGenerator:
    """Generate a macro geographic skeleton via phased LLM classification."""

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
        """Generate skeleton votes via phased classification.

        Phase 1: Classify kingdoms → continents (which continent does each kingdom belong to?)
        Phase 2: Classify regions → kingdoms (which kingdom does each region belong to?)
        Phase 3: Direction anchoring for top-level regions.

        Each phase is a small LLM call (~20 locations, <3K output, 60s timeout).
        """
        all_locs_full = set(location_tiers.keys())
        if len(all_locs_full) < 3:
            return {}, [], []

        # ── Frequency-based input filtering ──
        _SKELETON_MIN_FREQ = 3
        all_locs = set(all_locs_full)
        if location_frequencies:
            freq_filtered = {
                loc for loc in all_locs
                if location_frequencies.get(loc, 0) >= _SKELETON_MIN_FREQ
            }
            for loc, tier in location_tiers.items():
                if tier in ("world", "continent", "kingdom"):
                    freq_filtered.add(loc)
            logger.info(
                "Skeleton freq filter: %d → %d locations",
                len(all_locs), len(freq_filtered),
            )
            all_locs = freq_filtered

        # ── Group by tier ──
        uber_root = self._find_uber_root(current_parents)
        continents = sorted(
            loc for loc in all_locs
            if location_tiers.get(loc) == "continent"
        )
        kingdoms = sorted(
            loc for loc in all_locs
            if location_tiers.get(loc) == "kingdom"
        )
        regions = sorted(
            loc for loc in all_locs
            if location_tiers.get(loc) == "region"
        )

        if not continents and not kingdoms:
            logger.debug("No continents or kingdoms for skeleton")
            return {}, [], []

        votes: dict[str, Counter] = {}
        all_synonyms: list[tuple[str, str]] = []
        title = novel_title or "未知"
        genre = novel_genre_hint or "未知"

        # ── Phase 1: kingdoms → continents ──
        if continents and kingdoms:
            p1_votes, p1_syns = await self._classify_batch(
                title, genre,
                children=kingdoms,
                parent_candidates=continents,
                child_label="国/王国",
                parent_label="大洲/界域",
                all_locs_full=all_locs_full,
                location_tiers=location_tiers,
                current_parents=current_parents,
                uber_root=uber_root,
            )
            votes.update(p1_votes)
            all_synonyms.extend(p1_syns)
            logger.info("Phase 1 (kingdom→continent): %d votes", len(p1_votes))

        # ── Phase 2: regions → kingdoms (or continents if no kingdoms) ──
        if regions:
            # Parent candidates: kingdoms + continents (regions can belong to either)
            p2_parents = kingdoms + continents
            if p2_parents:
                p2_votes, p2_syns = await self._classify_batch(
                    title, genre,
                    children=regions,
                    parent_candidates=p2_parents,
                    child_label="山/河/区域",
                    parent_label="国/大洲",
                    all_locs_full=all_locs_full,
                    location_tiers=location_tiers,
                    current_parents=current_parents,
                    uber_root=uber_root,
                )
                votes.update(p2_votes)
                all_synonyms.extend(p2_syns)
                logger.info("Phase 2 (region→kingdom): %d votes", len(p2_votes))

        # ── Phase 3: Direction anchoring ──
        direction_constraints: list[dict] = []
        top_regions = continents[:8]  # top-level regions for direction
        if len(top_regions) >= 2:
            direction_constraints = await self._get_directions(
                title, top_regions, all_locs_full,
            )
            logger.info("Phase 3 (directions): %d constraints", len(direction_constraints))

        logger.info(
            "Macro skeleton: %d votes, %d synonyms, %d directions for %s",
            len(votes), len(all_synonyms), len(direction_constraints), title,
        )
        return votes, all_synonyms, direction_constraints

    async def _classify_batch(
        self,
        novel_title: str,
        genre: str,
        children: list[str],
        parent_candidates: list[str],
        child_label: str,
        parent_label: str,
        all_locs_full: set[str],
        location_tiers: dict[str, str],
        current_parents: dict[str, str],
        uber_root: str | None,
        batch_size: int = 30,
    ) -> tuple[dict[str, Counter], list[tuple[str, str]]]:
        """Classify a batch of children into parent candidates via LLM.

        Splits into sub-batches of `batch_size` for reliability.
        Returns (votes, synonym_pairs).
        """
        votes: dict[str, Counter] = {}
        synonyms: list[tuple[str, str]] = []

        for i in range(0, len(children), batch_size):
            batch = children[i:i + batch_size]
            prompt = self._build_classify_prompt(
                novel_title, genre, batch, parent_candidates,
                child_label, parent_label,
            )

            # Retry once on timeout — MiniMax can be slow on first request
            for attempt in range(2):
                try:
                    result, _usage = await self.llm.generate(
                        system="你是一个小说地理分析专家。请严格按照 JSON 格式输出。",
                        prompt=prompt,
                        format=_CLASSIFY_SCHEMA,
                        temperature=0.1,
                        max_tokens=4096,
                        timeout=150,  # 150s per batch (MiniMax can be slow)
                        num_ctx=get_budget().context_window,
                    )
                    break  # success
                except Exception:
                    if attempt == 0:
                        logger.info(
                            "Skeleton classify batch %d-%d attempt 1 failed, retrying...",
                            i, i + len(batch),
                        )
                    else:
                        logger.warning(
                            "Skeleton classify batch %d-%d failed after retry",
                            i, i + len(batch), exc_info=True,
                        )
            else:
                continue  # both attempts failed

            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse classify result as JSON")
                    continue

            # Parse assignments
            for assign in result.get("assignments", []):
                child = assign.get("child", "")
                parent = assign.get("parent", "")
                confidence = assign.get("confidence", "medium")
                if not child or not parent or child == parent:
                    continue
                if child not in all_locs_full or parent not in all_locs_full:
                    continue
                weight = _CONFIDENCE_WEIGHT.get(confidence, 3)
                # Boost orphaned kingdoms
                child_tier = location_tiers.get(child, "city")
                child_parent = current_parents.get(child)
                if child_tier == "kingdom" and (
                    child_parent == uber_root or child_parent is None
                ):
                    weight = max(weight, 10)
                votes.setdefault(child, Counter())[parent] += weight

            # Parse synonyms
            for syn in result.get("synonyms", []):
                canonical = syn.get("canonical", "")
                alias = syn.get("alias", "")
                if canonical and alias and canonical != alias:
                    if canonical in all_locs_full and alias in all_locs_full:
                        synonyms.append((canonical, alias))

        return votes, synonyms

    @staticmethod
    def _build_classify_prompt(
        novel_title: str,
        genre: str,
        children: list[str],
        parent_candidates: list[str],
        child_label: str,
        parent_label: str,
    ) -> str:
        """Build a focused classification prompt."""
        children_str = "、".join(children)
        parents_str = "、".join(parent_candidates)
        return f"""小说「{novel_title}」（{genre}类）

## 任务
请为以下每个{child_label}指定它属于哪个{parent_label}。

## 可选的{parent_label}
{parents_str}

## 需要分类的{child_label}
{children_str}

## 规则
1. child 和 parent 必须使用上述列表中的原始名称
2. 只输出你有把握的分类（high=确定, medium=较确定）
3. 如果某个{child_label}不属于任何{parent_label}，跳过它
4. 如果发现同义地名（同一地点的不同叫法），在 synonyms 中声明

请输出 JSON："""

    async def _get_directions(
        self,
        novel_title: str,
        regions: list[str],
        all_locs_full: set[str],
    ) -> list[dict]:
        """Get direction constraints for top-level regions."""
        regions_str = "、".join(regions)
        prompt = f"""小说「{novel_title}」中，以下区域之间的方位关系是什么？

区域：{regions_str}

请标注你确定的方位关系（如"东胜神洲在天下的东方"）。
只输出 JSON，不要解释。"""

        try:
            result, _ = await self.llm.generate(
                system="你是一个小说地理分析专家。请严格按照 JSON 格式输出。",
                prompt=prompt,
                format=_DIRECTION_SCHEMA,
                temperature=0.1,
                max_tokens=2048,
                timeout=60,
            )
        except Exception:
            logger.warning("Direction generation failed", exc_info=True)
            return []

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return []

        constraints = []
        for d in result.get("directions", []):
            source = d.get("source", "")
            target = d.get("target", "")
            direction = d.get("direction", "")
            confidence = d.get("confidence", "medium")
            if not source or not target or source == target:
                continue
            if direction not in _VALID_DIRECTIONS:
                continue
            if source not in all_locs_full or target not in all_locs_full:
                continue
            conf_score = {"high": 1.0, "medium": 0.8}.get(confidence, 0.6)
            constraints.append({
                "source": source, "target": target,
                "relation_type": "direction", "value": direction,
                "confidence": confidence, "confidence_score": conf_score,
                "source_type": "llm_anchor",
            })
        return constraints

    @staticmethod
    def _find_uber_root(parents: dict[str, str]) -> str | None:
        children_set = set(parents.keys())
        parent_counts: Counter = Counter()
        for p in parents.values():
            if p not in children_set:
                parent_counts[p] += 1
        return parent_counts.most_common(1)[0][0] if parent_counts else None
