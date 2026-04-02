"""ConsolidatorSkill — GeoSkill wrapping hierarchy_consolidator.

Pure algorithm (no LLM). Reduces roots, fixes inversions, adopts orphans.
Always succeeds.
"""

from __future__ import annotations

import logging

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)


class ConsolidatorSkill(GeoSkill):
    """Post-processing: reduce roots, fix inversions, adopt orphans."""

    @property
    def name(self) -> str:
        return "Consolidator"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.services.hierarchy_consolidator import consolidate_hierarchy

        # consolidate_hierarchy is a pure function that modifies dicts in place
        parents = dict(snapshot.location_parents)
        tiers = dict(snapshot.location_tiers)

        parents, tiers = consolidate_hierarchy(
            parents,
            tiers,
            novel_genre_hint=snapshot.novel_genre_hint,
            parent_votes=snapshot.parent_votes,
            saved_parents=dict(snapshot.location_parents),  # self as fallback
        )

        # Continent protection
        uber_root = None
        for loc, tier in tiers.items():
            if tier == "world":
                uber_root = loc
                break
        if uber_root:
            for loc, tier in tiers.items():
                if tier == "continent" and parents.get(loc) not in (uber_root, None):
                    parents[loc] = uber_root

        result = SkillResult(
            skill_name=self.name,
            parent_overrides=parents,
            tier_updates=tiers,
        )
        logger.info(
            "Consolidator: %d parents, %d tiers",
            len(parents), len(tiers),
        )
        return result
