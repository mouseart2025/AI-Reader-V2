"""GeoEvolve 进化循环骨架（阶段 0：基线与骨架）。

规格：docs/analysis/geo-self-evolve-methodology.md §4.3（循环）、§5（阶段 0
退出标准）、§6（安全规则）。阶段 0 只做恒等变异（空 diff），不做真实变异
算子、不做 LLM 提议器；但循环的八个阶段全部落地，后续阶段在注册接口上
挂算子即可。

循环（一轮）：
  1. ANALYZE   读 audit_reports 最新 hierarchy diff + quality_history.jsonl
               尾部 + evolution_journal.jsonl 失败史 → 结构化上下文摘要
  2. PROPOSE   算子注册表取候选；阶段 0 只有 identity（恒等变异）
  3. APPLY     隔离应用接口；阶段 0 为 no-op（深拷贝 + 空 diff）
  4. EVAL      两种后端：cached（读 baseline.json，dry-run 默认，不花钱）
               / live（import quality_loop.run_loop 跑一轮门禁 + 汇总已有
               dashboard 产物）
  5. GATE      对照 eval_policy 阈值判定回归（单项 >0.01 绝对回归即拒；
               分小说记账无显著退化；golden pass_rate 硬阈值）
  6. ARCHIVE   Pareto 前沿：支配现任则入档（并剔除被支配者）；被支配拒绝；
               互不支配共存（种群上限，超出淘汰最老被支配者）。
               入档规则（JIT-Agent）：质量不降且至少一维严格改善
  7. COMMIT    追加 evolve/evolution_journal.jsonl（每代一条完整 lineage）；
               不打 git commit（由人决定）
  8. REPORT    每轮结束打印摘要；--report 输出当前前沿与趋势

安全（§6.1）：启动时校验 frozen_manifest.json 的 sha256 清单（评估器外置），
不符即中止。重新生成用 --freeze（仅限人为有意更新评估器后）。

Usage:
    cd backend && .venv/bin/python scripts/evolve/run_loop.py --generations 3 --dry-run
    .venv/bin/python scripts/evolve/run_loop.py --generations 1 --eval-backend live
    .venv/bin/python scripts/evolve/run_loop.py --report
    .venv/bin/python scripts/evolve/run_loop.py --freeze   # 重新生成冻结清单
"""

from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GENOME_PATH = _EVOLVE_DIR / "genome.yaml"
EVAL_POLICY_PATH = _EVOLVE_DIR / "eval_policy.yaml"
FROZEN_MANIFEST_PATH = _EVOLVE_DIR / "frozen_manifest.json"
BASELINE_PATH = _EVOLVE_DIR / "baseline.json"
JOURNAL_PATH = _EVOLVE_DIR / "evolution_journal.jsonl"
OUT_DIR = _EVOLVE_DIR / "out"
FRONTIER_PATH = OUT_DIR / "frontier.json"
DASHBOARD_DIR = OUT_DIR / "dashboard"
AUDIT_REPORT_DIR = _BACKEND_DIR / "audit_reports"

_EPS = 1e-9  # 阈值比较的浮点尾差容差（与 quality_loop 惯例一致）

# ── 冻结清单（§6.1 评估器外置）──────────────────────────────────────
# 进化对象永远只是 genome；以下文件（指标代码 / 黄金数据 / 评估策略）
# 不在变异面内，启动时逐文件校验 sha256，不符即中止。
FROZEN_FILES: list[str] = [
    "backend/scripts/quality_dashboard.py",
    "backend/scripts/quality_loop.py",
    "backend/src/utils/topology_metrics.py",
    "backend/scripts/evolve/eval_policy.yaml",
]
FROZEN_GLOBS: list[str] = [
    "backend/tests/fixtures/golden_standard_*.json",
]

# ── 指标向量口径（GATE/ARCHIVE 共用）────────────────────────────────
# higher=越大越好，lower=越小越好。键为拍平后的 dotted 路径：
# 全局指标无前缀，分小说指标带 <slug>. 前缀（Eevee 分小说记账）。
METRIC_DIRECTION: dict[str, str] = {
    "golden.pass_rate": "higher",
    "m6.shuihu_subtype_accuracy": "higher",
    "m6.xiyouji_mock_category": "higher",
}
PER_NOVEL_METRICS: dict[str, str] = {
    "m1.orphan_rate": "lower",
    "m2.recall_proxy": "higher",
    "m3.direction_error_rate": "lower",
    "m4.generic_residue": "lower",
    "m5.m5": "higher",
    "satisfaction": "higher",
    # 阶段 1 预注册（见 eval_policy v1 / README）：supplement 字典覆盖率指标
    "geo.unresolved_rate": "lower",
}

# ── 变异算子注册表（PROPOSE 接口）────────────────────────────────────
# 阶段 1+ 在这里注册真实算子；签名：(genome, context) -> Candidate dict。
OPERATORS: dict[str, object] = {}


def identity_mutation(genome: dict, context: dict) -> dict:
    """恒等变异（阶段 0 唯一算子）：空 diff，genome 不变。"""
    return {
        "operator": "identity",
        "hypothesis": "恒等变异：验证循环骨架空转，指标应与基线一致。",
        "genome_diff": {},
    }


OPERATORS["identity"] = identity_mutation


# ── LLM 预算真实计数（§4.2/§6.4）────────────────────────────────────

class LlmBudgetExceeded(RuntimeError):
    """单代 LLM 调用数超 eval_policy 预算；该代记失败变异。"""


class LlmBudget:
    """EVAL 路径 LLM 调用计数器：每次调用前 charge()，超限即抛。

    阶段 1 的 EVAL 全为规则路径（golden pytest 子进程 + geo 度量子进程），
    实测每代 0 次；后续阶段的 LLM 提议器/judge 在调用点接 charge() 即用。
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.calls = 0

    def charge(self, n: int = 1) -> None:
        self.calls += n
        if self.calls > self.limit:
            raise LlmBudgetExceeded(
                f"LLM 调用 {self.calls} 次超过每代预算 {self.limit}"
            )


# ── 阶段 1 EVAL：子进程重算 geo 指标（fresh import 加载候选 delta）────

COMPUTE_GEO_SCRIPT = _EVOLVE_DIR / "compute_geo_metrics.py"


def compute_geo_metrics_subprocess(timeout: int = 300) -> dict[str, dict]:
    """子进程跑 compute_geo_metrics.py，返回 {slug: {names,resolved,unresolved_rate}}。"""
    import subprocess

    proc = subprocess.run(
        [str(_BACKEND_DIR / ".venv" / "bin" / "python"), str(COMPUTE_GEO_SCRIPT)],
        cwd=_BACKEND_DIR, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"compute_geo_metrics 子进程失败: {proc.stderr[-500:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ── 配置加载与校验（手写校验函数，不引新依赖）───────────────────────

class ConfigError(ValueError):
    """genome / eval_policy 结构校验失败。"""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def validate_genome(data: dict) -> dict:
    """校验 genome.yaml 结构完整性（五级基因位齐全、每位有 current 字段）。"""
    _require(isinstance(data, dict), "genome 顶层必须是 mapping")
    _require("version" in data, "genome 缺 version")
    genes = data.get("genes")
    _require(isinstance(genes, dict), "genome 缺 genes")
    expected_levels = {"vocab_dict": 1, "weights_params": 2, "prompts": 3,
                       "pipeline": 4, "model": 5}
    for name, level in expected_levels.items():
        grp = genes.get(name)
        _require(isinstance(grp, dict), f"genes.{name} 缺失或不是 mapping")
        _require(grp.get("level") == level, f"genes.{name}.level 应为 {level}")
        loci = grp.get("loci")
        _require(isinstance(loci, dict) and loci, f"genes.{name}.loci 为空")
        for locus_name, locus in loci.items():
            _require(isinstance(locus, dict), f"genes.{name}.loci.{locus_name} 不是 mapping")
            _require("current" in locus, f"genes.{name}.loci.{locus_name} 缺 current")
    return data


def validate_eval_policy(data: dict) -> dict:
    """校验 eval_policy.yaml 结构（阈值/预算/数据集/Pareto 配置齐全且合理）。"""
    _require(isinstance(data, dict), "eval_policy 顶层必须是 mapping")
    ds = data.get("datasets", {})
    _require(isinstance(ds.get("inner", {}).get("novels"), list) and ds["inner"]["novels"],
             "datasets.inner.novels 为空")
    _require(isinstance(ds.get("holdout", {}).get("novels"), list) and ds["holdout"]["novels"],
             "datasets.holdout.novels 为空")
    th = data.get("thresholds", {})
    for key in ("metric_regression_abs", "per_novel_regression_abs", "golden_pass_rate_min"):
        _require(isinstance(th.get(key), (int, float)), f"thresholds.{key} 缺失或不是数值")
    _require(th["metric_regression_abs"] > 0, "metric_regression_abs 必须为正")
    pareto = data.get("pareto", {})
    _require(isinstance(pareto.get("population_max"), int) and pareto["population_max"] >= 1,
             "pareto.population_max 缺失或 <1")
    budget = data.get("budget", {})
    _require(isinstance(budget.get("wall_clock_seconds_per_generation"), (int, float)),
             "budget.wall_clock_seconds_per_generation 缺失")
    return data


def load_yaml_config(path: Path, validator) -> dict:
    if not path.exists():
        sys.exit(f"FATAL: 配置文件不存在: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        sys.exit(f"FATAL: {path.name} 解析失败: {err}")
    try:
        return validator(data)
    except ConfigError as err:
        sys.exit(f"FATAL: {path.name} 校验失败: {err}")


# ── 冻结清单（生成 + 校验）─────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frozen_file_list(repo_root: Path = _REPO_ROOT) -> list[str]:
    """展开 FROZEN_FILES + FROZEN_GLOBS 为排序后的相对路径列表。"""
    files = list(FROZEN_FILES)
    for pattern in FROZEN_GLOBS:
        files.extend(sorted(glob.glob(str(repo_root / pattern))))
    # glob 返回绝对路径，转回仓库相对路径
    rel = []
    for f in files:
        p = Path(f)
        rel.append(str(p.relative_to(repo_root)) if p.is_absolute() else f)
    return sorted(set(rel))


def build_manifest(repo_root: Path = _REPO_ROOT) -> dict:
    """生成 sha256 清单（缺文件即失败，防静默漏冻结）。"""
    files: dict[str, str] = {}
    for rel in frozen_file_list(repo_root):
        p = repo_root / rel
        if not p.exists():
            sys.exit(f"FATAL: 冻结清单目标不存在: {rel}")
        files[rel] = _sha256_file(p)
    return {
        "version": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def write_manifest(path: Path = FROZEN_MANIFEST_PATH, repo_root: Path = _REPO_ROOT) -> dict:
    manifest = build_manifest(repo_root)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return manifest


def verify_manifest(manifest_path: Path = FROZEN_MANIFEST_PATH,
                    repo_root: Path = _REPO_ROOT) -> list[str]:
    """校验冻结清单，返回失配文件列表（空 = 通过）。§6.1：不符即中止。"""
    if not manifest_path.exists():
        return [f"<manifest missing: {manifest_path.name}>"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        return [f"<manifest corrupt: {err}>"]
    recorded = manifest.get("files", {})
    expected = frozen_file_list(repo_root)
    mismatched: list[str] = []
    for rel in expected:
        p = repo_root / rel
        if rel not in recorded:
            mismatched.append(f"{rel} (未入清单)")
        elif not p.exists():
            mismatched.append(f"{rel} (文件缺失)")
        elif _sha256_file(p) != recorded[rel]:
            mismatched.append(f"{rel} (sha256 不符)")
    for rel in recorded:
        if rel not in expected:
            mismatched.append(f"{rel} (清单冗余项)")
    return mismatched


def check_frozen_or_abort() -> None:
    mismatched = verify_manifest()
    if mismatched:
        lines = "\n".join(f"  - {m}" for m in mismatched)
        sys.exit(
            "FATAL: 冻结清单校验失败（§6.1 评估器外置，防 reward hacking）:\n"
            f"{lines}\n如确为有意更新评估器/黄金数据/策略，请人工核对后运行 --freeze 重新生成。"
        )


# ── 指标向量 ────────────────────────────────────────────────────────

def direction_for(key: str) -> str | None:
    """查指标更优方向；分小说键走 PER_NOVEL_METRICS 后缀匹配。"""
    if key in METRIC_DIRECTION:
        return METRIC_DIRECTION[key]
    parts = key.split(".", 1)
    if len(parts) == 2 and parts[1] in PER_NOVEL_METRICS:
        return PER_NOVEL_METRICS[parts[1]]
    return None


def metric_vector_from_baseline(baseline: dict) -> dict[str, float]:
    """从 baseline.json 提取拍平指标向量（只收有方向口径的键）。"""
    vec: dict[str, float] = {}
    golden = baseline.get("golden", {})
    if isinstance(golden.get("pass_rate"), (int, float)):
        vec["golden.pass_rate"] = float(golden["pass_rate"])
    m6 = baseline.get("m6", {})
    for key in ("shuihu_subtype_accuracy", "xiyouji_mock_category"):
        if isinstance(m6.get(key), (int, float)):
            vec[f"m6.{key}"] = float(m6[key])
    for slug, entry in baseline.get("novels", {}).items():
        for mkey in PER_NOVEL_METRICS:
            val = entry.get("metrics", {}).get(mkey)
            if isinstance(val, (int, float)):
                vec[f"{slug}.{mkey}"] = float(val)
    return vec


# ── ANALYZE ─────────────────────────────────────────────────────────

def _load_jsonl_tail(path: Path, n: int) -> list[dict]:
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
    return records[-n:]


def analyze(context_tail: int = 5) -> dict:
    """ANALYZE：读上轮产物，产出结构化上下文摘要（纯读，不改任何东西）。"""
    # 最新 hierarchy diff（错误分类输入）
    diffs = sorted(AUDIT_REPORT_DIR.glob("hierarchy_diff_*.json"))
    diff_summary = None
    if diffs:
        latest = diffs[-1]
        try:
            d = json.loads(latest.read_text(encoding="utf-8"))
            diff_summary = {
                "file": latest.name,
                "top_level_keys": sorted(d.keys())[:20],
            }
            # 尽量带上错误分类计数（各 diff 结构不一，宽容提取）
            for key in ("summary", "stats", "counts"):
                if isinstance(d.get(key), dict):
                    diff_summary[key] = d[key]
                    break
        except json.JSONDecodeError:
            diff_summary = {"file": latest.name, "error": "JSON 解析失败"}

    # quality_history 趋势尾部
    history_tail = [
        {
            "timestamp": r.get("timestamp"),
            "tag": r.get("tag"),
            "golden_pass_rate": (r.get("golden") or {}).get("pass_rate"),
            "golden_status": (r.get("golden") or {}).get("status"),
        }
        for r in _load_jsonl_tail(AUDIT_REPORT_DIR / "quality_history.jsonl", context_tail)
    ]

    # journal 失败史（近几代的决策）
    journal_tail = [
        {
            "generation": r.get("generation"),
            "operator": r.get("operator"),
            "decision": r.get("decision"),
        }
        for r in _load_jsonl_tail(JOURNAL_PATH, context_tail)
    ]

    return {
        "latest_hierarchy_diff": diff_summary,
        "quality_history_tail": history_tail,
        "journal_tail": journal_tail,
    }


# ── PROPOSE / APPLY ─────────────────────────────────────────────────

def propose(genome: dict, context: dict, operator_names: list[str] | None = None,
            max_candidates: int = 3) -> list[dict]:
    """PROPOSE：从算子注册表产出 1~3 个候选变异（阶段 0 只有 identity）。"""
    names = operator_names or ["identity"]
    candidates = []
    for name in names[:max_candidates]:
        op = OPERATORS.get(name)
        if op is None:
            print(f"[evolve][propose] 未注册的算子: {name}，跳过")
            continue
        candidates.append(op(genome, context))
    return candidates


def apply_mutation(genome: dict, genome_diff: dict) -> dict:
    """APPLY：在隔离副本上应用变异（不动原 genome；阶段 0 空 diff = no-op）。

    后续阶段的隔离应用（工作区/特性开关注入）在本函数内扩展，
    约束不变：返回新 genome 对象，输入对象不被修改。
    """
    new_genome = copy.deepcopy(genome)
    for path, value in genome_diff.items():  # 空 diff 时循环不执行
        _set_dotted(new_genome, path, value)
    return new_genome


def _set_dotted(obj: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


# ── EVAL ────────────────────────────────────────────────────────────

def evaluate_cached(baseline_path: Path = BASELINE_PATH) -> dict:
    """EVAL/cached：直接读 baseline.json，不重复花钱（dry-run 默认）。"""
    if not baseline_path.exists():
        sys.exit(f"FATAL: 基线不存在: {baseline_path}（先运行 build_baseline.py）")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    return {
        "backend": "cached",
        "metrics": metric_vector_from_baseline(baseline),
        "cost": {"wall_clock_s": 0.0, "llm_calls": 0, "cost_usd": 0.0},
        "baseline_measured_at": baseline.get("measured_at"),
    }


def evaluate_live(generation: int, no_pytest: bool = False) -> dict:
    """EVAL/live：import quality_loop 跑一轮门禁 + 汇总已有 dashboard 产物。

    golden pytest 门禁由 quality_loop subprocess 跑；M1-M4/satisfaction 不重算
    （依赖冻结 DB），直接读 out/dashboard 已有产物与 baseline.json。
    """
    t0 = time.monotonic()
    import quality_loop as ql

    record, _prev, rows, exit_code = ql.run_loop(
        tag=f"evolve-g{generation}", no_pytest=no_pytest,
    )
    vec: dict[str, float] = {}
    golden = record.get("golden", {})
    if isinstance(golden.get("pass_rate"), (int, float)):
        vec["golden.pass_rate"] = float(golden["pass_rate"])
    m6 = record.get("m6", {})
    for key in ("shuihu_subtype_accuracy", "xiyouji_mock_category"):
        if isinstance(m6.get(key), (int, float)):
            vec[f"m6.{key}"] = float(m6[key])
    m5 = record.get("m5", {})
    for slug, entry in m5.items():
        if isinstance(entry, dict) and isinstance(entry.get("m5"), (int, float)):
            vec[f"{slug}.m5.m5"] = float(entry["m5"])
    # 分小说其余维度来自 baseline/dashboard 产物（live 模式不重算 M1-M4）
    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        for key, val in metric_vector_from_baseline(baseline).items():
            vec.setdefault(key, val)
    hard_fails = [r for r in rows if r.get("verdict") == "fail"]
    return {
        "backend": "live",
        "metrics": vec,
        "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                 "llm_calls": 0, "cost_usd": 0.0},
        "quality_loop_exit_code": exit_code,
        "quality_loop_hard_fails": [r.get("key") for r in hard_fails],
    }


# ── GATE ────────────────────────────────────────────────────────────

def gate(candidate_vec: dict[str, float], reference_vec: dict[str, float],
         policy: dict) -> dict:
    """GATE：对照 eval_policy 阈值判定回归（纯函数）。

    判定规则（§4.3）：
      - 冻结指标单项回归 > metric_regression_abs（绝对值）即拒
      - 分小说记账退化 > per_novel_regression_abs 即拒
      - golden.pass_rate 跌破 golden_pass_rate_min 即拒
      - 参考向量里缺失的键不参与判定（记 missing，不算回归）
    """
    th = policy["thresholds"]
    metric_thr = float(th["metric_regression_abs"])
    novel_thr = float(th["per_novel_regression_abs"])
    golden_min = float(th["golden_pass_rate_min"])

    rows: list[dict] = []
    passed = True
    for key in sorted(set(candidate_vec) | set(reference_vec)):
        direction = direction_for(key)
        cur, ref = candidate_vec.get(key), reference_vec.get(key)
        if direction is None or cur is None or ref is None:
            rows.append({"key": key, "ref": ref, "curr": cur, "delta": None,
                         "verdict": "missing" if cur is None or ref is None else "info"})
            continue
        delta = cur - ref
        # 全局键（METRIC_DIRECTION 直查命中）用单项阈值；分小说键用分小说阈值
        is_per_novel = key not in METRIC_DIRECTION
        thr = novel_thr if is_per_novel else metric_thr
        # ">阈值 即拒" 为严格大于；加 _EPS 吸收浮点尾差（恰好压线不算回归，
        # 与 quality_loop 的 _EPS 惯例一致）
        regressed = (direction == "higher" and (ref - cur) - thr > _EPS) or \
                    (direction == "lower" and (cur - ref) - thr > _EPS)
        verdict = "fail" if regressed else "ok"
        if regressed:
            passed = False
        rows.append({"key": key, "ref": ref, "curr": cur, "delta": delta,
                     "verdict": verdict})
    golden = candidate_vec.get("golden.pass_rate")
    if golden is not None and golden < golden_min:
        passed = False
        rows.append({"key": "golden.pass_rate", "ref": golden_min, "curr": golden,
                     "delta": None, "verdict": "fail",
                     "note": f"golden pass_rate 跌破硬阈值 {golden_min}"})
    return {"passed": passed, "rows": rows,
            "failures": [r["key"] for r in rows if r["verdict"] == "fail"]}


# ── ARCHIVE（Pareto 前沿）───────────────────────────────────────────

def _norm(vec: dict[str, float]) -> dict[str, float]:
    """按方向归一化为"越大越好"的得分，供支配比较。"""
    out = {}
    for key, val in vec.items():
        direction = direction_for(key)
        if direction is None:
            continue
        out[key] = val if direction == "higher" else -val
    return out


def dominates(a_vec: dict[str, float], b_vec: dict[str, float]) -> bool:
    """a 支配 b：交集键上 a 全部不差且至少一维严格更好（纯函数）。"""
    na, nb = _norm(a_vec), _norm(b_vec)
    common = sorted(set(na) & set(nb))
    if not common:
        return False
    return (all(na[k] >= nb[k] for k in common)
            and any(na[k] > nb[k] for k in common))


def archive_candidate(frontier: list[dict], candidate: dict,
                      population_max: int) -> tuple[list[dict], str]:
    """ARCHIVE：Pareto 前沿更新（纯函数）。返回 (新前沿, 决策)。

    决策取值：
      rejected_dominated      被现任支配 → 拒绝
      rejected_no_improvement 质量不降但无一维严格改善（JIT-Agent 入档规则）
      archived                入档（支配现任则剔除被支配者；互不支配共存）
      archived_evicted        入档但因种群上限淘汰了最老被支配者
    candidate 需带 metrics / generation 键；reference_vec 为其父代向量。
    """
    cvec = candidate["metrics"]
    ref = candidate.get("parent_metrics") or {}

    if any(dominates(inc["metrics"], cvec) for inc in frontier):
        return frontier, "rejected_dominated"

    # JIT-Agent 入档规则：相对父代质量不降且至少一维严格改善
    nc, nr = _norm(cvec), _norm(ref)
    common = sorted(set(nc) & set(nr))
    strictly_better = [k for k in common if nc[k] > nr[k]]
    worse = [k for k in common if nc[k] < nr[k]]
    if worse or not strictly_better:
        return frontier, "rejected_no_improvement"

    new_frontier = [inc for inc in frontier if not dominates(cvec, inc["metrics"])]
    entry = {
        "generation": candidate["generation"],
        "operator": candidate.get("operator"),
        "genome_diff": candidate.get("genome_diff", {}),
        "metrics": cvec,
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    new_frontier.append(entry)

    decision = "archived"
    if len(new_frontier) > population_max:
        # 淘汰最老的被支配者；互不支配全共存时淘汰最老条目
        dominated_idx = next(
            (i for i, inc in enumerate(new_frontier[:-1])
             if any(dominates(other["metrics"], inc["metrics"])
                    for j, other in enumerate(new_frontier) if j != i)),
            0,
        )
        new_frontier.pop(dominated_idx)
        decision = "archived_evicted"
    return new_frontier, decision


def load_frontier(path: Path = FRONTIER_PATH) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data.get("frontier", []) if isinstance(data, dict) else []


def save_frontier(frontier: list[dict], path: Path = FRONTIER_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 0, "frontier": frontier}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


# ── COMMIT（journal）───────────────────────────────────────────────

def commit_journal(record: dict, journal_path: Path = JOURNAL_PATH) -> Path:
    """COMMIT：追加 evolution_journal.jsonl（每代一条完整 lineage）。"""
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return journal_path


def next_generation(journal_path: Path = JOURNAL_PATH) -> int:
    tail = _load_jsonl_tail(journal_path, n=10_000)
    if not tail:
        return 0
    return max(int(r.get("generation", -1)) for r in tail) + 1


# ── REPORT ──────────────────────────────────────────────────────────

def render_report(journal_path: Path = JOURNAL_PATH,
                  frontier_path: Path = FRONTIER_PATH) -> str:
    """REPORT：当前前沿 + 趋势（可读文本）。"""
    records = _load_jsonl_tail(journal_path, n=10_000)
    frontier = load_frontier(frontier_path)
    lines = [
        "# GeoEvolve 进化报告",
        "",
        f"- journal 记录数: {len(records)}",
        f"- 当前前沿大小: {len(frontier)}",
    ]
    if records:
        decisions: dict[str, int] = {}
        for r in records:
            decisions[r.get("decision", "?")] = decisions.get(r.get("decision", "?"), 0) + 1
        lines.append("- 决策分布: " + ", ".join(f"{k}={v}" for k, v in sorted(decisions.items())))
        lines.append("")
        lines.append("## 近几代趋势")
        lines.append("")
        lines.append("| 代 | 算子 | 决策 | 成本(USD) | 耗时(s) |")
        lines.append("|---|---|---|---|---|")
        for r in records[-10:]:
            cost = r.get("cost", {})
            lines.append(
                f"| {r.get('generation')} | {r.get('operator')} | {r.get('decision')} "
                f"| {cost.get('cost_usd', 0)} | {cost.get('wall_clock_s', 0)} |"
            )
    if frontier:
        lines += ["", "## Pareto 前沿", ""]
        for entry in frontier:
            n_improved = sum(1 for k, v in entry.get("metrics", {}).items() if v is not None)
            lines.append(
                f"- gen{entry.get('generation')} ({entry.get('operator')}): "
                f"{n_improved} 维指标，入档于 {entry.get('archived_at')}"
            )
    lines.append("")
    return "\n".join(lines)


# ── 主循环 ──────────────────────────────────────────────────────────

def run_evolution(generations: int, eval_backend: str, dry_run: bool,
                  no_pytest: bool = False, verbose: bool = True) -> int:
    """进化主循环：ANALYZE→PROPOSE→APPLY→EVAL→GATE→ARCHIVE→COMMIT→REPORT。"""
    check_frozen_or_abort()  # §6.1 评估器外置

    genome = load_yaml_config(GENOME_PATH, validate_genome)
    policy = load_yaml_config(EVAL_POLICY_PATH, validate_eval_policy)
    budget_s = float(policy["budget"]["wall_clock_seconds_per_generation"])
    pop_max = int(policy["pareto"]["population_max"])

    if dry_run:
        eval_backend = policy.get("dry_run", {}).get("eval_backend", "cached")

    if not BASELINE_PATH.exists():
        sys.exit(f"FATAL: 基线不存在: {BASELINE_PATH}（先运行 build_baseline.py）")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_vec = metric_vector_from_baseline(baseline)

    frontier = load_frontier()
    gen0 = next_generation()
    no_improve_streak = 0
    pause_after = int(policy.get("guardrails", {})
                      .get("no_improvement_pause_generations", 5))

    for i in range(generations):
        generation = gen0 + i
        t0 = time.monotonic()
        print(f"\n[evolve] ══ generation {generation} ══")

        # 1. ANALYZE
        context = analyze()
        if verbose:
            diff = context.get("latest_hierarchy_diff") or {}
            print(f"[evolve][analyze] 最新层级 diff: {diff.get('file', '无')}; "
                  f"history 尾部 {len(context['quality_history_tail'])} 条; "
                  f"journal 尾部 {len(context['journal_tail'])} 条")

        # 2. PROPOSE
        candidates = propose(genome, context)
        if not candidates:
            print("[evolve][propose] 无候选，本轮跳过")
            continue
        candidate = candidates[0]  # 阶段 0 每轮只评第一个候选
        print(f"[evolve][propose] 算子={candidate['operator']} 假设: {candidate['hypothesis']}")

        # 3. APPLY（隔离副本，no-op for identity）
        mutated_genome = apply_mutation(genome, candidate["genome_diff"])

        # 4. EVAL
        if eval_backend == "live":
            result = evaluate_live(generation, no_pytest=no_pytest)
        else:
            result = evaluate_cached()
        vec = result["metrics"]
        cost = result["cost"]
        cost["wall_clock_s"] = round(time.monotonic() - t0, 3)
        over_budget = cost["wall_clock_s"] > budget_s
        print(f"[evolve][eval] backend={result['backend']} 指标 {len(vec)} 维 "
              f"耗时 {cost['wall_clock_s']}s（预算 {budget_s:.0f}s）"
              + (" ⚠️超预算，记失败变异" if over_budget else ""))

        # 5. GATE（对照基线向量；分小说阈值在 gate 内按键区分）
        gate_result = gate(vec, baseline_vec, policy)
        gate_passed = gate_result["passed"] and not over_budget
        print(f"[evolve][gate] {'通过' if gate_passed else '拒绝'} "
              f"(failures: {gate_result['failures'] or '无'})")

        # 6. ARCHIVE
        if gate_passed:
            candidate_entry = {
                "generation": generation,
                "operator": candidate["operator"],
                "genome_diff": candidate["genome_diff"],
                "metrics": vec,
                "parent_metrics": baseline_vec,
            }
            frontier, decision = archive_candidate(frontier, candidate_entry, pop_max)
            save_frontier(frontier)
        else:
            decision = "rejected_gate"
        print(f"[evolve][archive] 决策: {decision} (前沿大小 {len(frontier)}/{pop_max})")

        # 7. COMMIT（journal lineage；不打 git commit，由人决定）
        record = {
            "generation": generation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator": candidate["operator"],
            "hypothesis": candidate["hypothesis"],
            "genome_diff": candidate["genome_diff"],
            "genome_after": mutated_genome if candidate["genome_diff"] else None,
            "eval_backend": result["backend"],
            "metrics": vec,
            "gate": {"passed": gate_passed, "failures": gate_result["failures"]},
            "cost": cost,
            "parent": {"type": "baseline", "measured_at": baseline.get("measured_at")},
            "decision": decision,
            "dry_run": dry_run,
        }
        commit_journal(record)

        # 8. REPORT（每轮摘要）
        n_ok = sum(1 for r in gate_result["rows"] if r["verdict"] == "ok")
        print(f"[evolve][report] gen{generation} 完成: 指标 ok={n_ok} "
              f"fail={len(gate_result['failures'])} 决策={decision}")

        # §6.4 反漂移：连续 N 代无改进 → 自动暂停并输出诊断
        if decision.startswith("archived"):
            no_improve_streak = 0
        else:
            no_improve_streak += 1
        if no_improve_streak >= pause_after:
            print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。"
                  f"诊断: 最近决策见 journal; 建议人工检视 PROPOSE 算子有效性。")
            break

    print("\n[evolve] 循环结束。")
    print(render_report())
    return 0


# ── 阶段 1：词表/字典级 live 进化（ACE 模式）─────────────────────────

def _eval_stage1_state(baseline_vec: dict[str, float], policy: dict,
                       llm_budget: LlmBudget,
                       no_pytest: bool = False) -> dict:
    """阶段 1 EVAL：子进程重算 geo.unresolved_rate + golden 门禁；其余沿用基线。

    返回 {metrics, cost, golden}。LLM 调用计数经 llm_budget（规则路径恒 0，
    任何未来接入的 LLM 评估步骤必须先 llm_budget.charge()）。
    """
    t0 = time.monotonic()
    geo = compute_geo_metrics_subprocess()  # fresh import，加载当前源文件状态
    if no_pytest:
        golden = {"status": "skipped"}
    else:
        import quality_loop as ql

        golden = ql.run_golden_gate(timeout=300)
    vec = dict(baseline_vec)  # M1-M6/satisfaction 不受 geo 字典影响，沿用基线缓存
    for slug, m in geo.items():
        if isinstance(m.get("unresolved_rate"), (int, float)):
            vec[f"{slug}.geo.unresolved_rate"] = float(m["unresolved_rate"])
    if isinstance(golden.get("pass_rate"), (int, float)):
        vec["golden.pass_rate"] = float(golden["pass_rate"])
    return {
        "metrics": vec,
        "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                 "llm_calls": llm_budget.calls, "cost_usd": 0.0},
        "golden": golden,
        "geo": geo,
    }


def run_evolution_stage1(generations: int, no_pytest: bool = False,
                         batch_size: int = 10, verbose: bool = True) -> int:
    """阶段 1 主循环：ACE 词表增量 + 真实评估 + finally 回退 + 崩溃自愈。"""
    check_frozen_or_abort()  # §6.1 评估器外置

    import geo_vocab as gv

    genome = load_yaml_config(GENOME_PATH, validate_genome)
    policy = load_yaml_config(EVAL_POLICY_PATH, validate_eval_policy)
    budget_s = float(policy["budget"]["wall_clock_seconds_per_generation"])
    llm_limit = int(policy["budget"].get("llm_calls_per_generation", 100))
    pop_max = int(policy["pareto"]["population_max"])

    if not BASELINE_PATH.exists():
        sys.exit(f"FATAL: 基线不存在: {BASELINE_PATH}（先运行 build_baseline.py）")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_vec = metric_vector_from_baseline(baseline)

    store = gv.load_delta()
    if gv.heal_source(store):
        print("[evolve] 检测到源文件偏离已提交 delta 状态（上次崩溃残留？），已重渲染自愈")
    operator = gv.GeoSupplementDeltaOperator(batch_size=batch_size)
    golden_text = gv.load_golden_texts()

    frontier = load_frontier()
    gen0 = next_generation()
    pause_after = int(policy.get("guardrails", {})
                      .get("no_improvement_pause_generations", 5))

    # 起始 committed 状态向量（父代）：当前源文件状态 + golden
    print("[evolve] 测量已提交状态基线向量（父代）...")
    committed_eval = _eval_stage1_state(baseline_vec, policy, LlmBudget(llm_limit),
                                        no_pytest=no_pytest)
    parent_vec = committed_eval["metrics"]
    parent_ref: dict = {"type": "baseline", "measured_at": baseline.get("measured_at")}
    print(f"[evolve] 父代向量 {len(parent_vec)} 维；已提交 delta "
          f"{len(store['entries'])} 条")

    no_improve_streak = 0
    for i in range(generations):
        generation = gen0 + i
        t0 = time.monotonic()
        llm_budget = LlmBudget(llm_limit)
        print(f"\n[evolve] ══ generation {generation} ══")

        # 1. ANALYZE（上下文摘要 + 候选池快照）
        context = analyze()
        pools = operator.build_pools(store)
        context["pools"] = pools
        if verbose:
            pool_str = " ".join(f"{s}:{p['pool_size']}" for s, p in pools.items())
            print(f"[evolve][analyze] 候选池: {pool_str}; "
                  f"已提交 {len(store['entries'])} 条 / 黑名单 {len(store.get('rejected', {}))} 条")

        # 2. PROPOSE（规则算子）
        candidate = operator(genome, context)
        print(f"[evolve][propose] {candidate['hypothesis']}")
        if candidate.get("exhausted"):
            print("[evolve][propose] 候选池穷尽，如实停止（未凑满轮数）。")
            break
        add = candidate["genome_diff"]["vocab_delta.add"]

        # anti-hack（§6.3）：黄金集原文包含检测
        kept, ah_rejected = gv.anti_hack_filter(add, golden_text)
        if ah_rejected:
            print(f"[evolve][anti-hack] 剔除 {len(ah_rejected)} 条命中 golden fixture 的条目: "
                  f"{ah_rejected}")
            gv.mark_rejected(store, ah_rejected, "anti-hack: 命中 golden fixture 原文")
        if not kept:
            record = {
                "generation": generation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operator": candidate["operator"],
                "hypothesis": candidate["hypothesis"],
                "genome_diff": candidate["genome_diff"],
                "eval_backend": "live",
                "metrics": None,
                "gate": {"passed": False, "failures": ["anti_hack_all_rejected"]},
                "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                         "llm_calls": 0, "cost_usd": 0.0},
                "parent": parent_ref,
                "decision": "rejected_anti_hack",
                "anti_hack_rejected": ah_rejected,
                "dry_run": False,
                "stage": 1,
            }
            commit_journal(record)
            no_improve_streak += 1
            if no_improve_streak >= pause_after:
                print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。")
                break
            continue

        # 3. APPLY（渲染候选状态；finally 保证回退）
        gen_parent_ref = parent_ref  # 本代父代指针（谱系用，接受后再前移）
        prev_parent_vec = parent_vec
        committed = gv.committed_coords(store)
        candidate_state = dict(committed)
        candidate_state.update({n: tuple(c) for n, c in kept.items()})
        gv.write_source_state(candidate_state)
        decision = "failed_error"
        vec: dict | None = None
        gate_result = {"passed": False, "failures": ["eval_error"]}
        cost = {"wall_clock_s": 0.0, "llm_calls": 0, "cost_usd": 0.0}
        error: str | None = None
        try:
            # 4. EVAL（子进程 fresh import 候选状态）
            result = _eval_stage1_state(baseline_vec, policy, llm_budget,
                                        no_pytest=no_pytest)
            vec = result["metrics"]
            cost = result["cost"]
            cost["wall_clock_s"] = round(time.monotonic() - t0, 3)
            over_budget = cost["wall_clock_s"] > budget_s
            print(f"[evolve][eval] 指标 {len(vec)} 维 耗时 {cost['wall_clock_s']}s"
                  f"（预算 {budget_s:.0f}s）llm_calls={cost['llm_calls']}"
                  + (" ⚠️超 wall-clock 预算，记失败变异" if over_budget else ""))

            # 5. GATE（对照父代向量）
            gate_result = gate(vec, parent_vec, policy)
            gate_passed = gate_result["passed"] and not over_budget
            if over_budget:
                gate_result["failures"] = gate_result["failures"] + ["wall_clock_budget"]
            print(f"[evolve][gate] {'通过' if gate_passed else '拒绝'} "
                  f"(failures: {gate_result['failures'] or '无'})")

            # 6. ARCHIVE
            if gate_passed:
                entry = {
                    "generation": generation,
                    "operator": candidate["operator"],
                    "genome_diff": candidate["genome_diff"],
                    "metrics": vec,
                    "parent_metrics": parent_vec,
                }
                frontier, decision = archive_candidate(frontier, entry, pop_max)
                save_frontier(frontier)
            else:
                decision = "rejected_gate"
        except LlmBudgetExceeded as err:
            error = str(err)
            decision = "failed_llm_budget"
            gate_result = {"passed": False, "failures": ["llm_budget"]}
            print(f"[evolve][eval] ❌ {error}，该代记失败变异")
        except Exception as err:  # 评估异常：回退后记失败，不中断无人值守循环
            error = f"{type(err).__name__}: {err}"
            gate_result = {"passed": False, "failures": ["eval_error"]}
            print(f"[evolve][eval] ❌ 评估异常: {error}，该代记失败变异")
        finally:
            if decision.startswith("archived"):
                # 接受：delta 落盘（文件已是新提交状态）；父代指针前移
                ancestors = candidate.get("ancestors", {})
                freqs = candidate.get("frequencies", {})
                for n, c in kept.items():
                    store["entries"][n] = {
                        "coords": list(c),
                        "novel": candidate["target_novel"],
                        "ancestor": ancestors.get(n),
                        "frequency": freqs.get(n, 0),
                        "generation": generation,
                    }
                gv.save_delta(store)
                parent_vec = vec
                parent_ref = {"type": "generation", "generation": generation}
            else:
                # 拒绝/失败：完全还原到已提交状态（含异常路径）
                gv.write_source_state(committed)

        print(f"[evolve][archive] 决策: {decision} (前沿大小 {len(frontier)}/{pop_max})")

        # 7. COMMIT（journal 完整 lineage）
        record = {
            "generation": generation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator": candidate["operator"],
            "hypothesis": candidate["hypothesis"],
            "genome_diff": {"vocab_delta.add": kept,
                            "target_novel": candidate["target_novel"]},
            "eval_backend": "live",
            "metrics": vec,
            "gate": {"passed": gate_result["passed"],
                     "failures": gate_result["failures"]},
            "cost": cost,
            "parent": gen_parent_ref,
            "decision": decision,
            "anti_hack_rejected": ah_rejected,
            "error": error,
            "dry_run": False,
            "stage": 1,
        }
        commit_journal(record)

        # 8. REPORT（每轮摘要）
        if vec is not None and prev_parent_vec is not None:
            tgt = candidate["target_novel"]
            key = f"{tgt}.geo.unresolved_rate"
            print(f"[evolve][report] gen{generation}: {key} "
                  f"{prev_parent_vec.get(key)} → {vec.get(key)} 决策={decision}")
        else:
            print(f"[evolve][report] gen{generation}: 决策={decision}")

        # §6.4 反漂移护栏
        if decision.startswith("archived"):
            no_improve_streak = 0
        else:
            no_improve_streak += 1
        if no_improve_streak >= pause_after:
            print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。"
                  f"诊断: 最近决策见 journal。")
            break

    print("\n[evolve] 循环结束。")
    print(render_report())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GeoEvolve 进化循环（阶段 0 骨架 / 阶段 1 词表级 ACE）",
        epilog="规格: docs/analysis/geo-self-evolve-methodology.md §4.3/§5/§6",
    )
    parser.add_argument("--stage", type=int, choices=[0, 1], default=0,
                        help="进化阶段：0=恒等变异骨架；1=词表/字典级 ACE live 进化")
    parser.add_argument("--generations", type=int, default=1, help="进化轮数")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="阶段 1 每代提议的 delta 条数")
    parser.add_argument("--dry-run", action="store_true",
                        help="空转模式：cached 评估后端 + 恒等变异，不花钱")
    parser.add_argument("--eval-backend", choices=["cached", "live"], default="cached",
                        help="评估后端：cached=读 baseline.json；live=跑 quality_loop 门禁")
    parser.add_argument("--no-pytest", action="store_true",
                        help="live 后端跳过 golden pytest 子集")
    parser.add_argument("--report", action="store_true", help="输出当前前沿与趋势后退出")
    parser.add_argument("--freeze", action="store_true",
                        help="重新生成 frozen_manifest.json（仅限有意更新评估器后）")
    args = parser.parse_args(argv)

    if args.freeze:
        manifest = write_manifest()
        print(f"[evolve] 冻结清单已重新生成: {FROZEN_MANIFEST_PATH} "
              f"({len(manifest['files'])} 个文件)")
        return 0
    if args.report:
        print(render_report())
        return 0
    if args.stage == 1:
        if args.dry_run or args.eval_backend == "cached":
            print("[evolve] 阶段 1 强制 live 评估（--dry-run/--eval-backend cached 仅阶段 0 有效）")
        return run_evolution_stage1(generations=args.generations,
                                    no_pytest=args.no_pytest,
                                    batch_size=args.batch_size)
    return run_evolution(generations=args.generations, eval_backend=args.eval_backend,
                         dry_run=args.dry_run, no_pytest=args.no_pytest)


if __name__ == "__main__":
    sys.exit(main())
