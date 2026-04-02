"""GeoSkill base class — composable geographic analysis unit.

Each skill:
- Receives a HierarchySnapshot (immutable)
- Returns a SkillResult (votes, overrides, metadata)
- Never modifies the snapshot directly
- Can fail gracefully (returns empty SkillResult)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from src.services.geo_skills.snapshot import (
    HierarchyMetrics,
    HierarchySnapshot,
    SkillResult,
)

logger = logging.getLogger(__name__)


class GeoSkill(ABC):
    """Base class for geographic analysis skills."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Skill name for logging and metadata."""

    @property
    def requires_llm(self) -> bool:
        """Whether this skill uses LLM calls (may fail)."""
        return False

    async def run(self, snapshot: HierarchySnapshot) -> SkillResult:
        """Execute skill with timing and error handling.

        Subclasses implement `execute()`. This wrapper adds:
        - Timing measurement
        - Error catching (LLM skills fail gracefully)
        - Metrics computation
        """
        start = time.monotonic()
        try:
            result = await self.execute(snapshot)
            result.duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "GeoSkill [%s] completed in %dms: %d votes, %d overrides",
                self.name, result.duration_ms,
                len(result.new_votes), len(result.parent_overrides),
            )
            return result
        except Exception as e:
            duration = int((time.monotonic() - start) * 1000)
            logger.warning(
                "GeoSkill [%s] failed after %dms: %s",
                self.name, duration, e,
                exc_info=not self.requires_llm,  # full trace for non-LLM errors
            )
            result = SkillResult.empty(self.name, error=str(e))
            result.duration_ms = duration
            return result

    @abstractmethod
    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        """Core skill logic. Implement in subclasses."""
