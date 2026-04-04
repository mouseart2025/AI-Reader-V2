"""GeoOrchestrator — chains GeoSkills with snapshot versioning.

Orchestrates the rebuild pipeline:
1. Load current snapshot (or create from WorldStructure)
2. Run skills in sequence, each producing a new snapshot version
3. Save each version for rollback and paper metrics tracking
4. Apply final result to WorldStructure

Key guarantee: each skill failure is isolated — the pipeline continues
with the previous snapshot, and no data is lost.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import AsyncGenerator

from src.services.geo_skills.snapshot import (
    HierarchyMetrics,
    HierarchySnapshot,
    SkillResult,
)
from src.services.geo_skills.snapshot_store import (
    SnapshotStore,
    snapshot_from_world_structure,
)
from src.services.geo_skills.base import GeoSkill

logger = logging.getLogger(__name__)


class ProgressEvent:
    """SSE progress event for the rebuild pipeline."""

    def __init__(self, stage: str, message: str, **extra):
        self.stage = stage
        self.message = message
        self.extra = extra


class GeoOrchestrator:
    """Orchestrate geographic analysis skills with snapshot versioning."""

    def __init__(self, novel_id: str):
        self.novel_id = novel_id
        self.store = SnapshotStore()
        self._skills: list[tuple[str, GeoSkill]] = []

    def add_skill(self, tag: str, skill: GeoSkill) -> GeoOrchestrator:
        """Add a skill to the pipeline. Returns self for chaining."""
        self._skills.append((tag, skill))
        return self

    async def run(self) -> AsyncGenerator[ProgressEvent, None]:
        """Execute all skills in sequence, yielding progress events.

        Each skill:
        1. Receives current snapshot
        2. Produces SkillResult
        3. Result is applied to create new snapshot
        4. Snapshot is saved with metrics
        5. Progress event is yielded

        If a skill fails, pipeline continues with previous snapshot.
        """
        # Load or create initial snapshot
        yield ProgressEvent("init", "正在加载层级快照...")
        snapshot = await self.store.load_latest(self.novel_id)
        if snapshot is None:
            snapshot = await snapshot_from_world_structure(self.novel_id)
            await self.store.save(self.novel_id, snapshot, tag="import")
            yield ProgressEvent(
                "init",
                f"从 WorldStructure 导入 v0 快照 "
                f"({len(snapshot.location_tiers)} 地点, "
                f"{len(snapshot.location_parents)} parents)",
            )

        initial_metrics = HierarchyMetrics.compute(snapshot)
        yield ProgressEvent(
            "init",
            f"基线: {initial_metrics.summary()}",
        )

        # Run each skill
        for tag, skill in self._skills:
            yield ProgressEvent(tag, f"⏳ {skill.name}...")

            result = await skill.run(snapshot)

            if not result.success:
                yield ProgressEvent(
                    tag,
                    f"⚠️ {skill.name} 跳过: {result.error_message[:80]} "
                    f"({result.duration_ms//1000}s) — 不影响其他步骤",
                )
                continue

            # Special handling for VoteBuilder which needs to update
            # frequency data on the snapshot
            if hasattr(result, '_extra') and result._extra:
                extra = result._extra
                # Create new snapshot with updated frequency data
                new_snap = HierarchySnapshot(
                    location_parents=snapshot.location_parents,
                    location_tiers=snapshot.location_tiers,
                    parent_votes={k: Counter(v) for k, v in result.new_votes.items()},
                    location_frequencies=extra.get(
                        "location_frequencies", snapshot.location_frequencies),
                    chapter_settings=extra.get(
                        "chapter_settings", snapshot.chapter_settings),
                    location_chapters=extra.get(
                        "location_chapters", snapshot.location_chapters),
                    version=snapshot.version + 1,
                    source=result.skill_name,
                    timestamp=time.time(),
                    novel_genre_hint=snapshot.novel_genre_hint,
                )
            else:
                new_snap = snapshot.apply(result)

            # Save snapshot
            await self.store.save(self.novel_id, new_snap, tag=tag)
            snapshot = new_snap

            # Report metrics
            metrics = HierarchyMetrics.compute(snapshot)
            result.metrics_after = {
                "avg_depth": metrics.avg_depth,
                "max_children": metrics.max_children,
                "root_count": metrics.root_count,
            }

            # Format duration
            dur = f"{result.duration_ms}ms" if result.duration_ms < 1000 else f"{result.duration_ms/1000:.1f}s"
            yield ProgressEvent(
                tag,
                f"✅ {skill.name} ({dur}): "
                f"深度={metrics.avg_depth:.1f} 最大子节点={metrics.max_children}",
                votes=len(result.new_votes),
                overrides=len(result.parent_overrides),
                synonyms=len(result.synonym_pairs),
            )

        # Final metrics comparison
        final_metrics = HierarchyMetrics.compute(snapshot)
        yield ProgressEvent(
            "done",
            f"管线完成: v{snapshot.version}, "
            f"depth {initial_metrics.avg_depth:.2f}→{final_metrics.avg_depth:.2f}, "
            f"max_ch {initial_metrics.max_children}→{final_metrics.max_children}",
            initial_metrics={
                "avg_depth": initial_metrics.avg_depth,
                "max_children": initial_metrics.max_children,
                "root_count": initial_metrics.root_count,
            },
            final_metrics={
                "avg_depth": final_metrics.avg_depth,
                "max_children": final_metrics.max_children,
                "root_count": final_metrics.root_count,
            },
            version=snapshot.version,
        )

    async def apply_to_world_structure(self) -> dict:
        """Apply latest snapshot to WorldStructure (bridge to old system).

        Returns summary dict.
        """
        snapshot = await self.store.load_latest(self.novel_id)
        if not snapshot:
            return {"error": "No snapshot available"}

        from src.db import world_structure_store

        ws = await world_structure_store.load(self.novel_id)
        if not ws:
            return {"error": "WorldStructure not found"}

        old_parents = len(ws.location_parents)
        ws.location_parents = dict(snapshot.location_parents)
        ws.location_tiers = dict(snapshot.location_tiers)
        await world_structure_store.save(self.novel_id, ws)

        metrics = HierarchyMetrics.compute(snapshot)
        return {
            "version": snapshot.version,
            "source": snapshot.source,
            "old_parent_count": old_parents,
            "new_parent_count": len(snapshot.location_parents),
            "metrics": {
                "avg_depth": metrics.avg_depth,
                "max_children": metrics.max_children,
                "root_count": metrics.root_count,
            },
        }

    async def get_version_history(self) -> list[dict]:
        """Get version history with metrics for paper tracking."""
        return await self.store.list_versions(self.novel_id)
