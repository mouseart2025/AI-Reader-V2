"""ReviewerSkill — GeoSkill wrapping LocationHierarchyReviewer.

Three LLM phases, each independently fault-tolerant:
1. review(): classify orphan roots → parent votes
2. reflect_suspicious(): validate suspicious pairs → corrections
3. validate_hierarchy(): post-consolidation validation → corrections

Each phase failing does not affect the others or the pipeline.
"""

from __future__ import annotations

import logging
from collections import Counter

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)


class ReviewerSkill(GeoSkill):
    """LLM-based hierarchy review, reflection, and validation."""

    def __init__(self, novel_title: str = "", scene_analysis: dict | None = None):
        self._novel_title = novel_title
        self._scene_analysis = scene_analysis or {}

    @property
    def name(self) -> str:
        return "LLM审查"

    @property
    def requires_llm(self) -> bool:
        return True

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.services.location_hierarchy_reviewer import LocationHierarchyReviewer

        reviewer = LocationHierarchyReviewer()
        result = SkillResult(skill_name=self.name)
        total_llm = 0

        # Count orphans for reporting
        orphan_count = sum(
            1 for loc in snapshot.location_tiers
            if loc not in snapshot.location_parents
            and snapshot.location_tiers.get(loc) not in ("world",)
        )
        result.logs.append(f"📋 发现 {orphan_count} 个无归属地点，{len(self._find_suspicious_pairs(snapshot))} 对可疑关系")

        # ── Phase 1: Review orphan roots ──
        result.logs.append("🔍 阶段 1/3: 审查无归属地点...")
        try:
            review_votes = await reviewer.review(
                location_tiers=snapshot.location_tiers,
                current_parents=snapshot.location_parents,
                scene_analysis=self._scene_analysis,
                novel_genre_hint=snapshot.novel_genre_hint,
            )
            if review_votes:
                result.new_votes.update(review_votes)
                total_llm += 1
                result.logs.append(f"  ✅ LLM 建议 {len(review_votes)} 个地点的归属")
            else:
                result.logs.append("  ✅ 无需调整")
        except Exception as e:
            result.logs.append(f"  ⚠️ 跳过: {str(e)[:60]}")

        # ── Phase 2: Reflect on suspicious pairs ──
        suspicious = self._find_suspicious_pairs(snapshot)
        if suspicious:
            result.logs.append(f"🤔 阶段 2/3: 反思 {len(suspicious)} 对可疑关系...")
            try:
                reflections = await reviewer.reflect_suspicious(
                    self._novel_title, suspicious,
                )
                applied = 0
                for r in reflections:
                    child = r.get("child", "")
                    parent = r.get("parent", "")
                    verdict = r.get("verdict", "")
                    if not child or not parent or verdict in ("correct", "uncertain", ""):
                        continue
                    if verdict == "reverse":
                        old_parent = snapshot.location_parents.get(child)
                        if old_parent:
                            result.parent_overrides[parent] = old_parent
                            result.parent_overrides.pop(child, None)
                            applied += 1
                    elif verdict == "sibling":
                        from src.services.geo_skills.vote_resolver import _find_common_parent
                        known = set(snapshot.location_tiers.keys())
                        common = _find_common_parent(
                            child, parent, snapshot.parent_votes, known,
                        )
                        if common:
                            result.parent_overrides[child] = common
                            result.parent_overrides[parent] = common
                            applied += 1
                total_llm += 1
                result.logs.append(f"  ✅ LLM 反思 {len(reflections)} 条，修正 {applied} 处")
            except Exception as e:
                result.logs.append(f"  ⚠️ 跳过: {str(e)[:60]}")
        else:
            result.logs.append("🤔 阶段 2/3: 无可疑关系，跳过反思")

        # ── Phase 3: Validate hierarchy ──
        result.logs.append("🔎 阶段 3/3: 验证层级合理性...")
        try:
            corrections = await reviewer.validate_hierarchy(
                location_parents=snapshot.location_parents,
                location_tiers=snapshot.location_tiers,
                novel_genre_hint=snapshot.novel_genre_hint,
            )
            for corr in corrections:
                child = corr.get("child", "")
                correct_parent = corr.get("correct_parent", "")
                if child and correct_parent:
                    result.parent_overrides[child] = correct_parent
            total_llm += 1
            result.logs.append(f"  ✅ 验证修正 {len(corrections)} 处")
        except Exception as e:
            result.logs.append(f"  ⚠️ 跳过: {str(e)[:60]}")

        result.logs.append(f"📊 LLM审查总计: {total_llm} 次调用, {len(result.parent_overrides)} 处修正")
        result.llm_calls = total_llm
        return result

    @staticmethod
    def _find_suspicious_pairs(snapshot: HierarchySnapshot) -> list[dict]:
        """Identify suspicious parent-child pairs for LLM reflection."""
        from src.services.world_structure_agent import _get_suffix_rank

        suspicious: list[dict] = []
        parents = snapshot.location_parents
        votes = snapshot.parent_votes
        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        for child, parent in parents.items():
            if not parent:
                continue
            # Skip micro-locations
            if freq.get(child, 0) <= 2:
                continue

            reasons = []
            c_rank = _get_suffix_rank(child)
            p_rank = _get_suffix_rank(parent)
            if c_rank is not None and p_rank is not None and c_rank == p_rank:
                reasons.append("same_suffix")

            child_votes = votes.get(child, Counter())
            parent_vote = child_votes.get(parent, 0)
            runner_up = max(
                (v for k, v in child_votes.items() if k != parent), default=0,
            )
            if parent_vote > 0 and runner_up > 0 and parent_vote / runner_up < 2.0:
                reasons.append("close_votes")

            if reasons:
                suspicious.append({
                    "child": child, "parent": parent,
                    "reasons": reasons,
                    "child_tier": tiers.get(child, "unknown"),
                    "parent_tier": tiers.get(parent, "unknown"),
                })

        return suspicious[:20]  # Cap at 20
