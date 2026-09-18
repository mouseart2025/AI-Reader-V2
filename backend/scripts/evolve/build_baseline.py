"""GeoEvolve 阶段 0 —— 汇总 baseline.json。

输入产物（均已由冻结评估器产出，本脚本只做只读汇总，不重算任何指标）：
  - out/dashboard/{slug}.json     quality_dashboard.py 五本 M1-M5 全量测量
  - out/dashboard/summary.md      M6 汇总行 + LLM 成本
  - scratch DB (/tmp/q0-data/data.db，dashboard 跑完留下)
    的 map_layouts.satisfaction_json（只读）
  - golden 门禁：subprocess 跑 tests/test_golden_standard.py（纯本地，~2s）

输出：backend/scripts/evolve/baseline.json（git 跟踪）。
缺失维度（如某本无 map_layouts 行）在 per-novel missing 列表里标记，不报错。

Usage:
    cd backend && .venv/bin/python scripts/evolve/build_baseline.py
    .venv/bin/python scripts/evolve/build_baseline.py --skip-golden
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
if str(_EVOLVE_DIR) not in sys.path:
    sys.path.insert(0, str(_EVOLVE_DIR))
if str(_BACKEND_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR / "scripts"))  # quality_dashboard 所在

from run_loop import (  # noqa: E402  (sys.path 设置后导入)
    BASELINE_PATH,
    DASHBOARD_DIR,
    PER_NOVEL_METRICS,
)

SCRATCH_DB = Path("/tmp/q0-data/data.db")

# 五本小说：slug → (标题, 集合)。西游/红楼 id 冻结（DB 存在同名重复行），
# 与 quality_dashboard.NOVELS 保持一致。
NOVELS: dict[str, dict] = {
    "xiyouji": {"title": "西游记", "set": "inner",
                "novel_id": "3b2ef56c-1a55-466a-a7d1-34272446a198"},
    "honglou": {"title": "红楼梦", "set": "inner",
                "novel_id": "c384901a-8b71-437a-af35-b5ec1c56c696"},
    "shuihu": {"title": "水浒传", "set": "inner", "novel_id": None},
    "sanguo": {"title": "三国演义", "set": "holdout", "novel_id": None},
    "fengshen": {"title": "封神演义", "set": "holdout", "novel_id": None},
}

# dashboard JSON 指标路径 → baseline 拍平键（只收 PER_NOVEL_METRICS 口径内的）
_METRIC_PATHS: dict[str, tuple[str, ...]] = {
    "m1.orphan_rate": ("m1", "orphan_rate"),
    "m2.recall_proxy": ("m2", "recall_proxy"),
    "m3.direction_error_rate": ("m3", "direction_error_rate"),
    "m4.generic_residue": ("m4", "generic_residue"),
    "m5.m5": ("m5", "m5"),
}


def _dig(d: dict, path: tuple[str, ...]):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def run_golden_gate() -> dict:
    """subprocess 跑 golden pytest（纯本地），解析计数行。"""
    try:
        proc = subprocess.run(
            [str(_BACKEND_DIR / ".venv" / "bin" / "python"), "-m", "pytest",
             "tests/test_golden_standard.py", "-q"],
            cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=600,
        )
    except Exception as err:
        return {"status": "missing", "error": str(err)}
    out = proc.stdout + "\n" + proc.stderr
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for n, kind in re.findall(r"(\d+)\s+(passed|failed|skipped|error|errors)", out):
        key = "errors" if kind.startswith("error") else kind
        counts[key] = counts.get(key, 0) + int(n)
    total = counts["passed"] + counts["failed"]
    counts["pass_rate"] = (counts["passed"] / total) if total else None
    counts["status"] = "ok" if proc.returncode == 0 else "failed"
    counts["returncode"] = proc.returncode
    return counts


def load_satisfaction(scratch_db: Path) -> dict[str, float]:
    """从 scratch DB 只读提取 map_layouts.satisfaction_json 的 total_satisfaction。

    返回 novel_id → total_satisfaction；无行/无字段的小说不在结果里。
    """
    if not scratch_db.exists():
        print(f"[baseline] scratch DB 不存在: {scratch_db}，satisfaction 全部 missing")
        return {}
    conn = sqlite3.connect(f"file:{scratch_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT novel_id, satisfaction_json FROM map_layouts"
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, float] = {}
    for novel_id, sjson in rows:
        if not sjson:
            continue
        try:
            d = json.loads(sjson)
        except json.JSONDecodeError:
            continue
        val = d.get("total_satisfaction")
        if isinstance(val, (int, float)):
            out[novel_id] = float(val)
    return out


def parse_summary_cost(summary_md: Path) -> float | None:
    """从 summary.md 提取 LLM 总成本（美元）。"""
    if not summary_md.exists():
        return None
    m = re.search(r"≈ \*\*\$([\d.]+)\*\*", summary_md.read_text(encoding="utf-8"))
    return float(m.group(1)) if m else None


def build_baseline(skip_golden: bool = False,
                   dashboard_dir: Path = DASHBOARD_DIR) -> dict:
    """汇总五本小说可得指标 + satisfaction + golden 门禁 → baseline dict。"""
    satisfaction_by_nid = load_satisfaction(SCRATCH_DB)
    golden = {"status": "skipped"} if skip_golden else run_golden_gate()

    novels: dict[str, dict] = {}
    db_md5 = None
    for slug, info in NOVELS.items():
        dash_path = dashboard_dir / f"{slug}.json"
        entry: dict = {"title": info["title"], "set": info["set"],
                       "novel_id": info["novel_id"], "metrics": {}, "missing": []}
        if not dash_path.exists():
            entry["missing"] = sorted(PER_NOVEL_METRICS)
            entry["error"] = f"dashboard 产物缺失: {dash_path.name}"
            novels[slug] = entry
            continue
        report = json.loads(dash_path.read_text(encoding="utf-8"))
        if report.get("error"):
            entry["error"] = report["error"]
        entry["novel_id"] = report.get("novel_id") or info["novel_id"]
        db_md5 = db_md5 or _dig(report, ("freeze", "db_md5"))
        for mkey, path in _METRIC_PATHS.items():
            val = _dig(report, path)
            if isinstance(val, (int, float)):
                entry["metrics"][mkey] = float(val)
            else:
                entry["missing"].append(mkey)
        nid = entry["novel_id"]
        if nid and nid in satisfaction_by_nid:
            entry["metrics"]["satisfaction"] = satisfaction_by_nid[nid]
        else:
            entry["missing"].append("satisfaction")
        entry["missing"].sort()
        entry["dashboard_file"] = f"out/dashboard/{slug}.json"
        novels[slug] = entry

    # M6 全局指标：优先复用冻结实现 compute_m6(load_m6_eval())（纯规则不调 LLM，
    # 与 quality_loop 同一实现）；失败时降级解析 summary.md（百分数有舍入误差）。
    summary = dashboard_dir / "summary.md"
    m6: dict = {"status": "missing"}
    try:
        import quality_dashboard as qd

        m6 = qd.compute_m6(qd.load_m6_eval())
        m6["note"] = "compute_m6(load_m6_eval())，与 quality_loop 同一实现"
    except Exception as err:
        if summary.exists():
            text = summary.read_text(encoding="utf-8")
            shuihu = re.search(r"类型级 ([\d.]+)%", text)
            xiyouji = re.search(r"category ([\d.]+)%", text)
            if shuihu and xiyouji:
                m6 = {
                    "status": "ok",
                    "shuihu_subtype_accuracy": float(shuihu.group(1)) / 100,
                    "xiyouji_mock_category": float(xiyouji.group(1)) / 100,
                    "note": f"降级：解析 summary.md 舍入值（compute_m6 不可用: {err}）",
                }

    return {
        "version": 0,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "db_md5": db_md5,
        "db_path": "~/.ai-reader-v2/data.db（只读；测量在 scratch 副本上进行）",
        "scratch_db": str(SCRATCH_DB),
        "llm_cost_usd": parse_summary_cost(summary),
        "golden": golden,
        "m6": m6,
        "novels": novels,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GeoEvolve 阶段 0 baseline.json 汇总")
    parser.add_argument("--skip-golden", action="store_true",
                        help="跳过 golden pytest（复用已有人工结果时）")
    args = parser.parse_args(argv)

    baseline = build_baseline(skip_golden=args.skip_golden)
    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[baseline] 已写入 {BASELINE_PATH}")
    for slug, entry in baseline["novels"].items():
        n_metrics = len(entry["metrics"])
        missing = ",".join(entry["missing"]) or "无"
        print(f"[baseline] {slug}: {n_metrics} 维指标, missing=[{missing}]")
    g = baseline["golden"]
    print(f"[baseline] golden: {g.get('status')} "
          f"passed={g.get('passed')} failed={g.get('failed')}")
    print(f"[baseline] m6: {baseline['m6'].get('status')} "
          f"db_md5={baseline.get('db_md5')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
