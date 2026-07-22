"""M5 accuracy evaluation — fair-intersection Overall + orphan-subset parent match.

Companion to m5_no_edmonds_ablation.py (structural metrics). Answers:
  "If Edmonds' marginal contribution is not fan-out reduction, is it
   placement QUALITY of the ~30% no-vote orphan nodes?"

Method (mirrors paper/evaluation/v071/ablation-voting-baseline-fair.json):
  - Gold: backend/data/hierarchy_validation/<novel>_errata_gold.json
    (frozen 4,941-node errata annotations), scored via the SAME function
    used for the frozen benchmarks:
    src.services.hierarchy_validator.compute_metrics_from_gold.
  - Fair intersection: gold nodes restricted to (M5 nodes ∩ full-pipeline
    nodes); both pipelines scored on the identical subset with their own
    tiers/parents. (The frozen fair JSON used voting∩full; intersection
    differs, so frozen fair numbers are context, not direct anchors.)
  - Full-pipeline state = frozen world_structures row in the scratch DB
    copy (authoritative post-pipeline state, what benchmark_hierarchy.py
    scores). Naive full-pipeline Overall is validated against the frozen
    ablation-voting-baseline.json "full_pipeline" numbers before use.
  - Orphan subset: nodes with no VoteResolver assignment (M5 attached them
    to uber_root). For orphans present in gold, expected parent =
    errata parent correction (if any) else the gold-accepted parent field;
    compare M5's final parent vs the full pipeline's parent.

Frozen-data safety: same scratch-DB isolation as m5_no_edmonds_ablation.py
(imports it, which copies the frozen DB to /tmp/m5-data and points
AI_READER_DATA_DIR there). The real DB is never opened by this script.

Usage:
    cd backend && uv run python scripts/m5_accuracy_eval.py

Output:
    Appends an "accuracy" field to
    ../../ai-reader-internal/paper/evaluation/v071/baselines/m5-no-edmonds.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

_BACKEND_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(_BACKEND_DIR))

# Importing the structural script performs scratch-DB isolation (copies the
# frozen DB to the scratch dir and sets AI_READER_DATA_DIR) and provides the
# resolver classes. MUST be imported before any src.* module.
from scripts import m5_no_edmonds_ablation as m5mod  # noqa: E402

from src.db.sqlite_db import get_connection  # noqa: E402
from src.services.geo_skills.orchestrator import GeoOrchestrator  # noqa: E402
from src.services.geo_skills.snapshot_store import SnapshotStore  # noqa: E402
from src.services.geo_skills.tier_classifier import TierClassifier  # noqa: E402
from src.services.geo_skills.vote_builder import VoteBuilder  # noqa: E402
from src.services.hierarchy_validator import (  # noqa: E402
    compute_metrics_from_gold,
    load_gold,
    parse_errata_correction,
)

# Frozen naive full-pipeline Overall (paper/evaluation/v071/
# ablation-voting-baseline.json "full_pipeline") — reproduction gate.
FROZEN_NAIVE_FULL_OVERALL = {
    "xiyouji": 0.9809128630705394,
    "honglou": 0.972,
    "shuihu": 0.8900939985538684,
    "sanguo": 0.8767857142857143,
    "fengshen": 0.9484978540772532,
}

# Frozen fair-intersection (voting∩full) Overall, for context only.
FROZEN_FAIR = {
    "xiyouji": {"voting": 0.9671772428884027, "full": 0.975929978118162},
    "honglou": {"voting": 0.9735449735449735, "full": 0.9735449735449735},
    "shuihu": {"voting": 0.8282290279627164, "full": 0.8348868175765646},
    "sanguo": {"voting": 0.8571428571428572, "full": 0.8571428571428572},
    "fengshen": {"voting": 0.930379746835443, "full": 0.9367088607594937},
}


def _nodes_of(parents: dict, tiers: dict) -> set[str]:
    return (set(parents) | set(parents.values()) | set(tiers)) - {"", None}


def _load_full_state(novel_id: str) -> tuple[dict, dict]:
    """Frozen full-pipeline state = world_structures row in the scratch DB."""
    conn = sqlite3.connect(str(m5mod._SCRATCH_DB))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?",
            (novel_id,),
        ).fetchone()
    finally:
        conn.close()
    ws = json.loads(row["structure_json"])
    return ws.get("location_parents", {}), ws.get("location_tiers", {})


def _filter_gold_raw(gold_raw: dict, names: set[str]) -> dict:
    return {"nodes": {n: gold_raw["nodes"][n] for n in names if n in gold_raw.get("nodes", {})}}


async def run_one(novel_id: str, key: str, title: str) -> dict:
    store = SnapshotStore()

    # Fresh snapshot chain in the SCRATCH db (real DB untouched)
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()

    voting = m5mod.CapturingVoteResolver()
    m5 = m5mod.M5NoEdmondsResolver(voting)
    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("voting", voting)
    orch.add_skill("m5", m5)
    async for _ in orch.run():
        pass

    snap_m5 = await store.load_version(novel_id, 4)
    m5_parents = snap_m5.location_parents
    m5_tiers = snap_m5.location_tiers
    voting_parents = voting.captured

    full_parents, full_tiers = _load_full_state(novel_id)

    gold, gold_raw = load_gold(key)

    # ── Validation gate: naive full-pipeline Overall must reproduce frozen ──
    full_nodes = _nodes_of(full_parents, full_tiers)
    naive_full, _ = compute_metrics_from_gold(
        key, full_nodes, gold,
        current_tiers=full_tiers, current_parents=full_parents,
        gold_raw=gold_raw,
    )
    frozen_naive = FROZEN_NAIVE_FULL_OVERALL[key]
    naive_ok = abs(naive_full.overall - frozen_naive) < 5e-4

    # ── Naive M5 (full gold set, context) ──
    m5_nodes = _nodes_of(m5_parents, m5_tiers)
    naive_m5, _ = compute_metrics_from_gold(
        key, m5_nodes, gold,
        current_tiers=m5_tiers, current_parents=m5_parents,
        gold_raw=gold_raw,
    )

    # ── Fair intersection: gold ⊆ (M5 nodes ∩ full nodes) ──
    intersection = m5_nodes & full_nodes
    gold_sub_names = {n for n in gold if n in intersection}
    gold_sub = {n: gold[n] for n in gold_sub_names}
    gold_raw_sub = _filter_gold_raw(gold_raw, gold_sub_names)

    def _score(nodes, tiers, parents):
        m, _ = compute_metrics_from_gold(
            key, nodes, gold_sub,
            current_tiers=tiers, current_parents=parents,
            gold_raw=gold_raw_sub,
        )
        return {
            "overall": m.overall,
            "entity_precision": m.entity_precision,
            "name_accuracy": m.name_accuracy,
            "tier_accuracy": m.tier_accuracy,
            "parent_precision": m.parent_precision,
            "structural_health": m.structural_health,
            "error_count": m.error_count,
            "total_nodes": m.total_nodes,
        }

    fair_m5 = _score(m5_nodes, m5_tiers, m5_parents)
    fair_full = _score(full_nodes, full_tiers, full_parents)
    voting_nodes = _nodes_of(voting_parents, m5_tiers)
    fair_voting = _score(voting_nodes, m5_tiers, voting_parents)

    # ── Orphan subset: M5 uber_root attachment vs Edmonds placement ──
    uber_root = m5.uber_root
    orphan_rows = []
    for o in m5.orphans:
        node = gold_raw.get("nodes", {}).get(o)
        if node is None:
            continue
        corr = parse_errata_correction(node.get("reasons", "") or "")
        expected = corr.get("parent_to")
        has_d_error = any(
            t.startswith("D-") for t in node.get("error_types", [])
        )
        if expected is None:
            if has_d_error and node.get("verdict") == "错误":
                continue  # wrong parent recorded, no parseable correction
            expected = node.get("parent") or None
        if not expected:
            continue
        m5_par = m5_parents.get(o)
        full_par = full_parents.get(o)
        orphan_rows.append({
            "node": o,
            "expected": expected,
            "m5_parent": m5_par,
            "full_parent": full_par,
            "m5_match": m5_par == expected,
            "full_match": (o in full_parents) and full_par == expected,
            "full_absent": o not in full_parents,
            "m5_moved_off_uber_root": m5_par not in (None, uber_root),
        })

    n_or = len(orphan_rows)
    n_absent = sum(1 for r in orphan_rows if r["full_absent"])
    orphan_stats = {
        "orphans_total": len(m5.orphans),
        "orphans_in_gold_scorable": n_or,
        "m5_parent_match": sum(1 for r in orphan_rows if r["m5_match"]),
        "full_parent_match": sum(1 for r in orphan_rows if r["full_match"]),
        "full_absent_from_output": n_absent,
        "m5_match_rate": round(
            sum(1 for r in orphan_rows if r["m5_match"]) / n_or, 4
        ) if n_or else None,
        "full_match_rate": round(
            sum(1 for r in orphan_rows if r["full_match"]) / n_or, 4
        ) if n_or else None,
        "m5_orphans_moved_off_uber_root": sum(
            1 for r in orphan_rows if r["m5_moved_off_uber_root"]
        ),
        "sample": orphan_rows[:15],
    }

    return {
        "novel": key,
        "title": title,
        "validation_naive_full": {
            "recomputed_overall": naive_full.overall,
            "frozen_overall": frozen_naive,
            "match": naive_ok,
        },
        "naive_m5_overall": naive_m5.overall,
        "fair_intersection": {
            "nodes": {
                "m5": len(m5_nodes),
                "full": len(full_nodes),
                "intersection": len(intersection),
                "gold_subset": len(gold_sub),
            },
            "m5": fair_m5,
            "full": fair_full,
            "voting": fair_voting,
            "frozen_fair_voting_intersection_for_context": FROZEN_FAIR[key],
        },
        "orphan_subset": orphan_stats,
    }


async def main() -> None:
    results = []
    for novel_id, key, title in m5mod.NOVELS:
        print(f"[m5-acc] {title} ...", flush=True)
        r = await run_one(novel_id, key, title)
        v = r["validation_naive_full"]
        print(
            f"  naive full validation: {v['recomputed_overall']:.6f} vs frozen "
            f"{v['frozen_overall']:.6f} → {'OK' if v['match'] else 'MISMATCH'}"
        )
        f = r["fair_intersection"]
        print(
            f"  fair (gold∩M5∩full, n={f['nodes']['gold_subset']}): "
            f"voting={f['voting']['overall']:.4f} m5={f['m5']['overall']:.4f} "
            f"full={f['full']['overall']:.4f} | parentP m5={f['m5']['parent_precision']:.4f} "
            f"full={f['full']['parent_precision']:.4f}"
        )
        o = r["orphan_subset"]
        print(
            f"  orphans: {o['orphans_total']} total, {o['orphans_in_gold_scorable']} gold-scorable; "
            f"parent match m5={o['m5_match_rate']} full={o['full_match_rate']} "
            f"(full absent={o['full_absent_from_output']})"
        )
        results.append(r)

    n = len(results)

    def _avg(getter):
        vals = [getter(r) for r in results]
        return round(sum(vals) / n, 4)

    averages = {
        "fair_overall": {
            "voting": _avg(lambda r: r["fair_intersection"]["voting"]["overall"]),
            "m5": _avg(lambda r: r["fair_intersection"]["m5"]["overall"]),
            "full": _avg(lambda r: r["fair_intersection"]["full"]["overall"]),
        },
        "fair_parent_precision": {
            "voting": _avg(lambda r: r["fair_intersection"]["voting"]["parent_precision"]),
            "m5": _avg(lambda r: r["fair_intersection"]["m5"]["parent_precision"]),
            "full": _avg(lambda r: r["fair_intersection"]["full"]["parent_precision"]),
        },
        "orphan_subset_pooled": _pooled_orphans(results),
    }

    accuracy = {
        "_method": (
            "Fair-intersection accuracy of M5 (voting+post-processing, no "
            "Edmonds, no priors) vs full pipeline. Gold = frozen errata "
            "annotations scored via hierarchy_validator.compute_metrics_from_gold "
            "(same function as frozen benchmarks). Intersection = M5 nodes ∩ "
            "full-pipeline nodes; both scored on the identical gold subset. "
            "Full-pipeline state = frozen world_structures (naive Overall "
            "reproduction against ablation-voting-baseline.json validated "
            "per novel, see validation_naive_full). Orphan subset = nodes "
            "with no VoteResolver assignment (M5 attached them to uber_root); "
            "expected parent from errata correction or gold-accepted parent. "
            "Computed by backend/scripts/m5_accuracy_eval.py on a scratch DB "
            "copy; frozen data untouched."
        ),
        "per_novel": results,
        "five_novel_average": averages,
    }

    out_path = m5mod.OUT_PATH
    data = json.loads(out_path.read_text(encoding="utf-8"))
    data["accuracy"] = accuracy
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[m5-acc] accuracy appended to: {out_path}")


def _pooled_orphans(results: list[dict]) -> dict:
    total = sum(r["orphan_subset"]["orphans_in_gold_scorable"] for r in results)
    m5_hit = sum(r["orphan_subset"]["m5_parent_match"] for r in results)
    full_hit = sum(r["orphan_subset"]["full_parent_match"] for r in results)
    absent = sum(r["orphan_subset"]["full_absent_from_output"] for r in results)
    return {
        "orphans_gold_scorable": total,
        "m5_parent_match": m5_hit,
        "full_parent_match": full_hit,
        "full_absent_from_output": absent,
        "m5_match_rate": round(m5_hit / total, 4) if total else None,
        "full_match_rate": round(full_hit / total, 4) if total else None,
    }


if __name__ == "__main__":
    asyncio.run(main())
