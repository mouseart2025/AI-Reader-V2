"""论文刷新试跑(version-refresh trial run)— 冻结 v071 抽取 × v0.78 聚合管线。

统一基准:~/.ai-reader-v2/data.db.bak-20260920-kimi(2026-09-20 Kimi 合并前
全库备份)。真库永不写入:备份复制到 scratch 目录,AI_READER_DATA_DIR 指向
scratch(隔离模式同 m5_no_edmonds_ablation.py)。

子命令:
  --step0   漂移基线:scratch 内 hierarchy_snapshots 六阶段结构指标
            对照 paper/evaluation/v071/ablation-by-stage.json(只读)。
  --run     五本全链(tier→votes→prior→edmonds→auditor→suffix→purify)
            + apply(scratch 内),产出:
            a. 每阶段结构指标(对照 ablation-by-stage.json 口径;
               auditor/purify 为新阶段,单列)
            b. 公平交集 Overall(voting∩full,m5/frozen 同口径)
            c. errata 金标五维(benchmark 口径 = 同交集外的全量 naive)
            每本跑两遍,逐边 diff 验证确定性。
输出:
  /tmp/v078-refresh/version_refresh_trialrun.json(机器明细)
  stdout 摘要(供报告引用)

Frozen-data safety:scratch 隔离,在导入任何 src.* 前完成;真库与
paper/evaluation/v071/ 下 JSON 一律只读。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

# ── Scratch-DB isolation: must happen BEFORE any src.* import ──
_BACKEND_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(_BACKEND_DIR))

_REAL_DB = Path(
    os.environ.get(
        "VR_BASE_DB",
        str(Path.home() / ".ai-reader-v2" / "data.db.bak-20260920-kimi"),
    )
)
_SCRATCH_DIR = Path(os.environ.get("VR_DATA_DIR", "/tmp/v078-refresh"))
_SCRATCH_DB = _SCRATCH_DIR / "data.db"

_REAL_HOME = Path.home() / ".ai-reader-v2"
if _SCRATCH_DIR.resolve() == _REAL_HOME.resolve():
    sys.exit("FATAL: VR_DATA_DIR must differ from the real data dir.")

if "--refresh" in sys.argv or not _SCRATCH_DB.exists():
    _SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[vr] copying frozen base DB → {_SCRATCH_DB} ...", flush=True)
    shutil.copy2(_REAL_DB, _SCRATCH_DB)

os.environ["AI_READER_DATA_DIR"] = str(_SCRATCH_DIR)

PAPER_EVAL = (
    _BACKEND_DIR.parent.parent
    / "ai-reader-internal" / "paper" / "evaluation" / "v071"
).resolve()
OUT_JSON = _SCRATCH_DIR / "version_refresh_trialrun.json"

NOVELS = [
    ("3b2ef56c-1a55-466a-a7d1-34272446a198", "xiyouji", "西游记"),
    ("c384901a-8b71-437a-af35-b5ec1c56c696", "honglou", "红楼梦"),
    ("4ac43c73-f67b-427c-8d6d-e766a1423977", "shuihu", "水浒传"),
    ("b1287ef6-c215-4bd2-842c-cb04aec5eb70", "sanguo", "三国演义"),
    ("53013970-effd-4f50-aef7-728ca13de69a", "fengshen", "封神演义"),
]

# 冻结六阶段 → 当前链 tag 映射(auditor/purify 为 v0.72+ 新阶段,单列)
STAGE_MAP = ["import", "tier", "votes", "prior", "edmonds", "suffix"]
NEW_STAGES = ["auditor", "purify"]

FROZEN_NAIVE_FULL_OVERALL = {
    "xiyouji": 0.9809128630705394,
    "honglou": 0.972,
    "shuihu": 0.8900939985538684,
    "sanguo": 0.8767857142857143,
    "fengshen": 0.9484978540772532,
}


# ── Step 0: 漂移基线 ─────────────────────────────────────────────

def step0() -> dict:
    import sqlite3

    stored = json.loads((PAPER_EVAL / "ablation-by-stage.json").read_text())
    conn = sqlite3.connect(str(_SCRATCH_DB))
    conn.row_factory = sqlite3.Row
    report: dict = {}
    for novel_id, key, title in NOVELS:
        cur = conn.execute(
            """
            SELECT tag, metrics_json FROM hierarchy_snapshots
            WHERE novel_id=? AND (tag, version) IN (
                SELECT tag, MAX(version) FROM hierarchy_snapshots
                WHERE novel_id=? GROUP BY tag)
            """,
            (novel_id, novel_id),
        )
        db: dict = {}
        for row in cur.fetchall():
            if not row["metrics_json"]:
                continue
            m = json.loads(row["metrics_json"])
            db[row["tag"]] = {
                "depth": m.get("avg_depth"),
                "max_ch": m.get("max_children"),
                "roots": m.get("root_count"),
                "nodes": m.get("node_count") or m.get("nodes"),
            }
        frozen = stored.get(key, {})
        stages = {}
        for stage in STAGE_MAP + NEW_STAGES:
            a, b = db.get(stage), frozen.get(stage)
            diffs = []
            if a is not None and b is not None:
                for k in ("depth", "max_ch", "roots", "nodes"):
                    if a.get(k) != b.get(k):
                        diffs.append(f"{k}: db={a.get(k)} json={b.get(k)}")
            elif a is None and b is not None:
                diffs.append("db missing")
            stages[stage] = {"db": a, "json": b, "diff": diffs}
        report[key] = {
            "title": title,
            "db_tags": sorted(db.keys()),
            "stages": stages,
            "drift": any(s["diff"] for s in stages.values()),
        }
    conn.close()
    return report


# ── Step 3: 全链 + 三口径 ────────────────────────────────────────

class _CapturingVoteResolver:  # VoteResolver 输出捕获(m5 同款,就地定义避免导入副作用)
    pass


def _nodes_of(parents: dict, tiers: dict) -> set:
    return (set(parents) | set(parents.values()) | set(tiers)) - {"", None}


async def run_full_chain(novel_id: str, key: str, title: str) -> dict:
    """当前 v0.78 完整链 + apply(scratch),返回阶段指标/最终 parents/ws。"""
    from src.db.sqlite_db import get_connection
    from src.services.geo_skills.orchestrator import build_default_orchestrator

    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,))
        await conn.commit()
    finally:
        await conn.close()

    orch = build_default_orchestrator(novel_id, novel_title=title)
    async for _ in orch.run(fresh=True):
        pass
    snap = orch._last_run_snapshot
    parents = dict(snap.location_parents)
    tiers = dict(snap.location_tiers or {})

    # 阶段指标(latest per tag)
    conn = await get_connection()
    stage_metrics = {}
    try:
        cur = await conn.execute(
            """
            SELECT tag, metrics_json FROM hierarchy_snapshots
            WHERE novel_id=? AND (tag, version) IN (
                SELECT tag, MAX(version) FROM hierarchy_snapshots
                WHERE novel_id=? GROUP BY tag)
            """,
            (novel_id, novel_id),
        )
        for row in await cur.fetchall():
            if not row[1]:
                continue
            m = json.loads(row[1])
            stage_metrics[row[0]] = {
                "depth": m.get("avg_depth"),
                "max_ch": m.get("max_children"),
                "roots": m.get("root_count"),
                "nodes": m.get("node_count") or m.get("nodes"),
            }
    finally:
        await conn.close()

    apply_res = await orch.apply_to_world_structure()

    # 落库 ws(scratch)
    import sqlite3
    conn2 = sqlite3.connect(str(_SCRATCH_DB))
    ws = json.loads(conn2.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?",
        (novel_id,)).fetchone()[0])
    conn2.close()
    return {
        "stage_metrics": stage_metrics,
        "snapshot_parents": parents,
        "snapshot_tiers": tiers,
        "ws_parents": ws.get("location_parents", {}),
        "ws_tiers": ws.get("location_tiers", {}),
        "ws_max_children": Counter(ws.get("location_parents", {}).values()).most_common(1)[0][1],
        "apply": {k: v for k, v in apply_res.items() if k != "alias_merge"},
    }


async def run_voting_capture(novel_id: str, key: str, title: str) -> dict:
    """tier→votes→voting(CapturingVoteResolver)短链,取 greedy voting 输出。"""
    from src.db.sqlite_db import get_connection
    from src.services.geo_skills.orchestrator import GeoOrchestrator
    from src.services.geo_skills.snapshot import HierarchyMetrics
    from src.services.geo_skills.snapshot_store import SnapshotStore
    from src.services.geo_skills.tier_classifier import TierClassifier
    from src.services.geo_skills.vote_builder import VoteBuilder
    from src.services.geo_skills.vote_resolver import VoteResolver

    class CapturingVoteResolver(VoteResolver):
        def __init__(self) -> None:
            self.captured: dict = {}

        async def execute(self, snapshot):
            result = await super().execute(snapshot)
            self.captured = {
                k: v for k, v in result.parent_overrides.items() if v is not None
            }
            return result

    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,))
        await conn.commit()
    finally:
        await conn.close()

    voting = CapturingVoteResolver()
    orch = GeoOrchestrator(novel_id, novel_title=title)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id, novel_title=title))
    orch.add_skill("voting", voting)
    async for _ in orch.run(fresh=True):
        pass
    snap_voting = await SnapshotStore().load_version(novel_id, 3)
    mv = HierarchyMetrics.compute(snap_voting)
    return {
        "voting_parents": dict(voting.captured),
        "voting_tiers": dict(snap_voting.location_tiers or {}),
        "voting_merged_max_ch": mv.max_children,
        "voting_merged_max_ch_node": mv.max_children_node,
    }


def score_gold(key: str, nodes: set, tiers: dict, parents: dict,
               gold: dict, gold_raw: dict) -> dict:
    from src.services.hierarchy_validator import compute_metrics_from_gold
    m, _ = compute_metrics_from_gold(
        key, nodes, gold,
        current_tiers=tiers, current_parents=parents, gold_raw=gold_raw,
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


async def run_book(novel_id: str, key: str, title: str) -> dict:
    from src.services.hierarchy_validator import load_gold

    full = await run_full_chain(novel_id, key, title)
    voting = await run_voting_capture(novel_id, key, title)

    gold, gold_raw = load_gold(key)

    # 3c/naive: 全量 gold × 落库 ws(= benchmark 口径)
    ws_parents, ws_tiers = full["ws_parents"], full["ws_tiers"]
    ws_nodes = _nodes_of(ws_parents, ws_tiers)
    naive_full = score_gold(key, ws_nodes, ws_tiers, ws_parents, gold, gold_raw)

    # 3b: 公平交集 voting∩full(frozen fair 同口径)
    voting_parents = voting["voting_parents"]
    voting_tiers = voting["voting_tiers"]
    voting_nodes = _nodes_of(voting_parents, voting_tiers)
    inter = voting_nodes & ws_nodes
    gold_sub_names = {n for n in gold if n in inter}
    gold_sub = {n: gold[n] for n in gold_sub_names}
    gold_raw_sub = {"nodes": {n: gold_raw["nodes"][n]
                              for n in gold_sub_names if n in gold_raw.get("nodes", {})}}
    fair_voting = score_gold(key, voting_nodes, voting_tiers, voting_parents,
                             gold_sub, gold_raw_sub)
    fair_full = score_gold(key, ws_nodes, ws_tiers, ws_parents,
                           gold_sub, gold_raw_sub)

    return {
        "novel": key,
        "stage_metrics": full["stage_metrics"],
        "snapshot_max_ch": Counter(full["snapshot_parents"].values()).most_common(1)[0][1],
        "ws_parents": ws_parents,
        "ws_max_children": full["ws_max_children"],
        "voting_merged_max_ch": voting["voting_merged_max_ch"],
        "voting_merged_max_ch_node": voting["voting_merged_max_ch_node"],
        "naive_full": naive_full,
        "fair": {
            "nodes": {"voting": len(voting_nodes), "full": len(ws_nodes),
                      "intersection": len(inter), "gold_subset": len(gold_sub)},
            "voting": fair_voting,
            "full": fair_full,
        },
    }


async def main() -> None:
    from src.infra.config import DB_PATH
    assert Path(DB_PATH).resolve() == _SCRATCH_DB.resolve(), (
        f"refusing: DB_PATH={DB_PATH} is not scratch")
    print(f"[vr] scratch DB: {DB_PATH}", file=sys.stderr)

    ap = argparse.ArgumentParser()
    ap.add_argument("--step0", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="recopy base DB into scratch")
    args = ap.parse_args()

    out: dict = {"base_db": str(_REAL_DB), "scratch": str(_SCRATCH_DB)}

    if args.step0:
        print("[vr] Step 0 漂移基线 ...", file=sys.stderr)
        out["step0_drift"] = step0()
        for key, r in out["step0_drift"].items():
            print(f"  {key}: drift={r['drift']} tags={','.join(r['db_tags'])}",
                  file=sys.stderr)

    if args.run:
        passes = []
        for pass_no in (1, 2):
            # 每遍从统一基座重新复制:隔离 run-to-run 确定性
            # (apply 会改写 scratch ws,baseline 注入使下一遍起点漂移——
            # 机制③非幂等,见 version-refresh 报告;此处测的是同起点确定性)
            print(f"[vr] recopy base DB for pass {pass_no} ...", file=sys.stderr)
            shutil.copy2(_REAL_DB, _SCRATCH_DB)
            print(f"[vr] full run pass {pass_no} ...", file=sys.stderr)
            books = {}
            for novel_id, key, title in NOVELS:
                print(f"[vr]   {title} ...", file=sys.stderr, flush=True)
                books[key] = await run_book(novel_id, key, title)
            passes.append(books)
        # 确定性:两遍逐边 diff + 指标 diff
        det = {}
        for key in passes[0]:
            a, b = passes[0][key], passes[1][key]
            edge_diff = sum(
                1 for k in set(a["ws_parents"]) | set(b["ws_parents"])
                if a["ws_parents"].get(k) != b["ws_parents"].get(k))
            det[key] = {
                "ws_edge_diff": edge_diff,
                "naive_overall_equal": a["naive_full"] == b["naive_full"],
                "fair_equal": a["fair"] == b["fair"],
                "stage_metrics_equal": a["stage_metrics"] == b["stage_metrics"],
            }
            print(f"  {key}: ws_edge_diff={edge_diff} "
                  f"naive_eq={det[key]['naive_overall_equal']} "
                  f"fair_eq={det[key]['fair_equal']}", file=sys.stderr)
        for key in passes[0]:
            passes[0][key].pop("ws_parents", None)
            passes[1][key].pop("ws_parents", None)
        out["run_pass1"] = passes[0]
        out["determinism"] = det

    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[vr] saved: {OUT_JSON}")


if __name__ == "__main__":
    asyncio.run(main())
