"""Pure MWA vs Hybrid ablation — validates §3.3 hybrid design.

The deployed EdmondsResolver is NOT pure MWA optimization. It is a hybrid:
  Phase 1: preserve LLM-extracted parents as base + apply name-containment
           and prior overrides
  Phase 2: run Edmonds, but USE its output only for orphans
  Phase 3: cycle repair (DFS, break weakest edge)
  Phase 4: phantom-parent lift heuristic
  Phase 5: degree balancing (MAX_CHILDREN=30, 10-iter)

This script defines PureMWAResolver — subclass that uses the same candidate
graph but runs pure MWA and uses ALL of Edmonds' output (no LLM base, no
post-repair).

Compares Hybrid vs Pure MWA structural metrics on 3 novels:
  - max_children (expected: Pure MWA much larger; no fan-out control)
  - root_count (expected: both 1; Hybrid by post-pass, Pure MWA by uber_root fallback)
  - cycles (expected: both 0 by virtue of Edmonds being acyclic algorithm)
  - total_parents (expected: both equal; same node set)

Populates Table 8 of paper/section_3_3_honest_rewrite_v15.tex.

Usage:
    cd backend && uv run python scripts/pure_mwa_ablation.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import Counter

import networkx as nx

# Make src/ importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.geo_skills.edmonds_resolver import EdmondsResolver
from src.services.geo_skills.knowledge_prior import KnowledgePrior
from src.services.geo_skills.orchestrator import GeoOrchestrator
from src.services.geo_skills.snapshot import HierarchyMetrics, HierarchySnapshot, SkillResult
from src.services.geo_skills.snapshot_store import (
    SnapshotStore,
    snapshot_from_world_structure,
)
from src.services.geo_skills.tier_classifier import TierClassifier
from src.services.geo_skills.vote_builder import VoteBuilder
from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)


class PureMWAResolver(EdmondsResolver):
    """Pure Maximum Weight Arborescence — no LLM base, no post-repair.

    Reuses parent EdmondsResolver's candidate graph construction (lines
    59-132) but discards Phases 1, 3, 4, 5. Phase 2 runs Edmonds on the
    full graph and uses ALL of its output as the parent assignment.
    """

    @property
    def name(self) -> str:
        return "纯MWA"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        votes = snapshot.parent_votes
        tiers = snapshot.location_tiers

        if not votes:
            return SkillResult.empty(self.name, "No votes to resolve")

        # Find uber_root (same as parent class)
        uber_root = self._find_uber_root(snapshot.location_parents)
        if not uber_root:
            for loc, tier in tiers.items():
                if tier == "world":
                    uber_root = loc
                    break
        if not uber_root:
            uber_root = "天下"

        # ── Build candidate graph (replicates parent class logic) ──
        G = nx.DiGraph()
        all_locs: set[str] = set(tiers.keys())
        all_locs.update(votes.keys())
        all_locs.add(uber_root)

        from src.services.world_structure_agent import _get_suffix_rank

        for child, vote_counter in votes.items():
            for parent, weight in vote_counter.items():
                if not parent or parent == child:
                    continue
                if parent not in all_locs:
                    continue
                w = float(weight)
                if w <= 0:
                    continue

                p_suf = _get_suffix_rank(parent)
                c_suf = _get_suffix_rank(child)
                if p_suf is not None and c_suf is not None and p_suf > c_suf:
                    w *= 0.1

                if G.has_edge(parent, child):
                    G[parent][child]["weight"] = max(
                        G[parent][child]["weight"], w
                    )
                else:
                    G.add_edge(parent, child, weight=w)

        for loc in all_locs:
            if loc not in G:
                G.add_node(loc)

        # ── Name-containment rule (kept; this is in the candidate graph,
        # not the algorithm) ──
        _NAME_CONTAIN_WEIGHT = 25.0
        sorted_locs = sorted(all_locs, key=len, reverse=True)
        for child in list(all_locs):
            for candidate in sorted_locs:
                if candidate == child or len(candidate) < 2:
                    continue
                if child.startswith(candidate) and candidate in all_locs:
                    if len(candidate) < 2:
                        continue
                    if G.has_edge(candidate, child):
                        G[candidate][child]["weight"] = max(
                            G[candidate][child]["weight"],
                            _NAME_CONTAIN_WEIGHT,
                        )
                    else:
                        G.add_edge(candidate, child, weight=_NAME_CONTAIN_WEIGHT)
                    break

        # ── Fallback edges (kept; necessary for Edmonds to not throw on
        # disconnected components — this is preprocessing of the candidate
        # graph, not part of pure MWA) ──
        _FALLBACK_WEIGHT = 0.001
        for node in G.nodes():
            if node != uber_root and not G.has_edge(uber_root, node):
                G.add_edge(uber_root, node, weight=_FALLBACK_WEIGHT)

        # ── PURE MWA: run Edmonds on the full graph and USE ALL output ──
        # This is the §3.3 Eq. 1 optimization, applied without modification.
        try:
            T = nx.maximum_spanning_arborescence(G, attr="weight")
        except nx.NetworkXException as e:
            logger.error("Pure MWA Edmonds failed: %s", e)
            return SkillResult.empty(self.name, f"Pure MWA failed: {e}")

        # Pure MWA parent assignment = Edmonds output, no overrides
        parents: dict[str, str] = {}
        for u, v in T.edges():
            parents[v] = u

        # ── NO Phase 3 cycle repair (Edmonds outputs acyclic by definition,
        # so no cycles to repair anyway) ──
        # ── NO Phase 4 phantom parent lift ──
        # ── NO Phase 5 degree balancing ──

        # Stats
        ch_count = Counter(parents.values())
        top = ch_count.most_common(1)
        max_ch = top[0][1] if top else 0
        logger.info(
            "PureMWAResolver: %d parents, max_children=%d(%s)",
            len(parents), max_ch, top[0][0] if top else "?",
        )

        return SkillResult(
            skill_name=self.name,
            parent_overrides=parents,
        )


def count_cycles(parents: dict[str, str]) -> int:
    """Count cycles in a parent map via DFS."""
    cycles = 0
    seen_in_cycle: set[str] = set()
    for start in parents:
        if start in seen_in_cycle:
            continue
        visited: set[str] = set()
        node = start
        while node and node in parents and node not in visited:
            visited.add(node)
            node = parents.get(node)
        if node and node in visited:
            # Found a cycle
            cycles += 1
            # Mark all cycle nodes as seen
            cur = node
            cycle_path: set[str] = set()
            while cur not in cycle_path:
                cycle_path.add(cur)
                cur = parents.get(cur, "")
            seen_in_cycle.update(cycle_path)
    return cycles


def is_valid_arborescence(parents: dict[str, str]) -> tuple[bool, str]:
    """Check if parent map is a valid arborescence.

    Returns (is_valid, reason). Valid = exactly one root + acyclic.
    """
    if not parents:
        return False, "empty"
    cycles = count_cycles(parents)
    if cycles > 0:
        return False, f"has_{cycles}_cycles"
    children_set = set(parents.keys())
    parents_set = set(parents.values())
    roots = parents_set - children_set
    if len(roots) > 1:
        return False, f"{len(roots)}_roots"
    if len(roots) == 0:
        return False, "no_root_all_have_parents"
    return True, "valid_arborescence"


async def run_pure_mwa(novel_id: str, title: str) -> dict:
    """Run Pure MWA on a single novel, return structural metrics."""
    store = SnapshotStore()

    # Clear old snapshots
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()

    # Pure MWA pipeline
    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("prior", KnowledgePrior(novel_title=title))
    orch.add_skill("pure_mwa", PureMWAResolver())
    async for _ in orch.run():
        pass
    snap = await store.load_latest(novel_id)
    metrics = HierarchyMetrics.compute(snap)
    cycles = count_cycles(snap.location_parents)
    valid, reason = is_valid_arborescence(snap.location_parents)

    return {
        "method": "Pure MWA",
        "title": title,
        "avg_depth": metrics.avg_depth,
        "max_depth": metrics.max_depth,
        "root_count": metrics.root_count,
        "max_children": metrics.max_children,
        "max_children_node": metrics.max_children_node,
        "total_locations": metrics.total_locations,
        "total_parents": metrics.total_parents,
        "cycles": cycles,
        "is_valid": valid,
        "validity_reason": reason,
    }


async def run_hybrid(novel_id: str, title: str) -> dict:
    """Run Hybrid (deployed) on a single novel for comparison."""
    store = SnapshotStore()

    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()

    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("prior", KnowledgePrior(novel_title=title))
    orch.add_skill("edmonds", EdmondsResolver())
    async for _ in orch.run():
        pass
    snap = await store.load_latest(novel_id)
    metrics = HierarchyMetrics.compute(snap)
    cycles = count_cycles(snap.location_parents)
    valid, reason = is_valid_arborescence(snap.location_parents)

    return {
        "method": "Hybrid (deployed)",
        "title": title,
        "avg_depth": metrics.avg_depth,
        "max_depth": metrics.max_depth,
        "root_count": metrics.root_count,
        "max_children": metrics.max_children,
        "max_children_node": metrics.max_children_node,
        "total_locations": metrics.total_locations,
        "total_parents": metrics.total_parents,
        "cycles": cycles,
        "is_valid": valid,
        "validity_reason": reason,
    }


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    novels = [
        ("3b2ef56c-1a55-466a-a7d1-34272446a198", "西游记"),
        ("c384901a-8b71-437a-af35-b5ec1c56c696", "红楼梦"),
        ("4ac43c73-f67b-427c-8d6d-e766a1423977", "水浒传"),
    ]

    all_results: list[dict] = []
    for novel_id, title in novels:
        print(f"\n=== {title} ===", flush=True)
        print("[1/2] Running Hybrid (deployed)...", flush=True)
        h = await run_hybrid(novel_id, title)
        print(
            f"  max_children={h['max_children']}({h['max_children_node']}) "
            f"roots={h['root_count']} cycles={h['cycles']} "
            f"valid={h['is_valid']}({h['validity_reason']})",
            flush=True,
        )
        all_results.append(h)

        print("[2/2] Running Pure MWA...", flush=True)
        p = await run_pure_mwa(novel_id, title)
        print(
            f"  max_children={p['max_children']}({p['max_children_node']}) "
            f"roots={p['root_count']} cycles={p['cycles']} "
            f"valid={p['is_valid']}({p['validity_reason']})",
            flush=True,
        )
        all_results.append(p)

    # Re-run Hybrid as final state (so we don't leave the DB in Pure MWA state)
    print("\n=== Restoring Hybrid as final DB state ===", flush=True)
    for novel_id, title in novels:
        print(f"  restoring {title}...", flush=True)
        await run_hybrid(novel_id, title)

    # Output Table 8 markdown for paper
    print("\n\n## Table 8 — Pure MWA vs Hybrid (paper §3.3)\n")
    print("| Novel | Method | max_ch | max_ch_node | roots | cycles | valid? | depth | n_locs |")
    print("|-------|--------|--------|-------------|-------|--------|--------|-------|--------|")
    for r in all_results:
        print(
            f"| {r['title']} | {r['method']} | {r['max_children']} | "
            f"{r['max_children_node']} | {r['root_count']} | {r['cycles']} | "
            f"{'✓' if r['is_valid'] else '✗'} ({r['validity_reason']}) | "
            f"{r['avg_depth']:.2f} | {r['total_locations']} |"
        )

    # JSON output for downstream consumption
    import json
    out_dir = "../../ai-reader-internal/paper/evaluation/v071/baselines"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "pure_mwa_ablation.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved JSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
