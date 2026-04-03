"""Ablation experiment for location hierarchy methods.

Compares 4 configurations on the same data:
1. Full (Edmonds + KnowledgePrior) — full system
2. Edmonds-only (no priors) — algorithm contribution
3. Voting (VoteResolver) — old method baseline
4. Raw LLM (direct chapter fact parents) — extraction quality baseline

Usage:
    cd backend && uv run python scripts/ablation_hierarchy.py

Output: markdown table for paper Section 4.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_ablation(novel_id: str, title: str):
    from src.services.geo_skills.orchestrator import GeoOrchestrator
    from src.services.geo_skills.vote_builder import VoteBuilder
    from src.services.geo_skills.edmonds_resolver import EdmondsResolver
    from src.services.geo_skills.vote_resolver import VoteResolver
    from src.services.geo_skills.tier_classifier import TierClassifier
    from src.services.geo_skills.knowledge_prior import KnowledgePrior
    from src.services.geo_skills.snapshot import HierarchyMetrics
    from src.services.geo_skills.snapshot_store import (
        SnapshotStore, snapshot_from_world_structure,
    )
    from src.db.sqlite_db import get_connection

    store = SnapshotStore()

    # Clear old snapshots
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    except Exception:
        pass
    finally:
        await conn.close()

    # Baseline: raw LLM (chapter fact parents as-is)
    snap0 = await snapshot_from_world_structure(novel_id)
    m_raw = HierarchyMetrics.compute(snap0)

    # Config 1: Edmonds + Prior (full)
    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("prior", KnowledgePrior(novel_title=title))
    orch.add_skill("edmonds", EdmondsResolver())
    async for _ in orch.run():
        pass
    snap_full = await store.load_latest(novel_id)
    m_full = HierarchyMetrics.compute(snap_full)

    # Config 2: Edmonds only (no priors)
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    except Exception:
        pass
    finally:
        await conn.close()

    orch2 = GeoOrchestrator(novel_id)
    orch2.add_skill("tier", TierClassifier(novel_id))
    orch2.add_skill("votes", VoteBuilder(novel_id))
    orch2.add_skill("edmonds", EdmondsResolver())
    async for _ in orch2.run():
        pass
    snap_edmonds = await store.load_latest(novel_id)
    m_edmonds = HierarchyMetrics.compute(snap_edmonds)

    # Config 3: Voting method (old)
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    except Exception:
        pass
    finally:
        await conn.close()

    orch3 = GeoOrchestrator(novel_id)
    orch3.add_skill("tier", TierClassifier(novel_id))
    orch3.add_skill("votes", VoteBuilder(novel_id))
    orch3.add_skill("voting", VoteResolver())
    async for _ in orch3.run():
        pass
    snap_voting = await store.load_latest(novel_id)
    m_voting = HierarchyMetrics.compute(snap_voting)

    return {
        "title": title,
        "raw": m_raw,
        "voting": m_voting,
        "edmonds": m_edmonds,
        "full": m_full,
    }


async def main():
    novels = [
        ("3b2ef56c-1a55-466a-a7d1-34272446a198", "西游记"),
        ("c384901a-8b71-437a-af35-b5ec1c56c696", "红楼梦"),
        ("4ac43c73-f67b-427c-8d6d-e766a1423977", "水浒传"),
    ]

    results = []
    for novel_id, title in novels:
        print(f"Running ablation for {title}...", flush=True)
        r = await run_ablation(novel_id, title)
        results.append(r)

    # Output markdown table
    print("\n\n## Ablation Results — Location Hierarchy\n")
    print("| Novel | Method | avg_depth | max_children | roots |")
    print("|-------|--------|-----------|-------------|-------|")
    for r in results:
        for method, key in [
            ("Raw LLM", "raw"),
            ("Voting", "voting"),
            ("Edmonds", "edmonds"),
            ("Edmonds+Prior", "full"),
        ]:
            m = r[key]
            bold = "**" if key == "full" else ""
            print(
                f"| {r['title']} | {bold}{method}{bold} | "
                f"{bold}{m.avg_depth:.2f}{bold} | "
                f"{bold}{m.max_children}{bold} | "
                f"{bold}{m.root_count}{bold} |"
            )

    # Summary
    print("\n### Summary (avg across 3 novels)\n")
    print("| Method | avg_depth | max_children |")
    print("|--------|-----------|-------------|")
    for method, key in [
        ("Raw LLM", "raw"),
        ("Voting", "voting"),
        ("Edmonds", "edmonds"),
        ("**Edmonds+Prior**", "full"),
    ]:
        avg_d = sum(r[key].avg_depth for r in results) / len(results)
        avg_ch = sum(r[key].max_children for r in results) / len(results)
        print(f"| {method} | {avg_d:.2f} | {avg_ch:.0f} |")


if __name__ == "__main__":
    asyncio.run(main())
