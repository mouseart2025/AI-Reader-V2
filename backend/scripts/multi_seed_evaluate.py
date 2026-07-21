"""P2 multi-seed evaluation: full-pipeline metrics + gold benchmark per run.

For each of the 6 multi-seed novels (xiyouji/honglou × temp 0.0/0.7/1.0,
DeepSeek V3 extraction):
  1. Run the full 5-skill chain (tier→votes→prior→edmonds→suffix) and record
     structural metrics (max_children, roots, cycles, depth).
  2. Run benchmark_hierarchy against the frozen gold (--novel-id + --novel key)
     and record Overall accuracy.
  3. Aggregate mean ± std across the 3 temperatures per novel.

Usage:
    cd backend && uv run python scripts/multi_seed_evaluate.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cross_llm_replication import run_chain  # noqa: E402
from src.services.geo_skills.edmonds_resolver import EdmondsResolver  # noqa: E402
from src.services.geo_skills.knowledge_prior import KnowledgePrior  # noqa: E402
from src.services.geo_skills.suffix_normalizer import SuffixNormalizer  # noqa: E402
from src.services.geo_skills.tier_classifier import TierClassifier  # noqa: E402
from src.services.geo_skills.vote_builder import VoteBuilder  # noqa: E402

RUNS = {
    "xiyouji": {
        "prior_title": "西游记",
        "temps": {
            "0.0": "629fa976-6ce2-418b-b74a-f043b884bb1d",
            "0.7": "afc1720f-dddc-4b54-9491-30d3ef491976",
            "1.0": "bc4984bb-ca99-4102-af00-1dfae5ad3462",
        },
    },
    "honglou": {
        "prior_title": "红楼梦",
        "temps": {
            "0.0": "3793ddb5-caf9-4698-b83c-5e2b9136a04e",
            "0.7": "b967ba16-bc8b-4f21-a1d8-d380b4f3d368",
            "1.0": "2bf66c78-92cb-45e4-af9e-32f0040ec1ad",
        },
    },
}

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_seed_results")


def run_benchmark(novel_key: str, novel_id: str) -> dict:
    out_path = os.path.join(OUT_DIR, f"benchmark_{novel_key}_{novel_id[:8]}.json")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    subprocess.run(
        [
            "uv", "run", "--project", "backend",
            "python", "-m", "backend.scripts.benchmark_hierarchy",
            "--novel-id", novel_id, "--novel", novel_key, "--out", out_path,
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


async def main():
    logging.basicConfig(level=logging.ERROR)
    os.makedirs(OUT_DIR, exist_ok=True)
    all_results: dict = {}

    for novel_key, cfg in RUNS.items():
        per_temp: dict = {}
        for temp, nid in cfg["temps"].items():
            print(f"=== {novel_key} temp={temp} ({nid[:8]}) ===")
            metrics = await run_chain(
                nid,
                [
                    ("tier", TierClassifier(nid)),
                    ("votes", VoteBuilder(nid)),
                    ("prior", KnowledgePrior(novel_title=cfg["prior_title"])),
                    ("edmonds", EdmondsResolver()),
                    ("suffix", SuffixNormalizer()),
                ],
            )
            bench = run_benchmark(novel_key, nid)
            per_temp[temp] = {"novel_id": nid, "metrics": metrics, "benchmark_file": f"benchmark_{novel_key}_{nid[:8]}.json"}
            print(json.dumps(metrics, ensure_ascii=False))

        # aggregate mean ± std over the 3 temps
        def agg(field):
            vals = [per_temp[t]["metrics"][field] for t in cfg["temps"]]
            return {"mean": statistics.mean(vals), "std": statistics.pstdev(vals), "vals": vals}

        all_results[novel_key] = {
            "per_temp": per_temp,
            "struct_agg": {f: agg(f) for f in ["max_children", "root_count", "cycles", "avg_depth"]},
        }

    out = os.path.join(OUT_DIR, "multi_seed_summary.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
