"""Path 2 validation: 对任意小说重跑 GeoOrchestrator v2 skill pipeline.

使用 Phase 2a 增强的 TierClassifier + EdmondsResolver phantom lift,
仅重跑层级skill, 不调用LLM (几秒完成).

用法:
    # Dry-run: 显示会变化的tier数/phantom节点
    uv run python scripts/rebuild_with_new_skills.py --novel-key=xiyouji

    # 实际应用: 更新DB中的world_structure
    uv run python scripts/rebuild_with_new_skills.py --novel-key=xiyouji --apply

    # 批量所有经典小说
    uv run python scripts/rebuild_with_new_skills.py --all --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.services.geo_skills.tier_classifier import TierClassifier, _detect_era  # noqa: E402
from src.services.geo_skills.edmonds_resolver import EdmondsResolver  # noqa: E402
from src.services.geo_skills.snapshot import HierarchySnapshot  # noqa: E402

DB_PATH = Path.home() / ".ai-reader-v2" / "data.db"
NOVELS = {
    "xiyouji": "3b2ef56c-1a55-466a-a7d1-34272446a198",
    "honglou": "c384901a-8b71-437a-af35-b5ec1c56c696",
    "shuihu": "4ac43c73-f67b-427c-8d6d-e766a1423977",
    "sanguo": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
    "fengshen": "53013970-effd-4f50-aef7-728ca13de69a",
}


def load_snapshot_from_db(novel_id: str) -> tuple[HierarchySnapshot, dict]:
    conn = sqlite3.connect(DB_PATH)
    ws = json.loads(
        conn.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
        ).fetchone()[0]
    )
    freq: Counter = Counter()
    loc_chapters: dict[str, list[int]] = {}
    chapter_settings: dict[int, str] = {}
    for ch_id, fj in conn.execute(
        "SELECT chapter_id, fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id", (novel_id,)
    ):
        d = json.loads(fj)
        for loc in (d.get("locations") or []):
            name = loc.get("name")
            if not name:
                continue
            freq[name] += 1
            loc_chapters.setdefault(name, []).append(ch_id)
    conn.close()

    snapshot = HierarchySnapshot(
        location_parents=dict(ws.get("location_parents", {})),
        location_tiers=dict(ws.get("location_tiers", {})),
        parent_votes={},  # rebuild doesn't need votes for TierClassifier alone
        location_frequencies=freq,
        chapter_settings=chapter_settings,
        location_chapters=loc_chapters,
        novel_genre_hint=ws.get("novel_genre_hint", ""),
    )
    return snapshot, ws


async def run_refinement(novel_key: str, novel_id: str, apply: bool):
    print(f"\n[{novel_key}] loading snapshot...")
    snapshot, ws = load_snapshot_from_db(novel_id)
    print(f"  nodes={len(snapshot.location_tiers)}, mentions={sum(snapshot.location_frequencies.values())}")

    # Only apply Phase 2 refinement (skip Phase 1 suffix re-classification
    # which would override LLM-optimized tiers and cause regressions).
    children_count: dict[str, int] = {}
    for _, p in snapshot.location_parents.items():
        if p:
            children_count[p] = children_count.get(p, 0) + 1

    genre = snapshot.novel_genre_hint or ""
    era = _detect_era(genre, set(snapshot.location_tiers.keys()))
    print(f"  genre={genre}, era={era}")

    tier_changes = TierClassifier._multi_feature_refine(
        tiers=dict(snapshot.location_tiers),
        parents=snapshot.location_parents,
        frequencies=snapshot.location_frequencies,
        children_count=children_count,
        era=era,
    )
    print(f"  TierClassifier refinement: {len(tier_changes)} tier changes")

    # Run phantom lift directly on current parents
    phantoms_lifted = 0
    if snapshot.location_parents:
        new_parents, phantoms_lifted = EdmondsResolver._lift_phantom_parent_children(
            snapshot.location_parents, snapshot.location_frequencies,
            uber_root="天下",
        )
        print(f"  Phantom lift: {phantoms_lifted} children lifted")

    if not tier_changes and not phantoms_lifted:
        print("  [no changes]")
        return 0, 0

    if not apply:
        # Show top 5 tier changes
        print(f"  Sample tier changes:")
        for i, (name, new_tier) in enumerate(list(tier_changes.items())[:5]):
            old = snapshot.location_tiers[name]
            mc = snapshot.location_frequencies.get(name, 0)
            print(f"    {name}: {old}→{new_tier} (mc={mc})")
        return len(tier_changes), phantoms_lifted

    # Apply changes to DB
    ws["location_tiers"].update(tier_changes)
    ws["location_parents"] = new_parents
    ws["_phase2a_rebuild"] = {
        "applied_at": datetime.now().isoformat(),
        "tier_changes": len(tier_changes),
        "phantoms_lifted": phantoms_lifted,
    }
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE world_structures SET structure_json=?, updated_at=datetime('now') WHERE novel_id=?",
        (json.dumps(ws, ensure_ascii=False), novel_id),
    )
    # Clear layer layouts cache so map regenerates
    conn.execute("DELETE FROM layer_layouts WHERE novel_id=?", (novel_id,))
    conn.commit()
    conn.close()
    print(f"  [applied] map cache cleared")
    return len(tier_changes), phantoms_lifted


async def amain():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel-key")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    targets = list(NOVELS.items()) if args.all else [(args.novel_key, NOVELS[args.novel_key])]

    total_tier = 0
    total_phantom = 0
    for key, nid in targets:
        t, p = await run_refinement(key, nid, args.apply)
        total_tier += t
        total_phantom += p

    print(f"\n=== SUMMARY ===")
    print(f"  Total tier changes: {total_tier}")
    print(f"  Total phantoms lifted: {total_phantom}")
    if args.apply:
        print(f"\n[next] Run benchmark: uv run python scripts/benchmark_hierarchy.py --novel=xiyouji")
    return 0


def main():
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
