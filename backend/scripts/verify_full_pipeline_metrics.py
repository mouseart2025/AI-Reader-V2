"""Verify Full pipeline (incl. SuffixNormalizer) structural metrics.

One-off verification for paper number consistency (63 vs 67 question).
Runs the production 5-skill chain: tier -> votes -> prior -> edmonds -> suffix
on the 3 pure-MWA-ablation novels and reports structural metrics.

Usage:
    cd backend && uv run python scripts/verify_full_pipeline_metrics.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.geo_skills.edmonds_resolver import EdmondsResolver
from src.services.geo_skills.knowledge_prior import KnowledgePrior
from src.services.geo_skills.orchestrator import GeoOrchestrator
from src.services.geo_skills.snapshot import HierarchyMetrics
from src.services.geo_skills.snapshot_store import SnapshotStore
from src.services.geo_skills.suffix_normalizer import SuffixNormalizer
from src.services.geo_skills.tier_classifier import TierClassifier
from src.services.geo_skills.vote_builder import VoteBuilder
from src.db.sqlite_db import get_connection


def count_cycles(parents: dict[str, str]) -> int:
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
            cycles += 1
            cur = node
            cycle_path: set[str] = set()
            while cur not in cycle_path:
                cycle_path.add(cur)
                cur = parents.get(cur, "")
            seen_in_cycle.update(cycle_path)
    return cycles


async def run_full(novel_id: str, title: str) -> dict:
    store = SnapshotStore()
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,)
        )
        await conn.commit()
    finally:
        await conn.close()

    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("prior", KnowledgePrior(novel_title=title))
    orch.add_skill("edmonds", EdmondsResolver())
    orch.add_skill("suffix", SuffixNormalizer())
    async for _ in orch.run():
        pass
    snap = await store.load_latest(novel_id)
    metrics = HierarchyMetrics.compute(snap)
    return {
        "title": title,
        "avg_depth": metrics.avg_depth,
        "max_depth": metrics.max_depth,
        "root_count": metrics.root_count,
        "max_children": metrics.max_children,
        "max_children_node": metrics.max_children_node,
        "total_locations": metrics.total_locations,
        "total_parents": metrics.total_parents,
        "cycles": count_cycles(snap.location_parents),
    }


async def main():
    logging.basicConfig(level=logging.WARNING)
    novels = [
        ("3b2ef56c-1a55-466a-a7d1-34272446a198", "西游记"),
        ("c384901a-8b71-437a-af35-b5ec1c56c696", "红楼梦"),
        ("4ac43c73-f67b-427c-8d6d-e766a1423977", "水浒传"),
        ("b1287ef6-c215-4bd2-842c-cb04aec5eb70", "三国演义"),
        ("53013970-effd-4f50-aef7-728ca13de69a", "封神演义"),
    ]
    rows = []
    for novel_id, title in novels:
        rows.append(await run_full(novel_id, title))
    print("| Novel | max_ch | max_ch_node | roots | cycles | depth | n_locs | n_parents |")
    print("|-------|--------|-------------|-------|--------|-------|--------|-----------|")
    for r in rows:
        print(
            f"| {r['title']} | {r['max_children']} | {r['max_children_node']} | "
            f"{r['root_count']} | {r['cycles']} | {r['avg_depth']:.2f} | "
            f"{r['total_locations']} | {r['total_parents']} |"
        )
    avg_max_ch = sum(r["max_children"] for r in rows) / len(rows)
    avg_depth = sum(r["avg_depth"] for r in rows) / len(rows)
    print(f"\n5-novel avg: max_ch={avg_max_ch:.1f} depth={avg_depth:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
