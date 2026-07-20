"""Cross-LLM replication: structural metrics on DeepSeek-extracted 西游记.

P1 experiment (rebuttal arsenal C2): does greedy-voting structural collapse
(max_children explosion) reproduce when extraction is done by a different LLM
(DeepSeek V3 / deepseek-chat) instead of Claude?

Runs on the dedicated replication novel (DeepSeek-extracted copy of 西游记,
100 chapters, apples-to-apples with the frozen Claude extraction):

  (a) tier -> votes only        => voting-stage metrics
       Claude reference: max_children=279, roots=25, cycles=2
  (b) tier -> votes -> prior -> edmonds -> suffix  (full pipeline)
       Claude reference: max_children=63,  roots=1,  cycles=0

Usage:
    cd backend && uv run python scripts/cross_llm_replication.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

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

NOVEL_ID = "530de1f0-b160-423f-a082-b3fe86d14166"
# Prior matching is by novel title; use the original name to reuse the
# 399-entry 西游记 prior table.
PRIOR_TITLE = "西游记"

CLAUDE_REFERENCE = {
    "voting": {"max_children": 279, "root_count": 25, "cycles": 2},
    "full": {"max_children": 63, "root_count": 1, "cycles": 0},
}


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


async def run_chain(novel_id: str, skills: list[tuple[str, object]]) -> dict:
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
    for name, skill in skills:
        orch.add_skill(name, skill)
    async for _ in orch.run():
        pass
    snap = await store.load_latest(novel_id)
    metrics = HierarchyMetrics.compute(snap)
    return {
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

    voting = await run_chain(
        NOVEL_ID,
        [
            ("tier", TierClassifier(NOVEL_ID)),
            ("votes", VoteBuilder(NOVEL_ID)),
        ],
    )
    print("=== (a) tier -> votes (DeepSeek extraction) ===")
    print(json.dumps(voting, ensure_ascii=False, indent=2))
    print("Claude reference:", CLAUDE_REFERENCE["voting"])

    full = await run_chain(
        NOVEL_ID,
        [
            ("tier", TierClassifier(NOVEL_ID)),
            ("votes", VoteBuilder(NOVEL_ID)),
            ("prior", KnowledgePrior(novel_title=PRIOR_TITLE)),
            ("edmonds", EdmondsResolver()),
            ("suffix", SuffixNormalizer()),
        ],
    )
    print("\n=== (b) full pipeline (DeepSeek extraction) ===")
    print(json.dumps(full, ensure_ascii=False, indent=2))
    print("Claude reference:", CLAUDE_REFERENCE["full"])

    out = {
        "novel_id": NOVEL_ID,
        "extractor": "deepseek-chat (DeepSeek V3)",
        "chapters": 100,
        "voting": voting,
        "full": full,
        "claude_reference": CLAUDE_REFERENCE,
    }
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "cross_llm_replication_result.json",
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
