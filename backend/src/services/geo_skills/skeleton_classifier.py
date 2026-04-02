"""SkeletonClassifier — GeoSkill wrapping MacroSkeletonGenerator.

Delegates to the phased classification skeleton generator.
This skill requires LLM and may fail gracefully.
"""

from __future__ import annotations

import logging

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)


class SkeletonClassifier(GeoSkill):
    """Classify locations into hierarchy via phased LLM calls."""

    def __init__(self, novel_title: str = ""):
        self._novel_title = novel_title

    @property
    def name(self) -> str:
        return "SkeletonClassifier"

    @property
    def requires_llm(self) -> bool:
        return True

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.services.macro_skeleton_generator import MacroSkeletonGenerator

        gen = MacroSkeletonGenerator()
        votes, synonyms, directions = await gen.generate(
            novel_title=self._novel_title,
            novel_genre_hint=snapshot.novel_genre_hint,
            location_tiers=snapshot.location_tiers,
            current_parents=snapshot.location_parents,
            location_frequencies=snapshot.location_frequencies,
        )

        if not votes and not synonyms and not directions:
            return SkillResult.empty(self.name, "骨架生成无结果")

        result = SkillResult(
            skill_name=self.name,
            new_votes=votes,
            synonym_pairs=synonyms,
            direction_constraints=directions,
            llm_calls=3,  # Phase 1 + Phase 2 + directions
        )
        logger.info(
            "SkeletonClassifier: %d votes, %d synonyms, %d directions",
            len(votes), len(synonyms), len(directions),
        )
        return result
