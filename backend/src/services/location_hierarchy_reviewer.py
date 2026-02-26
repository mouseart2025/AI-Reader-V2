"""LocationHierarchyReviewer: single LLM call to review and fix orphan root nodes.

Post-analysis step that globally reviews the location hierarchy tree and
suggests parent assignments for orphan root nodes (locations without parents
that are not top-level tiers like world/continent).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path

from src.infra.context_budget import get_budget
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

        budget = get_budget()
        try:
            result, _usage = await self.llm.generate(
                system=system,
                prompt=prompt,
                format=_REVIEW_SCHEMA,
                temperature=0.1,
                max_tokens=4096,
                timeout=budget.hierarchy_timeout,
                num_ctx=min(budget.context_window, 8192),
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

    # ── LLM output schema for hierarchy validation ──
    _VALIDATION_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "corrections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "child": {"type": "string"},
                        "wrong_parent": {"type": "string"},
                        "correct_parent": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["child", "wrong_parent", "correct_parent", "confidence"],
                },
            },
        },
        "required": ["corrections"],
    }

    # Subtree size threshold: trees smaller than this are batched together
    _SUBTREE_MIN_SIZE = 5
    # Per-subtree LLM timeout (seconds)
    _SUBTREE_TIMEOUT = 45.0
    # Max nodes per subtree slice sent to LLM
    _SUBTREE_MAX_DETAIL = 30

    async def validate_hierarchy(
        self,
        location_parents: dict[str, str],
        location_tiers: dict[str, str],
        novel_genre_hint: str | None,
    ) -> list[dict]:
        """Post-consolidation LLM validation of hierarchy reasonableness.

        Splits validation by uber-root's direct subtrees for lower LLM
        cognitive load and better parallelism.  Cloud mode runs subtrees
        concurrently via ``asyncio.gather()``; local mode runs sequentially.

        Returns a list of correction dicts:
        ``[{child, wrong_parent, correct_parent, confidence, reason}]``
        """
        if not location_parents:
            return []

        # Find uber-root
        children_set = set(location_parents.keys())
        parent_counts: Counter = Counter()
        for p in location_parents.values():
            if p not in children_set:
                parent_counts[p] += 1
        if not parent_counts:
            return []
        uber_root = parent_counts.most_common(1)[0][0]

        # Build root children list
        root_children_names = [
            child for child, parent in location_parents.items()
            if parent == uber_root
        ]
        if not root_children_names:
            return []

        # Build parent→children index for subtree collection
        children_of: dict[str, list[str]] = defaultdict(list)
        for child, parent in location_parents.items():
            children_of[parent].append(child)

        # Collect subtrees rooted at each uber-root direct child
        def _collect_subtree(root: str) -> dict[str, str]:
            """BFS to collect all descendants' parent relationships."""
            subtree: dict[str, str] = {}
            queue = [root]
            while queue:
                node = queue.pop(0)
                for ch in children_of.get(node, []):
                    subtree[ch] = node
                    queue.append(ch)
            return subtree

        # Partition into large subtrees and small-tree batch
        large_slices: list[tuple[str, dict[str, str]]] = []
        small_batch_parents: dict[str, str] = {}
        small_batch_roots: list[str] = []

        for rc in root_children_names:
            st = _collect_subtree(rc)
            # subtree includes rc's own children; total nodes = len(st) + 1 (rc itself)
            total_nodes = len(st) + 1
            if total_nodes >= self._SUBTREE_MIN_SIZE:
                # Include the root child → uber_root relationship for context
                st[rc] = uber_root
                large_slices.append((rc, st))
                logger.info(
                    "Subtree partition: %s (%d nodes) → independent slice",
                    rc, total_nodes,
                )
            else:
                small_batch_parents[rc] = uber_root
                small_batch_parents.update(st)
                small_batch_roots.append(rc)

        # Add small-tree batch if non-empty
        if small_batch_parents:
            label = "小子树批次({})".format(", ".join(small_batch_roots[:5]))
            large_slices.append((label, small_batch_parents))
            logger.info(
                "Subtree partition: %d small subtrees (%d nodes) → batched",
                len(small_batch_roots), len(small_batch_parents),
            )

        logger.info(
            "Subtree validation: %d slices from %d root children",
            len(large_slices), len(root_children_names),
        )

        all_known = set(location_tiers.keys()) | set(location_parents.values())

        # Determine cloud vs local mode for concurrency
        from src.infra.openai_client import OpenAICompatibleClient
        from src.infra.anthropic_client import AnthropicClient
        is_cloud = isinstance(self.llm, (OpenAICompatibleClient, AnthropicClient))

        # Build coroutines for each slice
        async def _run_slice(label: str, sp: dict[str, str]) -> list[dict]:
            try:
                return await asyncio.wait_for(
                    self._validate_subtree(
                        subtree_root=label,
                        subtree_parents=sp,
                        location_tiers=location_tiers,
                        location_parents=location_parents,
                        all_known=all_known,
                        uber_root=uber_root,
                        novel_genre_hint=novel_genre_hint,
                    ),
                    timeout=self._SUBTREE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Subtree validation timed out (%.0fs): %s",
                    self._SUBTREE_TIMEOUT, label,
                )
                return []
            except Exception:
                logger.warning(
                    "Subtree validation failed: %s", label, exc_info=True,
                )
                return []

        if is_cloud and len(large_slices) > 1:
            # Cloud: concurrent execution
            logger.info("Cloud mode: running %d subtree validations concurrently", len(large_slices))
            results = await asyncio.gather(
                *[_run_slice(label, sp) for label, sp in large_slices]
            )
            all_corrections = []
            for r in results:
                all_corrections.extend(r)
        else:
            # Local: sequential execution
            all_corrections = []
            for label, sp in large_slices:
                corrs = await _run_slice(label, sp)
                all_corrections.extend(corrs)

        logger.info(
            "Subtree validation complete: %d total corrections from %d slices",
            len(all_corrections), len(large_slices),
        )
        return all_corrections

    async def _validate_subtree(
        self,
        subtree_root: str,
        subtree_parents: dict[str, str],
        location_tiers: dict[str, str],
        location_parents: dict[str, str],
        all_known: set[str],
        uber_root: str,
        novel_genre_hint: str | None,
    ) -> list[dict]:
        """Validate a single subtree slice via LLM.

        Args:
            subtree_root: Display name of this subtree (for logging)
            subtree_parents: ``{child: parent}`` within this subtree
            location_tiers: Full tier map for all locations
            location_parents: Full parent map (for actual_parent validation)
            all_known: Set of all known location names
            uber_root: The uber-root name
            novel_genre_hint: Genre hint string
        """
        if not subtree_parents:
            return []

        # Determine the effective root for this subtree's prompt
        # For large subtrees, use the subtree root as context anchor
        # For the small batch, use uber_root
        effective_root = uber_root

        # Collect all locations in this subtree
        subtree_locs = set(subtree_parents.keys()) | set(subtree_parents.values())

        # Format root children with tier and child count (within subtree)
        child_count_map: Counter = Counter(subtree_parents.values())
        direct_children = [
            ch for ch, p in subtree_parents.items()
            if p == uber_root or p not in subtree_parents
        ]
        root_children_lines = []
        for name in sorted(direct_children):
            tier = location_tiers.get(name, "unknown")
            n_ch = child_count_map.get(name, 0)
            root_children_lines.append(f"  {name} [tier={tier}, 子节点={n_ch}]")

        # Build detail lines (limited to _SUBTREE_MAX_DETAIL)
        detail_lines = []
        shown = 0
        for child, parent in sorted(subtree_parents.items()):
            if shown >= self._SUBTREE_MAX_DETAIL:
                break
            c_tier = location_tiers.get(child, "unknown")
            detail_lines.append(f"  {parent} → {child} [tier={c_tier}]")
            shown += 1

        # Highlight suspicious items: building/site directly under uber-root
        suspicious_lines = []
        for child, parent in sorted(subtree_parents.items()):
            if parent == uber_root:
                tier = location_tiers.get(child, "unknown")
                if tier in ("building", "site"):
                    suspicious_lines.append(
                        f"  ⚠ {child} [tier={tier}] → parent={uber_root}"
                    )

        # Load prompt template and format
        path = _PROMPTS_DIR / "hierarchy_validation.txt"
        template = path.read_text(encoding="utf-8")
        prompt = template.format(
            genre_hint=novel_genre_hint or "未知",
            uber_root=effective_root,
            root_children="\n".join(root_children_lines) or "无",
            hierarchy_detail="\n".join(detail_lines) or "无",
            suspicious_items="\n".join(suspicious_lines) or "无可疑项",
        )

        system = "你是一个小说地理分析专家。请严格按照 JSON 格式输出。"
        budget = get_budget()

        logger.info(
            "Validating subtree: %s (%d nodes, %d detail lines)",
            subtree_root, len(subtree_parents), len(detail_lines),
        )

        try:
            result, _usage = await self.llm.generate(
                system=system,
                prompt=prompt,
                format=self._VALIDATION_SCHEMA,
                temperature=0.1,
                max_tokens=4096,
                timeout=budget.hierarchy_timeout,
                num_ctx=min(budget.context_window, 8192),
            )
        except Exception:
            logger.warning(
                "LLM hierarchy validation failed for subtree: %s",
                subtree_root, exc_info=True,
            )
            return []

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse LLM validation result for subtree: %s",
                    subtree_root,
                )
                return []

        # Parse and validate corrections
        corrections = []
        for corr in result.get("corrections", []):
            child = corr.get("child", "")
            wrong_parent = corr.get("wrong_parent", "")
            correct_parent = corr.get("correct_parent", "")
            confidence = corr.get("confidence", "")

            if not child or not correct_parent:
                continue
            if child not in all_known or correct_parent not in all_known:
                continue
            if confidence not in ("high", "medium"):
                continue
            actual_parent = location_parents.get(child)
            if actual_parent != wrong_parent:
                continue
            if child == correct_parent:
                continue

            corrections.append({
                "child": child,
                "wrong_parent": wrong_parent,
                "correct_parent": correct_parent,
                "confidence": confidence,
                "reason": corr.get("reason", ""),
            })
            logger.debug(
                "Subtree %s: %s: %s → %s (confidence=%s, reason=%s)",
                subtree_root, child, wrong_parent, correct_parent,
                confidence, corr.get("reason", ""),
            )

        logger.info(
            "Subtree %s: %d corrections from %d LLM suggestions",
            subtree_root, len(corrections),
            len(result.get("corrections", [])),
        )
        return corrections
