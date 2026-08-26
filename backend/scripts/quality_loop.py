"""质量改进循环编排 (任务 B):一条命令完成 测量 → 对比 → 记录。

每轮质量改动后跑一次,自动完成:
  1. 测量(各门可独立降级,缺产物标 missing 不报错):
     - golden 硬门禁: subprocess 跑 pytest tests/test_golden_standard.py,
       解析 pass/fail 计数(--no-pytest 跳过)
     - M6 关系维度回测: 复用 quality_dashboard.load_m6_eval / compute_m6
       (单一实现,不复制;产物缺失时自动离线重算,纯规则不调 LLM)
     - M5 抽取忠实度: 复用 quality_dashboard.load_latest_judge_report /
       load_judge_calibration / compute_m5,无 judge 产物时 missing
       (M1–M4 依赖冻结 DB,本脚本不重算,统一 missing)
     - 冒烟产物: 最新 audit_reports/smoke_extraction_quality_*.json 的关键指标
  2. 记录: 向 audit_reports/quality_history.jsonl 追加一条
     (timestamp / git sha / 分支 / 五个开关 / 各指标 / 测试计数)
  3. 对比: 与上一条 history 记录逐项对比,delta 表渲染到 stdout +
     audit_reports/quality_loop_latest.md;硬门禁回退退出码非零,
     软指标下降打 ⚠️ 警告但不 fail。

硬门禁(回退即退出码 1):
  - golden fail 数增加,或 golden 正确率跌破阈值(默认 100%)
  - M6 水浒 subtype 达标 / 西游不低于旧基线 由 True 翻转为 False
其余数值指标下降一律为软警告。

Usage:
    cd backend && .venv/bin/python scripts/quality_loop.py --tag after-epic4
    .venv/bin/python scripts/quality_loop.py --no-pytest --tag baseline
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPORT_DIR = _BACKEND_DIR / "audit_reports"
HISTORY_PATH = REPORT_DIR / "quality_history.jsonl"
LATEST_MD_PATH = REPORT_DIR / "quality_loop_latest.md"

GOLDEN_PASS_THRESHOLD = 1.0   # golden 正确率硬门禁阈值
_EPS = 1e-9

# 数值指标的"更优方向":higher=越大越好,lower=越小越好。
# 不在表中的数值键只展示 delta,不参与回退判定(info)。
METRIC_DIRECTION: dict[str, str] = {
    "golden.pass_rate": "higher",
    "golden.failed": "lower",
    "m6.shuihu_subtype_accuracy": "higher",
    "m6.xiyouji_mock_category": "higher",
    "m5.*.m5": "higher",
    "m5.*.evidence_coverage": "higher",
    "m5.*.span_located_rate": "higher",
    "smoke.polarity_fill_rate": "higher",
    "smoke.rel_subtype_fill_rate": "higher",
    "smoke.closeness_fill_rate": "higher",
    "smoke.evidence_coverage": "higher",
    "smoke.span_located_rate": "higher",
    "smoke.recall_additions": "higher",
    "smoke.invalid_dimension_total": "lower",
    "smoke.hallucination_removed": "lower",
}

# 硬门禁键(支持 m6 两个布尔翻转),其余回归一律软警告
HARD_NUMERIC_KEYS = {"golden.failed", "golden.pass_rate"}
HARD_BOOL_KEYS = {"m6.shuihu_target_met", "m6.xiyouji_not_below_baseline"}

# 指标中文标签(渲染用,缺省用键名)
METRIC_LABELS: dict[str, str] = {
    "golden.passed": "golden 通过数",
    "golden.failed": "golden 失败数",
    "golden.skipped": "golden 跳过数",
    "golden.pass_rate": "golden 正确率",
    "m6.shuihu_subtype_accuracy": "M6 水浒 subtype 准确率",
    "m6.shuihu_target_met": "M6 水浒达标",
    "m6.xiyouji_mock_category": "M6 西游 mock category",
    "m6.xiyouji_not_below_baseline": "M6 西游不低于旧基线",
    "smoke.polarity_fill_rate": "冒烟 polarity 填充率",
    "smoke.rel_subtype_fill_rate": "冒烟 rel_subtype 填充率",
    "smoke.closeness_fill_rate": "冒烟 closeness 填充率",
    "smoke.evidence_coverage": "冒烟 evidence 覆盖率",
    "smoke.span_located_rate": "冒烟 span 定位率",
    "smoke.recall_additions": "冒烟 recall 补漏数",
    "smoke.invalid_dimension_total": "冒烟越界拦截数",
    "smoke.vote_overrides": "冒烟投票 override 数",
    "smoke.hallucination_candidates": "冒烟幻觉候选数",
    "smoke.hallucination_removed": "冒烟幻觉剔除数",
    "smoke.total_tokens": "冒烟 token 总量",
    "smoke.cost_usd": "冒烟成本 USD",
}


# ── golden 硬门禁 ──────────────────────────────────────────────────

_PYTEST_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed)")


def parse_pytest_summary(output: str) -> dict:
    """解析 pytest -q 尾部计数行为 dict(纯函数)。"""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for n, kind in _PYTEST_COUNT_RE.findall(output):
        key = "errors" if kind == "error" else kind
        if key in counts:
            counts[key] += int(n)
    total = counts["passed"] + counts["failed"]
    counts["pass_rate"] = (counts["passed"] / total) if total else None
    return counts


def run_golden_gate(backend_dir: Path = _BACKEND_DIR, timeout: int = 300) -> dict:
    """subprocess 跑 golden 测试并解析计数;失败(无法运行)标 missing。"""
    try:
        proc = subprocess.run(
            [str(backend_dir / ".venv" / "bin" / "python"), "-m", "pytest",
             "tests/test_golden_standard.py", "-q"],
            cwd=backend_dir, capture_output=True, text=True, timeout=timeout,
        )
    except Exception as err:
        return {"status": "missing", "error": str(err)}
    result = parse_pytest_summary(proc.stdout + "\n" + proc.stderr)
    result["status"] = "ok" if proc.returncode == 0 else "failed"
    result["returncode"] = proc.returncode
    return result


# ── M5 / M6 / 冒烟产物(复用 quality_dashboard,单一实现)────────────

def collect_m6() -> dict:
    """M6 关系维度回测:load_m6_eval 缺失时其内部自动离线重算。"""
    try:
        import quality_dashboard as qd

        return qd.compute_m6(qd.load_m6_eval())
    except Exception as err:
        return {"status": "missing", "error": str(err)}


def collect_m5() -> dict:
    """M5 抽取忠实度:消费各小说最新 judge 报告;全部缺失则 missing。"""
    try:
        import quality_dashboard as qd
    except Exception as err:
        return {"status": "missing", "error": str(err)}
    calibration = qd.load_judge_calibration()
    per_slug: dict[str, dict] = {}
    for slug, _title, _nid in qd.NOVELS:
        m5 = qd.compute_m5(qd.load_latest_judge_report(slug), calibration)
        if m5.get("status") == "ok":
            per_slug[slug] = m5
    if not per_slug:
        return {"status": "missing"}
    per_slug["status"] = "ok"
    return per_slug


def load_latest_smoke(report_dir: Path = REPORT_DIR) -> dict | None:
    """加载最新冒烟产物 JSON;无则 None。"""
    candidates = sorted(report_dir.glob("smoke_extraction_quality_*.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def smoke_metrics(smoke_json: dict) -> dict:
    """从冒烟报告提取进入质量历史的关键指标(纯函数)。"""
    d = smoke_json.get("dimensions", {})
    ev = smoke_json.get("evidence", {})
    s = smoke_json.get("sanitize", {})
    rec = smoke_json.get("recall_pass", {})
    hall = smoke_json.get("hallucination", {})
    usage = smoke_json.get("usage", {})
    actions = hall.get("actions", [])
    return {
        "status": "ok",
        "mode": smoke_json.get("mode"),
        "polarity_fill_rate": d.get("polarity_fill_rate"),
        "rel_subtype_fill_rate": d.get("rel_subtype_fill_rate"),
        "closeness_fill_rate": d.get("closeness_fill_rate"),
        "evidence_coverage": ev.get("overall_coverage"),
        "span_located_rate": ev.get("span_located_rate"),
        "recall_additions": sum(rec.get(k, 0) for k in ("characters", "relationships", "events")),
        "invalid_dimension_total": s.get("invalid_dimension_total", 0),
        "vote_overrides": s.get("vote_overrides", 0),
        "hallucination_candidates": len(hall.get("candidates", [])),
        "hallucination_removed": sum(1 for a in actions if a.get("action") == "removed"),
        "total_tokens": usage.get("total_tokens"),
        "cost_usd": usage.get("cost_usd"),
    }


def collect_smoke(report_dir: Path = REPORT_DIR) -> dict:
    smoke_json = load_latest_smoke(report_dir)
    if not smoke_json:
        return {"status": "missing"}
    try:
        return smoke_metrics(smoke_json)
    except Exception as err:
        return {"status": "missing", "error": str(err)}


# ── 记录:git / 开关 / history ──────────────────────────────────────

def git_info(cwd: Path = _BACKEND_DIR) -> dict:
    """git short sha + 分支;非 git 环境 graceful 降级。"""
    def _run(args: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10,
            )
            return proc.stdout.strip() or None if proc.returncode == 0 else None
        except Exception:
            return None

    return {
        "sha": _run(["rev-parse", "--short", "HEAD"]),
        "branch": _run(["rev-parse", "--abbrev-ref", "HEAD"]),
    }


def current_switches() -> dict:
    """五个质量开关当前状态。"""
    from src.infra import config

    return {
        "RELATION_DIMENSIONS_ENABLED": config.RELATION_DIMENSIONS_ENABLED,
        "ENTITY_RESOLUTION_ENABLED": config.ENTITY_RESOLUTION_ENABLED,
        "EVIDENCE_GROUNDING_ENABLED": config.EVIDENCE_GROUNDING_ENABLED,
        "RECALL_PASS_ENABLED": config.RECALL_PASS_ENABLED,
        "HALLUCINATION_REVIEW_ENABLED": config.HALLUCINATION_REVIEW_ENABLED,
    }


def build_record(tag: str | None, gates: dict) -> dict:
    """组装一条 history 记录(纯函数,git 信息由调用方传入)。"""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tag": tag,
        "git": gates.get("git", {}),
        "switches": gates.get("switches", {}),
        "golden": gates.get("golden", {"status": "missing"}),
        "m6": gates.get("m6", {"status": "missing"}),
        "m5": gates.get("m5", {"status": "missing"}),
        "smoke": gates.get("smoke", {"status": "missing"}),
    }


def append_history(record: dict, path: Path = HISTORY_PATH) -> Path:
    """向 JSONL 追加一条记录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path


def load_history(path: Path = HISTORY_PATH) -> list[dict]:
    """读取全部 history 记录;文件不存在返回空列表。"""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ── 对比:delta 表 + 回退判定 ───────────────────────────────────────

def _flatten_gate(prefix: str, gate: dict, out: dict) -> None:
    """把一门指标拍平成 dotted 数值键;无产物(missing/skipped)时不记。

    status="failed" 的门(如 golden 有 fail)仍拍平 — 回退对比正需要它。
    """
    if not isinstance(gate, dict):
        return
    if gate.get("status") not in ("ok", "failed"):
        return
    for key, val in gate.items():
        if key in ("status", "returncode", "error"):  # 过程元数据,非指标
            continue
        full = f"{prefix}.{key}"
        if isinstance(val, dict):  # m5 的 per-slug 子表
            _flatten_gate(full, val, out)
        elif isinstance(val, bool):
            out[full] = val
        elif isinstance(val, (int, float)):
            out[full] = val


def flatten_metrics(record: dict) -> dict:
    """拍平一条记录中可对比的数值/布尔指标。"""
    out: dict = {}
    for gate in ("golden", "m6", "m5", "smoke"):
        _flatten_gate(gate, record.get(gate, {}), out)
    return out


def _direction_for(key: str) -> str | None:
    if key in METRIC_DIRECTION:
        return METRIC_DIRECTION[key]
    # m5 per-slug 通配: m5.<slug>.<metric>
    parts = key.split(".")
    if len(parts) == 3 and f"{parts[0]}.*.{parts[2]}" in METRIC_DIRECTION:
        return METRIC_DIRECTION[f"{parts[0]}.*.{parts[2]}"]
    return None


def compare_records(prev: dict | None, curr: dict) -> list[dict]:
    """与上一条记录逐项对比,返回 delta 行(纯函数)。

    行 verdict: ok / warn(软指标下降)/ fail(硬门禁回退)/ new / missing。
    """
    curr_flat = flatten_metrics(curr)
    prev_flat = flatten_metrics(prev) if prev else {}
    rows: list[dict] = []
    for key in sorted(set(curr_flat) | set(prev_flat)):
        label = METRIC_LABELS.get(key, key)
        cur, prv = curr_flat.get(key), prev_flat.get(key)
        if prv is None:
            verdict = "new" if cur is not None else "missing"
            rows.append({"key": key, "label": label, "prev": None,
                         "curr": cur, "delta": None, "verdict": verdict})
            continue
        if cur is None:
            rows.append({"key": key, "label": label, "prev": prv,
                         "curr": None, "delta": None, "verdict": "missing"})
            continue
        if isinstance(cur, bool) or isinstance(prv, bool):
            verdict = "fail" if (key in HARD_BOOL_KEYS and prv is True and cur is False) else "ok"
            rows.append({"key": key, "label": label, "prev": prv,
                         "curr": cur, "delta": None, "verdict": verdict})
            continue
        delta = cur - prv
        verdict = "ok"
        if key in HARD_NUMERIC_KEYS:
            if key == "golden.failed" and delta > 0:
                verdict = "fail"
            elif key == "golden.pass_rate" and (
                delta < -_EPS or (cur is not None and cur < GOLDEN_PASS_THRESHOLD)
            ):
                verdict = "fail"
        else:
            direction = _direction_for(key)
            if direction == "higher" and delta < -_EPS:
                verdict = "warn"
            elif direction == "lower" and delta > _EPS:
                verdict = "warn"
        rows.append({"key": key, "label": label, "prev": prv,
                     "curr": cur, "delta": delta, "verdict": verdict})
    # golden 正确率跌破阈值(即使无历史可比)也是硬失败
    if not prev and curr_flat.get("golden.pass_rate") is not None:
        if curr_flat["golden.pass_rate"] < GOLDEN_PASS_THRESHOLD:
            for row in rows:
                if row["key"] == "golden.pass_rate":
                    row["verdict"] = "fail"
    return rows


def has_hard_regression(rows: list[dict]) -> bool:
    return any(r["verdict"] == "fail" for r in rows)


def _fmt(val) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, float):
        return f"{val:.4f}" if abs(val) < 10 else f"{val:.1f}"
    return str(val)


_VERDICT_MARK = {"ok": "", "warn": " ⚠️", "fail": " ❌回退", "new": " 🆕", "missing": " (missing)"}


def render_delta_md(curr: dict, prev: dict | None, rows: list[dict]) -> str:
    """渲染 delta 表 markdown(纯函数)。"""
    git = curr.get("git", {})
    lines = [
        "# 质量改进循环 · 最新对比",
        "",
        f"- 时间: {curr.get('timestamp')}",
        f"- tag: {curr.get('tag') or '—'} · git: {git.get('branch') or '?'}@{git.get('sha') or '?'}",
        "- 开关: " + " ".join(
            f"{k}={'开' if v else '关'}" for k, v in curr.get("switches", {}).items()
        ),
    ]
    if prev:
        pgit = prev.get("git", {})
        lines.append(
            f"- 对比基线: {prev.get('timestamp')} (tag={prev.get('tag') or '—'}, "
            f"{pgit.get('sha') or '?'})"
        )
    else:
        lines.append("- 对比基线: 无(首条记录)")
    lines += [
        "",
        "| 指标 | 上次 | 本次 | delta | 判定 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        delta = _fmt(r["delta"]) if not isinstance(r["delta"], float) else f"{r['delta']:+.4f}"
        lines.append(
            f"| {r['label']} | {_fmt(r['prev'])} | {_fmt(r['curr'])} | {delta} "
            f"| {r['verdict']}{_VERDICT_MARK.get(r['verdict'], '')} |"
        )
    lines.append("")
    if has_hard_regression(rows):
        lines.append("> ❌ 硬门禁回退:退出码非零,请先修复再迭代。")
    elif any(r["verdict"] == "warn" for r in rows):
        lines.append("> ⚠️ 存在软指标下降,不阻塞迭代,建议关注。")
    else:
        lines.append("> ✅ 未见回退。")
    lines.append("")
    return "\n".join(lines)


# ── 主流程 ─────────────────────────────────────────────────────────

def run_loop(
    *,
    tag: str | None = None,
    no_pytest: bool = False,
    report_dir: Path = REPORT_DIR,
    history_path: Path | None = None,
    golden_runner=run_golden_gate,
) -> tuple[dict, dict | None, list[dict], int]:
    """测量 → 记录 → 对比。返回 (record, prev_record, rows, exit_code)。"""
    history_path = history_path or report_dir / "quality_history.jsonl"

    if no_pytest:
        golden = {"status": "skipped"}
    else:
        golden = golden_runner()

    gates = {
        "git": git_info(),
        "switches": current_switches(),
        "golden": golden,
        "m6": collect_m6(),
        "m5": collect_m5(),
        "smoke": collect_smoke(report_dir),
    }
    record = build_record(tag, gates)
    history = load_history(history_path)
    prev = history[-1] if history else None
    append_history(record, history_path)
    rows = compare_records(prev, record)
    exit_code = 1 if has_hard_regression(rows) else 0
    return record, prev, rows, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="质量改进循环:测量 → 对比 → 记录")
    parser.add_argument("--tag", default=None, help="记录标签(如 after-epic4)")
    parser.add_argument("--no-pytest", action="store_true",
                        help="跳过 golden 硬门禁,只汇总已有产物")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)

    record, prev, rows, exit_code = run_loop(
        tag=args.tag, no_pytest=args.no_pytest, report_dir=args.report_dir,
    )
    md = render_delta_md(record, prev, rows)
    latest_md = args.report_dir / "quality_loop_latest.md"
    latest_md.write_text(md, encoding="utf-8")
    print(md)
    print(f"[quality-loop] 记录已追加: {args.report_dir / 'quality_history.jsonl'}")
    print(f"[quality-loop] delta 表: {latest_md}")
    if exit_code:
        print("[quality-loop] ❌ 硬门禁回退,退出码 1", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
