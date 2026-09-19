"""逐边 diff 诊断：重建指定小说的 location_parents，对照 golden fixture 列出不一致。

Usage:
    cd backend && .venv/bin/python scripts/evolve/diff_golden.py shuihu
    .venv/bin/python scripts/evolve/diff_golden.py shuihu --pred-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from compute_weight_metrics import FIXTURES, NOVELS, rebuild_parents, setup_scratch

import os

from src.utils.topology_metrics import compute_topology_metrics


async def amain(slug: str) -> None:
    info = NOVELS[slug]
    parents = await rebuild_parents(info["id"], info["title"], skip_prior=False)
    golden = json.loads((FIXTURES / info["fixture"]).read_text(encoding="utf-8"))
    golden_parents = {
        loc["name"]: loc["correct_parent"]
        for loc in golden["locations"]
        if loc.get("name") and loc.get("correct_parent") and loc.get("tier") != "DELETE"
    }
    print(f"# {slug}: gold 边 {len(golden_parents)}, pred 边 {len(parents)}")
    topo = compute_topology_metrics(parents, golden["locations"])
    print(f"# metrics: {topo}\n")

    wrong, missing = [], []
    for child, gold_p in sorted(golden_parents.items()):
        pred_p = parents.get(child)
        if pred_p is None and child not in parents:
            missing.append((child, gold_p))
        elif pred_p != gold_p:
            wrong.append((child, gold_p, pred_p))

    print(f"## 父错误 ({len(wrong)}):")
    for child, gold_p, pred_p in wrong:
        print(f"  {child}: gold={gold_p} pred={pred_p}")
    print(f"\n## 预测缺失 ({len(missing)}):")
    for child, gold_p in missing:
        print(f"  {child}: gold={gold_p}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", choices=[k for k, v in NOVELS.items() if v["fixture"]])
    args = ap.parse_args()
    setup_scratch(None)
    os.environ["AI_READER_DATA_DIR"] = "/tmp/evolve-s2-data"
    asyncio.run(amain(args.slug))
    return 0


if __name__ == "__main__":
    sys.exit(main())
