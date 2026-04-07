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

            # Emit sub-step logs (if skill provided them)
            for log_msg in result.logs:
                yield ProgressEvent(tag, f"  {log_msg}")

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

        # ── Re-detect layers after parent changes ──
        # Parent changes may invalidate old layer propagation (e.g., a location
        # was under 天庭→celestial but is now under 傲来国→overworld).
        # Reset all non-keyword-detected layers to overworld, then re-propagate.
        from src.services.world_structure_agent import WorldStructureAgent
        agent = WorldStructureAgent(self.novel_id)
        agent.structure = ws

        # Step 1: Reset all layers to overworld
        for loc_name in list(ws.location_layer_map.keys()):
            ws.location_layer_map[loc_name] = "overworld"

        # Step 2: Re-detect layers from keywords
        for loc_name in ws.location_tiers:
            detected = agent._detect_layer(loc_name, "")
            if detected:
                agent._ensure_layer_exists(detected)
                ws.location_layer_map[loc_name] = detected

        # Step 3: Re-propagate from parents (child inherits parent's non-overworld layer)
        for _ in range(5):
            changed = False
            for child, parent in ws.location_parents.items():
                p_layer = ws.location_layer_map.get(parent, "overworld")
                c_layer = ws.location_layer_map.get(child, "overworld")
                if p_layer != "overworld" and c_layer == "overworld":
                    ws.location_layer_map[child] = p_layer
                    changed = True
            if not changed:
                break

        # Step 4: Inject virtual layer root nodes under uber_root
        # Goal: 天下's children should be layer roots only, not a flat mix.
        # For each non-overworld layer, re-parent its top-level locations under
        # a layer root node (either an existing location or a virtual one).
        self._inject_layer_roots(ws)

        await world_structure_store.save(self.novel_id, ws)

        # Invalidate map cache after hierarchy change
        from src.services.visualization_service import _map_cache
        keys_to_remove = [k for k in _map_cache if k.startswith(self.novel_id)]
        for k in keys_to_remove:
            del _map_cache[k]

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

    @staticmethod
    def _inject_layer_roots(ws) -> None:
        """Inject virtual layer root nodes so 天下's children are grouped by layer.

        Before: 天下 → [东胜神洲, 天庭, 幽冥界, 庄院, ...] (flat mix)
        After:  天下 → [主世界, 天界, 冥界/地府, 海底/龙宫]
                主世界 → [东胜神洲, 西牛贺洲, ...]
                天界 → [天庭, 离恨天, ...]

        For each layer with locations:
        1. Find the layer's display name from ws.layers
        2. If an existing location matches that name, promote it as root
        3. Otherwise create a virtual node
        4. Re-parent all 天下-children in that layer under the root
        """
        parents = ws.location_parents
        tiers = ws.location_tiers
        layer_map = ws.location_layer_map

        # Find uber_root (天下 or equivalent)
        uber_root = None
        for name, tier in tiers.items():
            if tier == "world":
                uber_root = name
                break
        if not uber_root:
            # Fallback: find node with no parent that has most children
            for name in tiers:
                if name not in parents or not parents.get(name):
                    uber_root = name
                    break
        if not uber_root:
            return

        # Phase A: Fix cross-layer parenting — locations whose parent is
        # in a different layer should be detached to become layer top-level.
        # e.g., 龙宫(underwater) parent=黑风山(overworld) → parent=uber_root
        for child in list(parents.keys()):
            parent = parents[child]
            c_layer = layer_map.get(child, "overworld")
            p_layer = layer_map.get(parent, "overworld")
            if c_layer != "overworld" and p_layer != c_layer and parent != uber_root:
                parents[child] = uber_root

        # Collect uber_root's direct children, grouped by layer
        children_by_layer: dict[str, list[str]] = {}
        for child, parent in parents.items():
            if parent == uber_root:
                layer = layer_map.get(child, "overworld")
                children_by_layer.setdefault(layer, []).append(child)

        # Build layer_id → display name mapping from ws.layers
        layer_names: dict[str, str] = {}
        for layer_def in ws.layers:
            if hasattr(layer_def, "layer_id"):
                layer_names[layer_def.layer_id] = layer_def.name
            elif isinstance(layer_def, dict):
                layer_names[layer_def.get("layer_id", "")] = layer_def.get("name", "")

        # For each layer (including overworld), create/find a root and re-parent
        for layer_id, children in children_by_layer.items():
            if len(children) <= 1:
                continue  # single child → already clean

            root_name = layer_names.get(layer_id, layer_id)

            # Check if an existing child can serve as root
            # (location name matches layer display name, or is the largest subtree)
            existing_root = None
            for c in children:
                if c == root_name or c in root_name.split("/"):
                    existing_root = c
                    break

            if existing_root:
                # Use existing location as root — re-parent siblings under it
                for c in children:
                    if c != existing_root:
                        parents[c] = existing_root
                logger.info(
                    "Layer root [%s]: %s (existing, %d children adopted)",
                    layer_id, existing_root, len(children) - 1,
                )
            else:
                # Create virtual node
                parents[root_name] = uber_root
                tiers[root_name] = "continent" if layer_id == "overworld" else "realm"
                layer_map[root_name] = layer_id
                for c in children:
                    parents[c] = root_name
                logger.info(
                    "Layer root [%s]: %s (virtual, %d children)",
                    layer_id, root_name, len(children),
                )

    async def get_version_history(self) -> list[dict]:
        """Get version history with metrics for paper tracking."""
        return await self.store.list_versions(self.novel_id)
