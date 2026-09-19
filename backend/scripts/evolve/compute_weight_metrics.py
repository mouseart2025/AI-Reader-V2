"""GeoEvolve 阶段 2 —— 每代 EVAL 子进程：注入参数重建层级并测量拓扑指标。

流程：
  1. 把真实 DB (~/.ai-reader-v2/data.db) 复制到 scratch（每次重复制，
     消除 hierarchy_snapshots 累积与上轮污染，保证代间同起点）
  2. AI_READER_DATA_DIR 指向 scratch（所有 src DB 访问走 config.DB_PATH）
  3. --params 指定的 JSON 经 EVOLVE_PARAMS_JSON 环境变量注入
     （src.services.geo_skills.evolve_params；未设置时走代码原值，
     生产默认行为逐字节不变）
  4. 对五本小说跑 build_default_orchestrator(fresh=True) 规则管线
     （tier→votes→prior→edmonds→suffix→purify，无 LLM；封神无硬编码
     先验会走 LLM 路径，故封神跳过 prior skill——父代/候选同口径）
  5. 内层集三本 vs golden fixture 算 topology_metrics
     （parent_precision/recall/chain_accuracy，纯规则）；
     全部五本算 rebuild orphan_rate / max_children（回归护栏）

  6. --permute-chapters SEED：在 scratch 里按种子置换 chapter_facts 的
     fact_json（行序不动），模拟 §6.3"换章节顺序复测"的抖动。

输出：stdout 最后一行 JSON：
  {"xiyouji": {"parent_precision": ..., "parent_recall": ...,
               "chain_accuracy": ..., "orphan_rate": ..., "max_children": ...}, ...}

Usage:
    cd backend && .venv/bin/python scripts/evolve/compute_weight_metrics.py
    .venv/bin/python scripts/evolve/compute_weight_metrics.py --params out/candidate_params.json
    .venv/bin/python scripts/evolve/compute_weight_metrics.py --permute-chapters 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REAL_DB = Path.home() / ".ai-reader-v2" / "data.db"
SCRATCH_DIR = Path(os.environ.get("EVOLVE_SCRATCH_DIR", "/tmp/evolve-s2-data"))
FIXTURES = _BACKEND_DIR / "tests" / "fixtures"

NOVELS: dict[str, dict] = {
    "xiyouji": {"title": "西游记", "id": "3b2ef56c-1a55-466a-a7d1-34272446a198",
                "set": "inner", "fixture": "golden_standard_journey_to_west.json"},
    "honglou": {"title": "红楼梦", "id": "c384901a-8b71-437a-af35-b5ec1c56c696",
                "set": "inner", "fixture": "golden_standard_dream_of_red_chamber.json"},
    "shuihu": {"title": "水浒传", "id": "4ac43c73-f67b-427c-8d6d-e766a1423977",
               "set": "inner", "fixture": "golden_standard_water_margin.json"},
    "sanguo": {"title": "三国演义", "id": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
               "set": "holdout", "fixture": None},
    "fengshen": {"title": "封神演义", "id": "53013970-effd-4f50-aef7-728ca13de69a",
                 "set": "holdout", "fixture": None},
}
# 封神原无硬编码先验会落 LLM 路径(评估预算不允许);2026-09-19 起已有
# 硬编码 _FENGSHEN_PRIORS(requires_llm=False),不再跳过。
SKIP_PRIOR_SLUGS: set[str] = set()


def setup_scratch(permute_seed: int | None) -> Path:
    """复制真实 DB 到 scratch；可选按种子置换 chapter_facts 的 fact_json。

    复制后清空 hierarchy_snapshots:真实库中的历史快照链会在
    fresh 重建后让 store.load_latest 取到旧的高版本(快照按
    INSERT OR REPLACE 从 v0 重写,单轮 7 版,旧链 version 更高),
    导致测量读到的是陈旧结果而非本轮重建结果(2026-09-19 实测发现)。
    """
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    dst = SCRATCH_DIR / "data.db"
    shutil.copyfile(REAL_DB, dst)
    import sqlite3

    conn = sqlite3.connect(str(dst))
    try:
        conn.execute("DELETE FROM hierarchy_snapshots")
        if permute_seed is not None:
            for (nid,) in conn.execute("SELECT DISTINCT novel_id FROM chapter_facts"):
                rows = conn.execute(
                    "SELECT chapter_id, fact_json FROM chapter_facts "
                    "WHERE novel_id=? ORDER BY chapter_id", (nid,),
                ).fetchall()
                blobs = [r[1] for r in rows]
                random.Random(permute_seed).shuffle(blobs)
                for (chapter_id, _), blob in zip(rows, blobs, strict=True):
                    conn.execute(
                        "UPDATE chapter_facts SET fact_json=? WHERE chapter_id=?",
                        (blob, chapter_id),
                    )
        conn.commit()
    finally:
        conn.close()
    return dst


async def rebuild_parents(novel_id: str, title: str, skip_prior: bool) -> dict:
    """跑规则管线(fresh 起点)，返回最终 location_parents。"""
    from src.services.geo_skills.orchestrator import build_default_orchestrator

    orch = build_default_orchestrator(novel_id, novel_title=title)
    if skip_prior:
        orch._skills = [(t, s) for t, s in orch._skills if t != "prior"]
    async for _event in orch.run(fresh=True):
        pass
    snapshot = await orch.store.load_latest(novel_id)
    if snapshot is None:
        raise RuntimeError(f"rebuild 无快照产出: {novel_id}")
    return dict(snapshot.location_parents)


def tree_stats(parents: dict) -> dict:
    """rebuild 树结构统计（无 golden 的回归护栏）。"""
    children = set(parents.keys())
    all_nodes = children | {p for p in parents.values() if p}
    orphans = sum(1 for p in parents.values() if not p)
    roots = len(all_nodes - children) + orphans
    counts: dict[str, int] = {}
    for p in parents.values():
        if p:
            counts[p] = counts.get(p, 0) + 1
    return {
        "nodes": len(all_nodes),
        "orphan_rate": orphans / max(len(parents), 1),
        "root_count": roots,
        "max_children": max(counts.values()) if counts else 0,
    }


async def amain(args) -> dict:
    from src.utils.topology_metrics import compute_topology_metrics

    out: dict[str, dict] = {}
    for slug, info in NOVELS.items():
        t0 = time.monotonic()
        parents = await rebuild_parents(
            info["id"], info["title"], skip_prior=slug in SKIP_PRIOR_SLUGS
        )
        entry = tree_stats(parents)
        entry["rebuild_s"] = round(time.monotonic() - t0, 2)
        if info["fixture"]:
            golden = json.loads(
                (FIXTURES / info["fixture"]).read_text(encoding="utf-8")
            )
            topo = compute_topology_metrics(parents, golden["locations"])
            entry.update({
                "parent_precision": topo["parent_precision"],
                "parent_recall": topo["parent_recall"],
                "chain_accuracy": topo["chain_accuracy"],
            })
        out[slug] = entry
        print(f"[s2-eval] {slug}: {entry}", file=sys.stderr)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 2 权重评估子进程")
    parser.add_argument("--params", help="注入参数 JSON 路径（经 EVOLVE_PARAMS_JSON）")
    parser.add_argument("--permute-chapters", type=int, default=None, metavar="SEED",
                        help="按种子置换 chapter_facts（§6.3 换序复测）")
    args = parser.parse_args()

    if args.params:
        os.environ["EVOLVE_PARAMS_JSON"] = str(Path(args.params).resolve())

    setup_scratch(args.permute_chapters)
    os.environ["AI_READER_DATA_DIR"] = str(SCRATCH_DIR)

    # 运行时断言：src 必须指向 scratch（与 quality_dashboard 同款防护）
    from src.infra.config import DB_PATH

    if SCRATCH_DIR.resolve() not in Path(DB_PATH).resolve().parents:
        sys.exit(f"FATAL: DB_PATH {DB_PATH} 不在 scratch {SCRATCH_DIR} 内，拒绝运行。")

    out = asyncio.run(amain(args))
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
