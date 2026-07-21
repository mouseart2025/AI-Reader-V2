"""Bootstrap CI for the 4,941-node gold benchmark Overall (rebuttal C2#2).

Reconstructs per-node binary outcomes (error vs not) with the exact logic of
hierarchy_validator.compute_metrics_from_gold, pools all gold nodes across the
5 novels, and runs a stratified bootstrap (resample within novel, 10k reps)
to produce a 95% percentile CI for the pooled Overall.

Data source: current local DB snapshots (full-pipeline state) + frozen gold.
Point estimates may differ slightly from the frozen v071 table (code drift);
this script reports both.

Usage:
    cd backend && uv run python scripts/bootstrap_ci.py
"""

from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark_hierarchy import load_snapshot_from_db, default_db_path  # noqa: E402
from src.services.hierarchy_validator import (  # noqa: E402
    is_error_resolved,
    load_gold,
    parse_errata_correction,
)

NOVELS = {
    "xiyouji": "3b2ef56c-1a55-466a-a7d1-34272446a198",
    "honglou": "c384901a-8b71-437a-af35-b5ec1c56c696",
    "shuihu": "4ac43c73-f67b-427c-8d6d-e766a1423977",
    "sanguo": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
    "fengshen": "53013970-effd-4f50-aef7-728ca13de69a",
}

REPS = 10_000
FROZEN_DIR = (
    Path.home()
    / "Baiduyun/AISoul/ai-reader-internal/paper/evaluation/v071"
)


def per_node_error_flags(novel_key: str, novel_id: str, db: Path) -> list[int]:
    """1 = node counts as error toward Overall, 0 = not. Mirrors
    compute_metrics_from_gold's per-node logic exactly."""
    lp, lt, mentions, _title, _genre = load_snapshot_from_db(novel_id, db)
    current_nodes = (set(lp.keys()) | set(lp.values()) | set(lt.keys())) - {"", None}
    gold, gold_raw = load_gold(novel_key)

    children_count: dict[str, int] = {}
    for c, p in lp.items():
        if p:
            children_count[p] = children_count.get(p, 0) + 1

    flags: list[int] = []
    for name, v in gold.items():
        if v.verdict != "error":
            flags.append(0)
            continue
        raw_reasons = ""
        if gold_raw and name in gold_raw.get("nodes", {}):
            raw_reasons = gold_raw["nodes"][name].get("reasons", "") or ""
        corrections = parse_errata_correction(raw_reasons)
        unresolved = [
            etype
            for etype in v.error_types
            if not is_error_resolved(
                etype, name, corrections, lt, lp, current_nodes, children_count
            )
        ]
        flags.append(1 if unresolved else 0)
    return flags


def main():
    random.seed(20260721)
    db = default_db_path()

    per_novel: dict[str, list[int]] = {}
    frozen_overall: dict[str, float] = {}
    for key, nid in NOVELS.items():
        per_novel[key] = per_node_error_flags(key, nid, db)
        n = len(per_novel[key])
        err = sum(per_novel[key])
        pt = 1.0 - err / n
        fz = FROZEN_DIR / f"{key}-benchmark.json"
        fz_overall = json.load(open(fz))["gold_based"]["overall"] if fz.exists() else None
        frozen_overall[key] = fz_overall
        print(
            f"{key:9s} nodes={n:5d} errors={err:3d} "
            f"overall={pt:.4f} (frozen: {fz_overall:.4f})" if fz_overall else
            f"{key:9s} nodes={n:5d} errors={err:3d} overall={pt:.4f}"
        )

    total_nodes = sum(len(v) for v in per_novel.values())
    point = 1.0 - sum(sum(v) for v in per_novel.values()) / total_nodes
    print(f"\npooled: {total_nodes} nodes, point Overall = {point:.4f}")

    # Stratified bootstrap: resample within each novel, pool, recompute.
    estimates: list[float] = []
    for _ in range(REPS):
        err = 0
        for flags in per_novel.values():
            err += sum(random.choices(flags, k=len(flags)))
        estimates.append(1.0 - err / total_nodes)
    estimates.sort()
    lo = estimates[int(0.025 * REPS)]
    hi = estimates[int(0.975 * REPS)]
    mean = sum(estimates) / REPS
    std = (sum((e - mean) ** 2 for e in estimates) / REPS) ** 0.5
    print(f"bootstrap {REPS}x: mean={mean:.4f} std={std:.5f} 95% CI=[{lo:.4f}, {hi:.4f}]")

    out = {
        "reps": REPS,
        "total_nodes": total_nodes,
        "point_overall": point,
        "bootstrap_mean": mean,
        "bootstrap_std": std,
        "ci95": [lo, hi],
        "per_novel": {
            k: {
                "nodes": len(v),
                "errors": sum(v),
                "overall": 1.0 - sum(v) / len(v),
                "frozen_overall": frozen_overall[k],
            }
            for k, v in per_novel.items()
        },
        "data_source": "current local DB full-pipeline snapshots + frozen v071 gold",
        "date": "2026-07-21",
    }
    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "bootstrap_ci_result.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
