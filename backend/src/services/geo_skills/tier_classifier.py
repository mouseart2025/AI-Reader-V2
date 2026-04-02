"""TierClassifier — GeoSkill that re-classifies location tiers.

Uses WorldStructureAgent._classify_tier() with the latest suffix rules
to fix stale tier classifications (e.g., 天庭:site → 天庭:continent).

This skill always succeeds (no LLM dependency).
"""

from __future__ import annotations

import logging

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)


class TierClassifier(GeoSkill):
    """Re-classify location tiers with latest rules."""

    def __init__(self, novel_id: str):
        self._novel_id = novel_id

    @property
    def name(self) -> str:
        return "TierClassifier"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.services.world_structure_agent import WorldStructureAgent
        from src.db import world_structure_store

        # We need a WorldStructureAgent instance for _classify_tier
        ws = await world_structure_store.load(self._novel_id)
        if not ws:
            return SkillResult.empty(self.name, "WorldStructure not found")

        agent = WorldStructureAgent(self._novel_id)
        agent.structure = ws
        # Sync tiers from snapshot into agent's structure for constraint checks
        ws.location_tiers = dict(snapshot.location_tiers)

        tier_updates: dict[str, str] = {}
        for loc_name, old_tier in snapshot.location_tiers.items():
            parent = snapshot.location_parents.get(loc_name)
            level = 1 if parent else 0
            new_tier = agent._classify_tier(loc_name, "", parent, level)
            if new_tier != old_tier:
                tier_updates[loc_name] = new_tier

        result = SkillResult(
            skill_name=self.name,
            tier_updates=tier_updates,
        )
        if tier_updates:
            logger.info(
                "TierClassifier: %d tier changes (e.g., %s)",
                len(tier_updates),
                ", ".join(f"{k}:{v}" for k, v in list(tier_updates.items())[:3]),
            )
        return result
