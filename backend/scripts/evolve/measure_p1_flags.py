"""P1 增强组件 off/on 对照测量(五本小说,真实库 scratch 副本,只读真库)。

仿 scripts/evolve/compute_weight_metrics.py 的隔离模式:
  1. 真实 DB (~/.ai-reader-v2/data.db) 复制到 scratch,清空 hierarchy_snapshots
  2. 每个配置一份 params JSON,经 EVOLVE_PARAMS_JSON + reset_cache() 注入
     (现成通道,不 monkeypatch 源码常量);baseline = 空 JSON(全默认)
  3. 每本小说每个配置跑 build_default_orchestrator(fresh=True) 规则管线
  4. 指标:
     - errata gold parent_precision(hierarchy_validator.compute_metrics_from_gold,
       gold = backend/data/hierarchy_validation/<slug>_errata_gold.json)
     - golden fixture parent_precision / chain_accuracy
       (src.utils.topology_metrics.compute_topology_metrics;三国/封神无
       fixture,只报 errata 指标)
     - per-level(spatial_quality.compute_per_level_metrics,有 fixture 时)
  5. 确定性:P1-on 跑两遍,parents dict 逐边比对

stdout 末行 JSON:
  {slug: {"baseline": {...}, "p1_on": {...}, "deterministic": true/false}}

Usage:
    cd backend && .venv/bin/python scripts/evolve/measure_p1_flags.py
    .venv/bin/python scripts/evolve/measure_p1_flags.py \
        --configs baseline,a1_blend,b_decay,c_auditor
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REAL_DB = Path.home() / ".ai-reader-v2" / "data.db"
SCRATCH_DIR = Path(os.environ.get("P1_SCRATCH_DIR", "/tmp/p1-flags-data"))
FIXTURES = _BACKEND_DIR / "tests" / "fixtures"

NOVELS: dict[str, dict] = {
    "xiyouji": {"title": "西游记", "id": "3b2ef56c-1a55-466a-a7d1-34272446a198",
                "fixture": "golden_standard_journey_to_west.json"},
    "honglou": {"title": "红楼梦", "id": "c384901a-8b71-437a-af35-b5ec1c56c696",
                "fixture": "golden_standard_dream_of_red_chamber.json"},
    "shuihu": {"title": "水浒传", "id": "4ac43c73-f67b-427c-8d6d-e766a1423977",
               "fixture": "golden_standard_water_margin.json"},
    "sanguo": {"title": "三国演义", "id": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
               "fixture": None},
    "fengshen": {"title": "封神演义", "id": "53013970-effd-4f50-aef7-728ca13de69a",
                 "fixture": None},
}

CONFIGS: dict[str, dict] = {
    # 2026-09-20 起 auditor 默认启用(report_only 默认 False),baseline
    # 已等价于 c_auditor_refined;显式关闭 auditor 的旧基线可用
    # {"auditor.enabled": False} 注入。
    "baseline": {},
    "p1_on": {
        "votes.confidence_score_blend": True,
        "votes.primary_setting_discount": 0.5,
        "votes.conflict_decay": 0.5,
        "auditor.enabled": True,
        "auditor.report_only": False,
    },
    # 组件级隔离(回归定位用)
    "a1_blend": {"votes.confidence_score_blend": True},
    "a2_discount": {"votes.primary_setting_discount": 0.5},
    "b_decay": {"votes.conflict_decay": 0.5},
    "c_auditor": {"auditor.enabled": True, "auditor.report_only": False},
    # 只剔 error 级(CYCLE/TIER_INVERSION),warning 级只记录
    "c_auditor_error": {"auditor.enabled": True, "auditor.report_only": False,
                        "auditor.enforce_min_severity": "error"},
    # 精修 SCALE_SKIP(parent 为 kingdom/region 容器豁免)+ warning 级剔除生效
    "c_auditor_refined": {"auditor.enabled": True,
                          "auditor.report_only": False},
    # 词表精确名保护(_NAME_RANK_EXACT)生效后的测量——保护名单在源码
    # (world_structure_agent.py),非 evolve_param;此配置参数与 baseline
    # 相同,对照对象是保护项加入前的 baseline 测量。
    "vocab_protect": {},
    # 对照用:显式关闭 auditor(回到 auditor 引入前的管线行为)
    "auditor_off": {"auditor.enabled": False},
}


def setup_scratch() -> Path:
    """复制真实 DB 到 scratch 并清空 hierarchy_snapshots(每次全量重复制)。"""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    dst = SCRATCH_DIR / "data.db"
    shutil.copyfile(REAL_DB, dst)
    conn = sqlite3.connect(str(dst))
    try:
        conn.execute("DELETE FROM hierarchy_snapshots")
        conn.commit()
    finally:
        conn.close()
    return dst


def _write_params(name: str, params: dict) -> Path:
    p = SCRATCH_DIR / f"params_{name}.json"
    p.write_text(json.dumps(params), encoding="utf-8")
    return p


def _activate(params_path: Path) -> None:
    from src.services.geo_skills import evolve_params as ep

    os.environ["EVOLVE_PARAMS_JSON"] = str(params_path)
    ep.reset_cache()


async def rebuild(novel_id: str, title: str) -> tuple[dict, dict]:
    """fresh 规则管线重建,返回 (parents, tiers)。调用前须已 _activate。"""
    from src.db.sqlite_db import get_connection
    from src.services.geo_skills.orchestrator import build_default_orchestrator

    # 清掉本小说历史快照,避免 load_latest 取到上一轮残留高版本
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,))
        await conn.commit()
    finally:
        await conn.close()

    orch = build_default_orchestrator(novel_id, novel_title=title)
    async for _event in orch.run(fresh=True):
        pass
    snapshot = orch._last_run_snapshot
    if snapshot is None:
        raise RuntimeError(f"rebuild 无快照产出: {novel_id}")
    return dict(snapshot.location_parents), dict(snapshot.location_tiers)


def _nodes_of(parents: dict, tiers: dict) -> set[str]:
    return (set(parents) | set(parents.values()) | set(tiers)) - {"", None}


def score_run(slug: str, parents: dict, tiers: dict) -> dict:
    """errata gold + golden fixture + per-level 指标(纯计算,无 DB)。"""
    from src.services.hierarchy_validator import (
        compute_metrics_from_gold,
        load_gold,
    )

    entry: dict = {}
    gold, gold_raw = load_gold(slug)
    m, _resolved = compute_metrics_from_gold(
        slug, _nodes_of(parents, tiers), gold,
        current_tiers=tiers, current_parents=parents, gold_raw=gold_raw,
    )
    entry["errata_parent_precision"] = round(m.parent_precision, 4)
    entry["errata_overall"] = round(m.overall, 4)
    entry["errata_error_count"] = m.error_count

    fixture = NOVELS[slug]["fixture"]
    if fixture:
        from src.utils.spatial_quality import compute_per_level_metrics
        from src.utils.topology_metrics import compute_topology_metrics

        golden = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        topo = compute_topology_metrics(parents, golden["locations"])
        entry["fixture_parent_precision"] = topo["parent_precision"]
        entry["fixture_chain_accuracy"] = topo["chain_accuracy"]
        per_level = compute_per_level_metrics(parents, golden["locations"])
        entry["per_level_macro_precision"] = per_level["macro_precision"]
        entry["per_level"] = per_level["levels"]
    return entry


async def amain(config_names: list[str], determinism_config: str) -> dict:
    out: dict[str, dict] = {}
    for slug, info in NOVELS.items():
        entry: dict = {}
        parents_by_config: dict[str, dict] = {}
        for name in config_names:
            _activate(_write_params(name, CONFIGS[name]))
            t0 = time.monotonic()
            try:
                parents, tiers = await rebuild(info["id"], info["title"])
                metrics = score_run(slug, parents, tiers)
                metrics["rebuild_s"] = round(time.monotonic() - t0, 2)
                entry[name] = metrics
                parents_by_config[name] = parents
                print(f"[p1-measure] {slug}/{name}: {metrics}",
                      file=sys.stderr, flush=True)
            except Exception as e:  # 单本失败记 error 继续
                entry[name] = {"error": f"{type(e).__name__}: {e}"}
                print(f"[p1-measure] {slug}/{name} FAILED: {e}",
                      file=sys.stderr, flush=True)
        # 确定性:指定配置再跑一遍,parents 逐边比对
        if determinism_config in config_names:
            _activate(_write_params(
                determinism_config, CONFIGS[determinism_config]))
            try:
                parents2, _ = await rebuild(info["id"], info["title"])
                entry["deterministic"] = (
                    parents_by_config[determinism_config] == parents2)
            except Exception as e:
                entry["deterministic"] = f"error: {e}"
        out[slug] = entry
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 开关 off/on 对照测量")
    parser.add_argument("--configs", default="baseline,p1_on",
                        help="逗号分隔的配置名(见 CONFIGS)")
    parser.add_argument("--determinism-config", default="p1_on",
                        help="跑两遍做逐边确定性比对的配置名")
    args = parser.parse_args()

    config_names = args.configs.split(",")
    unknown = [c for c in config_names if c not in CONFIGS]
    if unknown:
        sys.exit(f"未知配置: {unknown};可选: {sorted(CONFIGS)}")
    if not REAL_DB.exists():
        sys.exit(f"FATAL: 真实库不存在: {REAL_DB}")

    setup_scratch()
    os.environ["AI_READER_DATA_DIR"] = str(SCRATCH_DIR)

    # 运行时断言:src 必须指向 scratch(与 compute_weight_metrics 同款防护)
    from src.infra.config import DB_PATH

    if SCRATCH_DIR.resolve() not in Path(DB_PATH).resolve().parents:
        sys.exit(f"FATAL: DB_PATH {DB_PATH} 不在 scratch {SCRATCH_DIR} 内,拒绝运行。")

    out = asyncio.run(amain(config_names, args.determinism_config))
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
