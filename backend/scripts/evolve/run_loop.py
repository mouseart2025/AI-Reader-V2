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
import asyncio
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
    # 阶段 3:prompt 级评估的冻结基准与提议器/judge prompt(§6.1 提议器不外置)
    "backend/scripts/evolve/fixtures/stage3_chapters.json",
    "backend/scripts/evolve/fixtures/stage3_t_set.json",
    "backend/scripts/evolve/fixtures/stage3_e0_baseline.json",
    "backend/scripts/evolve/prompts/propose_s3.txt",
    "backend/scripts/evolve/prompts/judge_spotcheck_s3.txt",
    "backend/scripts/evolve/prompts/extract_user_s3.txt",
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
    # 阶段 3:内层三本 prompt.recall 的宏均值(显著性判定主键,噪声底见 v3)
    "macro.prompt.recall": "higher",
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
    # 阶段 2 预注册（eval_policy v2）：rebuild 层级 vs golden 的拓扑指标（内层集）
    "topo.parent_precision": "higher",
    "topo.parent_recall": "higher",
    "topo.chain_accuracy": "higher",
    # 阶段 2 结构护栏（全五本；rebuild 后 orphan 恒 0，改用这两个）
    "rebuild.max_children": "lower",
    "rebuild.root_count": "lower",
    # 阶段 3 预注册（eval_policy v3）：冻结章节子集上的 prompt 敏感指标
    "prompt.recall": "higher",
    "prompt.count_inflation": "lower",
    "prompt.generic_rate": "lower",
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


# ── 阶段 2 EVAL：子进程注入参数重建层级并测拓扑指标 ───────────────────

COMPUTE_WEIGHT_SCRIPT = _EVOLVE_DIR / "compute_weight_metrics.py"
CANDIDATE_PARAMS_PATH = OUT_DIR / "candidate_params.json"
FRONTIER_STAGE2_PATH = OUT_DIR / "frontier_stage2.json"  # 阶段 2 独立前沿(目标空间不同,不与阶段 1 混档)

TOPO_KEYS = ("parent_precision", "parent_recall", "chain_accuracy")
STRUCT_KEYS = ("max_children", "root_count")


def compute_weight_metrics_subprocess(params_path: Path | None = None,
                                      permute_seed: int | None = None,
                                      timeout: int = 900) -> dict[str, dict]:
    """子进程跑 compute_weight_metrics.py（scratch 隔离 + 可选参数注入）。"""
    import subprocess

    cmd = [str(_BACKEND_DIR / ".venv" / "bin" / "python"), str(COMPUTE_WEIGHT_SCRIPT)]
    if params_path:
        cmd += ["--params", str(params_path)]
    if permute_seed is not None:
        cmd += ["--permute-chapters", str(permute_seed)]
    proc = subprocess.run(cmd, cwd=_BACKEND_DIR, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"compute_weight_metrics 子进程失败: {proc.stderr[-800:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def stage2_metric_vector(metrics: dict[str, dict]) -> dict[str, float]:
    """子进程输出 → 拍平指标向量（topo.* 内层三本 + rebuild.* 全五本护栏）。"""
    vec: dict[str, float] = {}
    for slug, m in metrics.items():
        for key in TOPO_KEYS:
            if isinstance(m.get(key), (int, float)):
                vec[f"{slug}.topo.{key}"] = float(m[key])
        for key in STRUCT_KEYS:
            if isinstance(m.get(key), (int, float)):
                vec[f"{slug}.rebuild.{key}"] = float(m[key])
    return vec


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

def regressed_beyond_threshold(key: str, cur: float, ref: float, policy: dict) -> bool:
    """单键回归判定（GATE 与 ARCHIVE/JIT 共用同一口径,纯函数）。

    口径:率型指标用 metric/per_novel_regression_abs;计数型结构护栏用
    thresholds.guard_overrides(relative=相对倍数 / abs=绝对阈值,按后缀匹配)。
    """
    direction = direction_for(key)
    if direction is None:
        return False
    th = policy["thresholds"]
    is_per_novel = key not in METRIC_DIRECTION
    thr = float(th["per_novel_regression_abs"] if is_per_novel
                else th["metric_regression_abs"])
    suffix = key.split(".", 1)[1] if is_per_novel and "." in key else key
    override = th.get("guard_overrides", {}).get(suffix, {})
    if "relative" in override and ref:
        rel = float(override["relative"])
        return (direction == "lower" and cur > ref * rel + _EPS) or \
               (direction == "higher" and cur < ref / rel - _EPS)
    if "abs" in override:
        thr = float(override["abs"])
    return (direction == "higher" and (ref - cur) - thr > _EPS) or \
           (direction == "lower" and (cur - ref) - thr > _EPS)


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
        # 与 ARCHIVE 同一口径:率型走 metric/per_novel 阈值,计数护栏走 guard_overrides
        regressed = regressed_beyond_threshold(key, cur, ref, policy)
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
                      population_max: int,
                      policy: dict | None = None) -> tuple[list[dict], str]:
    """ARCHIVE：Pareto 前沿更新（纯函数）。返回 (新前沿, 决策)。

    决策取值：
      rejected_dominated      被现任支配 → 拒绝
      rejected_no_improvement 质量不降但无一维严格改善（JIT-Agent 入档规则）
      archived                入档（支配现任则剔除被支配者；互不支配共存）
      archived_evicted        入档但因种群上限淘汰了最老被支配者
    candidate 需带 metrics / generation 键；reference_vec 为其父代向量。
    policy 提供时,JIT"质量不降"按 GATE 同口径阈值判定（计数型结构护栏在
    相对/绝对口径内的变化不算降）;不提供则为最严格口径（任何维变差即拒）。
    """
    cvec = candidate["metrics"]
    ref = candidate.get("parent_metrics") or {}

    if any(dominates(inc["metrics"], cvec) for inc in frontier):
        return frontier, "rejected_dominated"

    # JIT-Agent 入档规则：相对父代质量不降且至少一维严格改善
    nc, nr = _norm(cvec), _norm(ref)
    common = sorted(set(nc) & set(nr))
    if policy is not None:
        # 显著性下限(thresholds.min_improvement,按后缀匹配):改善幅度须超过
        # 预注册噪声底才算"严格改善"(阶段 3 起;缺省 0 = 任意严格改善)
        min_imp = policy["thresholds"].get("min_improvement", {})

        def _improved(k: str) -> bool:
            suffix = k.split(".", 1)[1] if k not in METRIC_DIRECTION and "." in k else k
            thr = float(min_imp.get(suffix, 0.0))
            d = cvec[k] - ref[k]
            if direction_for(k) == "higher":
                return d > thr + _EPS
            return -d > thr + _EPS

        strictly_better = [k for k in common if _improved(k)]
        worse = [k for k in common
                 if regressed_beyond_threshold(k, cvec[k], ref[k], policy)]
    else:
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

# 元层（§4.4）增强字段：每次 COMMIT 自动附带
_POLICY_VERSION_CACHE: tuple[float, int | None] = (0.0, None)


def _policy_version(policy_path: Path = EVAL_POLICY_PATH) -> int | None:
    """eval_policy.yaml 的 version(按 mtime 缓存)。"""
    try:
        mtime = policy_path.stat().st_mtime
    except OSError:
        return None
    global _POLICY_VERSION_CACHE
    if _POLICY_VERSION_CACHE[0] != mtime:
        try:
            data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            _POLICY_VERSION_CACHE = (mtime, data.get("version"))
        except Exception:
            _POLICY_VERSION_CACHE = (mtime, None)
    return _POLICY_VERSION_CACHE[1]


def state_sha256() -> str:
    """三个基因组状态文件(vocab_delta/weights_state/prompt_state)的组合哈希。"""
    h = hashlib.sha256()
    for p in (_EVOLVE_DIR / "vocab_delta.json", _EVOLVE_DIR / "weights_state.json",
              _EVOLVE_DIR / "prompt_state.json"):
        h.update(p.name.encode())
        h.update(p.read_bytes() if p.exists() else b"<absent>")
    return h.hexdigest()


def context_hash(context: object) -> str:
    """提议器输入快照的确定性哈希（canonical JSON）。"""
    return hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def commit_journal(record: dict, journal_path: Path = JOURNAL_PATH) -> Path:
    """COMMIT：追加 evolution_journal.jsonl（每代一条完整 lineage）。

    阶段 4 起自动附带元层字段（缺省时才补，调用方显式给的优先）：
    policy_version（评估口径版本）/ state_sha256（基因组状态指纹）。
    context_hash 由调用方给（提议器输入只有调用方知道）。
    """
    record.setdefault("policy_version", _policy_version())
    record.setdefault("state_sha256", state_sha256())
    record.setdefault("context_hash", None)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return journal_path


def next_generation(journal_path: Path = JOURNAL_PATH) -> int:
    tail = _load_jsonl_tail(journal_path, n=10_000)
    # 跳过非整数代的特殊记录(stability-check / state-correction 等)
    gens = [int(r["generation"]) for r in tail
            if isinstance(r.get("generation"), int)
            or (isinstance(r.get("generation"), str)
                and r["generation"].isdigit())]
    if not gens:
        return 0
    return max(gens) + 1


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
                         batch_size: int = 10, proposer_policy: str = "largest_pool",
                         verbose: bool = True) -> int:
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
    operator = gv.GeoSupplementDeltaOperator(batch_size=batch_size,
                                             novel_policy=proposer_policy)
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
                # 提议策略奖励回写(UCB1 等):接受批次的未解析率降幅
                names_total = pools.get(candidate["target_novel"], {}).get("names", 0)
                if names_total:
                    operator.note_outcome(candidate["target_novel"],
                                          len(kept) / names_total)
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
            "context_hash": context_hash({
                "pools": {s: p["pool_size"] for s, p in pools.items()},
                "committed": len(store["entries"]),
                "rejected": sorted(store.get("rejected", {})),
            }),
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


# ── 阶段 2：权重/参数级 live 进化（Pareto 小种群）────────────────────

def _eval_stage2_state(params: dict, baseline_vec: dict, llm_budget: LlmBudget,
                       no_pytest: bool = False,
                       permute_seed: int | None = None) -> dict:
    """阶段 2 EVAL：写参数 JSON → 子进程注入重建 → topo/rebuild 向量 + golden。"""
    t0 = time.monotonic()
    CANDIDATE_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_PARAMS_PATH.write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    raw = compute_weight_metrics_subprocess(params_path=CANDIDATE_PARAMS_PATH,
                                            permute_seed=permute_seed)
    if no_pytest:
        golden = {"status": "skipped"}
    else:
        import quality_loop as ql

        golden = ql.run_golden_gate(timeout=300)
    vec = dict(baseline_vec)  # geo/m1-m6 等不受权重影响，沿用基线缓存
    vec.update(stage2_metric_vector(raw))
    if isinstance(golden.get("pass_rate"), (int, float)):
        vec["golden.pass_rate"] = float(golden["pass_rate"])
    return {
        "metrics": vec,
        "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                 "llm_calls": llm_budget.calls, "cost_usd": 0.0},
        "golden": golden,
    }


def stability_check_stage2(params: dict, ref_vec: dict, policy: dict,
                           baseline_vec_default: dict | None = None,
                           baseline_jitter: float | None = None,
                           seeds: tuple[int, ...] = (7, 23)) -> dict:
    """§6.3 复测:换章节顺序重评当前最优,抖动显著超基线噪声底才降级。

    口径(v2.2 修正):绝对抖动 >阈值 会把默认参数也降级(实测默认参数在
    seed=23 下自身抖动 0.0209——率型拓扑指标在 ~50-130 个 golden 地点上的
    换序噪声底)。规则改为:候选抖动 > 基线(默认参数)抖动 + threshold
    才降级。baseline_jitter 由调用方测量/缓存传入;缺省时退化为绝对阈值。
    """
    threshold = float(policy.get("metrics", {})
                      .get("stability_jitter_threshold", 0.01))

    def _topo_jitter(ref: dict) -> tuple[float, dict]:
        max_j = 0.0
        detail: dict[str, float] = {}
        for seed in seeds:
            raw = compute_weight_metrics_subprocess(
                params_path=CANDIDATE_PARAMS_PATH, permute_seed=seed,
            )
            vec = stage2_metric_vector(raw)
            for key, rv in ref.items():
                if ".topo." not in key or key not in vec:
                    continue
                j = abs(vec[key] - rv)
                detail[f"seed{seed}:{key}"] = round(j, 4)
                max_j = max(max_j, j)
        return max_j, detail

    # 候选抖动(调用方已把候选参数写入 CANDIDATE_PARAMS_PATH)
    cand_jitter, details = _topo_jitter(ref_vec)

    # 基线(默认参数)抖动:测量或复用缓存
    base_jitter = baseline_jitter
    if base_jitter is None and baseline_vec_default is not None:
        CANDIDATE_PARAMS_PATH.write_text("{}", encoding="utf-8")  # 空注入=默认
        base_jitter, base_details = _topo_jitter(baseline_vec_default)
        details.update({f"baseline:{k}": v for k, v in base_details.items()})

    if base_jitter is not None:
        limit = base_jitter + threshold
        rule = f"候选抖动 {cand_jitter} > 基线噪声底 {base_jitter} + {threshold}"
        stable = cand_jitter <= limit + _EPS
    else:
        limit = threshold
        rule = f"候选抖动 {cand_jitter} > {threshold}(绝对口径,无基线)"
        stable = cand_jitter <= limit + _EPS
    return {"stable": stable, "max_jitter": round(cand_jitter, 4),
            "baseline_jitter": (round(base_jitter, 4)
                                if base_jitter is not None else None),
            "threshold": threshold, "rule": rule, "details": details}


def run_evolution_stage2(generations: int, no_pytest: bool = False,
                         force_param: str | None = None,
                         proposer_policy: str = "round_robin",
                         verbose: bool = True) -> int:
    """阶段 2 主循环：权重扰动 + Pareto 小种群 + 复测降级 + 收尾全量门禁。

    force_param("name=value"):首代强制提议指定参数值（走完整 EVAL/GATE/ARCHIVE/
    COMMIT 路径）——用于候选的拒绝原因已消失（如门禁口径修正）时的人工复测。
    """
    check_frozen_or_abort()  # §6.1 评估器外置

    import weight_jitter as wj

    genome = load_yaml_config(GENOME_PATH, validate_genome)
    policy = load_yaml_config(EVAL_POLICY_PATH, validate_eval_policy)
    budget_s = float(policy["budget"]["wall_clock_seconds_per_generation"])
    llm_limit = int(policy["budget"].get("llm_calls_per_generation", 100))
    pop_max = int(policy["pareto"]["population_max"])

    if not BASELINE_PATH.exists():
        sys.exit(f"FATAL: 基线不存在: {BASELINE_PATH}（先运行 build_baseline.py）")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_vec = metric_vector_from_baseline(baseline)

    loci = genome["genes"]["weights_params"]["loci"]
    # §4.3 停滞护栏:同机制连续无改进 → 强制切换机制(jitter → probe)
    mechanisms = [wj.WeightJitterOperator(loci, param_policy=proposer_policy),
                  wj.WeightProbeOperator(loci, param_policy=proposer_policy)]
    mech_idx = 0
    operator = mechanisms[mech_idx]
    state = wj.load_weights_state()

    frontier = load_frontier(FRONTIER_STAGE2_PATH)
    gen0 = next_generation()
    pause_after = int(policy.get("guardrails", {})
                      .get("no_improvement_pause_generations", 5))

    # 起始:父代向量 = 当前生效参数(default 被已接受 overrides 覆盖)的重评
    current_params = wj.effective_params(operator.loci, state["overrides"])
    print(f"[evolve] 测量父代向量（{len(state['overrides'])} 项已接受覆盖）...")
    parent_eval = _eval_stage2_state(current_params, baseline_vec,
                                     LlmBudget(llm_limit), no_pytest=no_pytest)
    parent_vec = parent_eval["metrics"]
    parent_ref: dict = {"type": "baseline", "measured_at": baseline.get("measured_at")}
    # 默认参数基线(退出标准对照)持久化
    if not state.get("baseline_metrics"):
        if state["overrides"]:
            default_eval = _eval_stage2_state(
                wj.effective_params(operator.loci, {}), baseline_vec,
                LlmBudget(llm_limit), no_pytest=no_pytest)
            state["baseline_metrics"] = default_eval["metrics"]
        else:
            state["baseline_metrics"] = parent_vec
        wj.save_weights_state(state)
    topo_keys = sorted(k for k in parent_vec if ".topo." in k)
    print("[evolve] 父代 topo: "
          + " ".join(f"{k}={parent_vec[k]:.3f}" for k in topo_keys))

    journal_records = _load_jsonl_tail(JOURNAL_PATH, n=10_000)
    # 续跑检测:上轮同机制已连续无改进达护栏线 → 直接从下一机制起步
    trailing_stall = 0
    for r in reversed(journal_records):
        if (r.get("operator") in wj.WEIGHT_OPERATORS
                and not r.get("decision", "").startswith("archived")):
            trailing_stall += 1
        else:
            break
    if trailing_stall >= pause_after and mech_idx + 1 < len(mechanisms):
        mech_idx += 1
        operator = mechanisms[mech_idx]
        print(f"[evolve] 续跑检测:尾部 {trailing_stall} 连无改进,"
              f"直接从 {operator.name} 起步（§4.3 停滞切换）")

    no_improve_streak = 0
    for i in range(generations):
        generation = gen0 + i
        t0 = time.monotonic()
        llm_budget = LlmBudget(llm_limit)
        print(f"\n[evolve] ══ generation {generation} ══")

        # 1. ANALYZE
        context = analyze()
        context["weights_state"] = dict(state["overrides"])
        context["generation"] = generation
        context["param_history"] = wj.build_param_history(journal_records)
        if verbose:
            n_tried = len(context["param_history"])
            print(f"[evolve][analyze] 参数池 {len(operator.param_names)} 个, "
                  f"已试 {n_tried} 个; 已接受覆盖 {len(state['overrides'])} 项")

        # 2. PROPOSE（轮询+动量,单参数扰动;首代可人工强制复测）
        if force_param and i == 0:
            pname, _, raw_val = force_param.partition("=")
            if pname not in operator.loci:
                sys.exit(f"FATAL: --force-param 未知参数: {pname}")
            val: float = float(raw_val)
            if operator.loci[pname].get("type") == "int":
                val = int(val)
            lo, hi = operator.loci[pname]["range"]
            if not lo <= val <= hi:
                sys.exit(f"FATAL: --force-param 值 {val} 超出 {pname} 范围 [{lo}, {hi}]")
            cur = current_params[pname]
            candidate = {
                "operator": operator.name,
                "hypothesis": (f"人工复测: {pname} {cur} → {val}"
                               f"（此前拒绝原因已消失,按现行口径重评,走完整门禁）"),
                "genome_diff": {"weights.set": {pname: val}},
                "param": pname,
                "direction": +1 if val > cur else -1,
                "old_value": cur,
            }
        else:
            candidate = operator(genome, context)
        print(f"[evolve][propose] {candidate['hypothesis']}")
        if candidate.get("exhausted"):
            print("[evolve][propose] 无可扰动空间，如实停止。")
            break

        # 3. APPLY（候选参数 JSON;源码零接触,无脏状态）
        candidate_params = dict(current_params)
        candidate_params.update(candidate["genome_diff"]["weights.set"])
        decision = "failed_error"
        vec: dict | None = None
        gate_result = {"passed": False, "failures": ["eval_error"]}
        cost = {"wall_clock_s": 0.0, "llm_calls": 0, "cost_usd": 0.0}
        error: str | None = None
        gen_parent_ref = parent_ref
        prev_parent_vec = parent_vec
        try:
            # 4. EVAL
            result = _eval_stage2_state(candidate_params, baseline_vec, llm_budget,
                                        no_pytest=no_pytest)
            vec = result["metrics"]
            cost = result["cost"]
            cost["wall_clock_s"] = round(time.monotonic() - t0, 3)
            over_budget = cost["wall_clock_s"] > budget_s
            print(f"[evolve][eval] 指标 {len(vec)} 维 耗时 {cost['wall_clock_s']}s"
                  f"（预算 {budget_s:.0f}s）llm_calls={cost['llm_calls']}"
                  + (" ⚠️超 wall-clock 预算，记失败变异" if over_budget else ""))

            # 5. GATE（对照父代向量;分小说/分指标记账）
            gate_result = gate(vec, parent_vec, policy)
            gate_passed = gate_result["passed"] and not over_budget
            if over_budget:
                gate_result["failures"] = gate_result["failures"] + ["wall_clock_budget"]
            print(f"[evolve][gate] {'通过' if gate_passed else '拒绝'} "
                  f"(failures: {gate_result['failures'] or '无'})")

            # 6. ARCHIVE（Pareto 小种群）
            if gate_passed:
                entry = {
                    "generation": generation,
                    "operator": candidate["operator"],
                    "genome_diff": candidate["genome_diff"],
                    "metrics": vec,
                    "parent_metrics": parent_vec,
                }
                frontier, decision = archive_candidate(frontier, entry, pop_max,
                                                       policy)
                save_frontier(frontier, FRONTIER_STAGE2_PATH)
            else:
                decision = "rejected_gate"
        except LlmBudgetExceeded as err:
            error = str(err)
            decision = "failed_llm_budget"
            gate_result = {"passed": False, "failures": ["llm_budget"]}
            print(f"[evolve][eval] ❌ {error}，该代记失败变异")
        except Exception as err:
            error = f"{type(err).__name__}: {err}"
            gate_result = {"passed": False, "failures": ["eval_error"]}
            print(f"[evolve][eval] ❌ 评估异常: {error}，该代记失败变异")

        # 接受:覆盖值落 weights_state.json(单一事实源);拒绝:状态文件未动,无需回退
        if decision.startswith("archived"):
            state["overrides"].update(candidate["genome_diff"]["weights.set"])
            wj.save_weights_state(state)
            current_params = candidate_params
            parent_vec = vec
            parent_ref = {"type": "generation", "generation": generation}
        # 提议策略奖励回写(ucb1/epsilon_greedy 用;round_robin 下无副作用)
        if candidate.get("param"):
            operator.note_outcome(candidate["param"],
                                  decision.startswith("archived"))
        print(f"[evolve][archive] 决策: {decision} (前沿大小 {len(frontier)}/{pop_max})")

        # 7. COMMIT（journal 完整 lineage）
        record = {
            "generation": generation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator": candidate["operator"],
            "hypothesis": candidate["hypothesis"],
            "genome_diff": candidate["genome_diff"],
            "param": candidate.get("param"),
            "direction": candidate.get("direction"),
            "old_value": candidate.get("old_value"),
            "eval_backend": "live",
            "metrics": vec,
            "gate": {"passed": gate_result["passed"],
                     "failures": gate_result["failures"]},
            "cost": cost,
            "parent": gen_parent_ref,
            "decision": decision,
            "error": error,
            "context_hash": context_hash({
                "param_history": context["param_history"],
                "overrides": context["weights_state"],
                "mechanism": operator.name,
            }),
            "dry_run": False,
            "stage": 2,
        }
        commit_journal(record)
        journal_records.append(record)

        # 8. REPORT（每轮摘要:内层 topo 均值轨迹）
        if vec is not None and prev_parent_vec is not None:
            deltas = [f"{k.split('.')[0]}.{k.split('.')[2]}:"
                      f"{prev_parent_vec.get(k)}→{vec.get(k)}"
                      for k in topo_keys
                      if vec.get(k) != prev_parent_vec.get(k)]
            print(f"[evolve][report] gen{generation}: 决策={decision}; 变化: "
                  + ("; ".join(deltas) if deltas else "无"))
        else:
            print(f"[evolve][report] gen{generation}: 决策={decision}")

        # §6.4 反漂移护栏;§4.3 同机制停滞 → 强制切换机制后再停
        if decision.startswith("archived"):
            no_improve_streak = 0
        else:
            no_improve_streak += 1
        if no_improve_streak >= pause_after:
            if mech_idx + 1 < len(mechanisms):
                mech_idx += 1
                operator = mechanisms[mech_idx]
                switch_record = {
                    "generation": generation,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "operator": "mechanism_switch",
                    "hypothesis": (
                        f"§4.3 停滞护栏:weight_jitter 连续 {no_improve_streak} 代 "
                        f"零变化(平台期),强制切换为 {operator.name}(步长 "
                        f"±{operator.step_min:.0%}-{operator.step_max:.0%})"
                    ),
                    "genome_diff": {},
                    "eval_backend": "live",
                    "metrics": None,
                    "gate": {"passed": True, "failures": []},
                    "cost": {"wall_clock_s": 0.0, "llm_calls": 0, "cost_usd": 0.0},
                    "parent": parent_ref,
                    "decision": "mechanism_switch",
                    "dry_run": False,
                    "stage": 2,
                }
                commit_journal(switch_record)
                journal_records.append(switch_record)
                no_improve_streak = 0
                print(f"[evolve][guardrail] {switch_record['hypothesis']}")
                continue
            print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进,"
                  f"机制已穷尽({mechanisms[-1].name}),自动暂停。"
                  f"诊断: 最近决策见 journal。")
            break

    # ── §6.3 收尾复测:换章节顺序重评当前最优,抖动显著超基线噪声底才降级 ──
    if state["overrides"] and parent_vec is not None:
        print("\n[evolve] §6.3 复测:换章节顺序(2 种子)重评当前最优...")
        CANDIDATE_PARAMS_PATH.write_text(
            json.dumps(current_params, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 基线(默认参数)噪声底:weights_state 缓存,缺则现测
        baseline_jitter = state.get("baseline_stability", {}).get("max_jitter")
        stab = stability_check_stage2(
            current_params, parent_vec, policy,
            baseline_vec_default=state.get("baseline_metrics"),
            baseline_jitter=baseline_jitter,
        )
        if baseline_jitter is None and stab.get("baseline_jitter") is not None:
            state["baseline_stability"] = {"max_jitter": stab["baseline_jitter"],
                                           "seeds": [7, 23]}
            wj.save_weights_state(state)
        stab_record = {
            "generation": "stability-check",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator": "stability_check",
            "hypothesis": "§6.3 换序复测当前最优",
            "genome_diff": {},
            "eval_backend": "live",
            "metrics": None,
            "gate": {"passed": stab["stable"], "failures": []},
            "cost": {"wall_clock_s": None, "llm_calls": 0, "cost_usd": 0.0},
            "parent": parent_ref,
            "decision": "stable" if stab["stable"] else "downgraded_unstable",
            "stability": stab,
            "stage": 2,
        }
        commit_journal(stab_record)
        if stab["stable"]:
            print(f"[evolve] 复测稳定:{stab['rule'].replace(' > ', ' ≤ ')}")
        else:
            print(f"[evolve] ⚠️ 复测不稳定:{stab['rule']},"
                  f"当前最优降级(移出前沿并回退该代参数覆盖)")
            # 降级 = 移出前沿 + 回退该代 genome_diff 设置的参数覆盖
            down_gen = parent_ref.get("generation")
            gen_rec = next((r for r in journal_records
                            if r.get("generation") == down_gen), None)
            if gen_rec:
                for pname in (gen_rec.get("genome_diff", {}).get("weights.set") or {}):
                    state["overrides"].pop(pname, None)
                wj.save_weights_state(state)
                current_params = wj.effective_params(operator.loci, state["overrides"])
            frontier = [e for e in frontier
                        if e.get("generation") != down_gen]
            save_frontier(frontier, FRONTIER_STAGE2_PATH)

    # ── 收尾全量门禁(退出标准:过 quality_loop)──
    print("\n[evolve] 收尾:quality_loop 全量门禁...")
    import quality_loop as ql

    _rec, _prev, rows, ql_exit = ql.run_loop(tag="evolve-stage2-final")
    n_fail = sum(1 for r in rows if r.get("verdict") == "fail")
    print(f"[evolve] quality_loop exit={ql_exit} hard_fails={n_fail}")

    print("\n[evolve] 循环结束。")
    print(render_report())
    return ql_exit


# ── 阶段 3：prompt 级 live 进化（GEPA 模式）──────────────────────────

FRONTIER_STAGE3_PATH = OUT_DIR / "frontier_stage3.json"
REVIEW_MD_PATH = OUT_DIR / "stage3_review.md"


def _stage3_parent_vec_from_fixture(e0: dict) -> dict[str, float]:
    """E0 冻结基线 → 父代向量（原 prompt,无 LLM 消耗）。"""
    vec: dict[str, float] = {}
    recalls = []
    for slug, d in e0["novels"].items():
        vec[f"{slug}.prompt.recall"] = float(d["recall_a"])
        vec[f"{slug}.prompt.count_inflation"] = 1.0
        vec[f"{slug}.prompt.generic_rate"] = float(d["generic_rate_a"])
        recalls.append(float(d["recall_a"]))
    if recalls:
        vec["macro.prompt.recall"] = sum(recalls) / len(recalls)
    return vec


def _stage3_vec_from_metrics(metrics: dict[str, dict]) -> dict[str, float]:
    """快速层 per-novel 指标 → 拍平向量(含 macro.prompt.recall 宏均值)。"""
    vec: dict[str, float] = {}
    recalls = []
    for slug, m in metrics.items():
        for k, v in m.items():
            if isinstance(v, (int, float)) and k != "e_size":
                vec[f"{slug}.{k}"] = float(v)
        if isinstance(m.get("prompt.recall"), (int, float)):
            recalls.append(float(m["prompt.recall"]))
    if recalls:
        vec["macro.prompt.recall"] = sum(recalls) / len(recalls)
    return vec


def _append_review(path: Path, record: dict, diff_text: str) -> None:
    """人工复核汇总：每代的假设/diff/指标变化追加到 stage3_review.md。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n## gen{record['generation']} — {record['decision']}\n\n")
        f.write(f"- 假设: {record.get('hypothesis', '')}\n")
        f.write(f"- 时间: {record.get('timestamp')} · 成本: "
                f"${record.get('cost', {}).get('cost_usd', 0):.4f}\n")
        if record.get("metrics"):
            m = record["metrics"]
            recalls = {k: round(v, 4) for k, v in m.items() if ".prompt.recall" in k}
            f.write(f"- recall: {recalls}\n")
        if record.get("judge"):
            f.write(f"- judge 抽检: supported_rate="
                    f"{record['judge'].get('supported_rate')} (n={record['judge'].get('n')})\n")
        if diff_text:
            f.write("\n```diff\n" + diff_text + "\n```\n")


async def _eval_stage3_candidate(state: dict, fixtures: dict, cost_acc: dict,
                                 llm_budget) -> dict:
    """阶段 3 EVAL 快速层：候选段落渲染 system prompt → 冻结子集重抽 → 指标。"""
    import prompt_evolve as pe

    genres = fixtures["genres"]
    section = pe.current_section(state)
    system_by_slug = {slug: pe.build_system_prompt(section, genres[slug])
                      for slug in pe.INNER}
    extracted = await pe.extract_subset(system_by_slug,
                                        fixtures["chapters"], cost_acc, llm_budget)
    metrics = pe.fast_layer_metrics(extracted, fixtures["t_set"],
                                    fixtures["e0"]["novels"],
                                    fixtures["chapters"])
    return {"extracted": extracted, "metrics": metrics}


def run_evolution_stage3(generations: int, verbose: bool = True) -> int:
    """阶段 3 主循环：GEPA 反思提议 + 分层评估 + judge 抽检 + 门禁/入档。"""
    import difflib

    import prompt_evolve as pe

    check_frozen_or_abort()  # §6.1 评估器外置(含提议器/judge prompt 与冻结基准)

    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env", override=True)

    genome = load_yaml_config(GENOME_PATH, validate_genome)
    policy = load_yaml_config(EVAL_POLICY_PATH, validate_eval_policy)
    budget_s = float(policy["budget"]["wall_clock_seconds_per_generation"])
    llm_limit = int(policy["budget"].get("llm_calls_per_generation", 100))
    cost_limit = float(policy["budget"].get("max_cost_usd_per_generation", 2.0))
    pop_max = int(policy["pareto"]["population_max"])
    pause_after = int(policy.get("guardrails", {})
                      .get("no_improvement_pause_generations", 5))
    judge_min = float(policy.get("metrics", {}).get("judge_min_supported", 0.7))

    state = pe.load_state()
    if pe.heal_prompt_file(state):
        print("[evolve] prompt 文件偏离已提交状态，已按 prompt_state.json 重渲染自愈")
    fixtures = {
        "chapters": json.loads(pe.CHAPTERS_FIXTURE.read_text(encoding="utf-8")),
        "t_set": json.loads(pe.T_SET_FIXTURE.read_text(encoding="utf-8")),
        "e0": json.loads(pe.E0_FIXTURE.read_text(encoding="utf-8")),
        "genres": pe.load_genre_hints(),
    }
    golden_names = pe.load_golden_names()

    frontier = load_frontier(FRONTIER_STAGE3_PATH)
    gen0 = next_generation()
    journal_records = _load_jsonl_tail(JOURNAL_PATH, n=10_000)

    # 父代:无 override 时用 E0 冻结基线(零 LLM);有 override(续跑)时实测
    parent_ref: dict = {"type": "baseline", "fixture": "stage3_e0_baseline"}
    if state["override_section"] is None:
        parent_vec = _stage3_parent_vec_from_fixture(fixtures["e0"])
        current_extracted = None  # None = 失败轨迹用 E0 并集近似
        print("[evolve] 父代=E0 冻结基线 recall: "
              + " ".join(f"{s}={parent_vec[f'{s}.prompt.recall']:.4f}" for s in pe.INNER))
    else:
        print("[evolve] 续跑检测:存在已接受 prompt 变异,实测当前状态作为父代...")
        cost0 = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        res = asyncio.run(_eval_stage3_candidate(state, fixtures, cost0,
                                                 LlmBudget(llm_limit)))
        parent_vec = _stage3_vec_from_metrics(res["metrics"])
        current_extracted = res["extracted"]
        print(f"[evolve] 父代实测成本 ${cost0['cost_usd']:.4f}")

    no_improve_streak = 0
    total_cost = 0.0
    for i in range(generations):
        generation = gen0 + i
        t0 = time.monotonic()
        llm_budget = LlmBudget(llm_limit)
        cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        print(f"\n[evolve] ══ generation {generation} ══")

        # 1. ANALYZE:失败轨迹(相对当前生效 prompt 的漏提) + prompt 变异史
        e_current = current_extracted
        if e_current is None:
            # 原 prompt:用 E0 A 跑结果构造按章视图不可得,E0 存的是并集;
            # 失败轨迹用并集近似(语境窗口仍按章取)
            e_current = {}
            for slug in pe.INNER:
                e0_names = set(fixtures["e0"]["novels"][slug]["names"])
                e_current[slug] = {ch: [n for n in e0_names]  # 并集近似
                                   for ch in fixtures["chapters"][slug]}
        failures = pe.build_failure_trajectory(fixtures["t_set"], e_current)
        prompt_history = [
            {"generation": r.get("generation"), "hypothesis": r.get("hypothesis"),
             "decision": r.get("decision"),
             "gate_failures": (r.get("gate") or {}).get("failures"),
             "recall": {k: v for k, v in (r.get("metrics") or {}).items()
                        if ".prompt.recall" in k}}
            for r in journal_records if r.get("stage") == 3
        ]
        # §4.3 同质停滞护栏:近 2 代提议假设雷同(确定性提议器在不变轨迹下
        # 会产出同样文本) → 在轨迹里附停滞警示,强制换角度(提议器 prompt
        # 本身冻结,此为轨迹数据而非 prompt 变更)
        stagnation_note = None
        recent_hyps = [h.get("hypothesis", "")[:60] for h in prompt_history[-2:]]
        if len(recent_hyps) == 2 and recent_hyps[0] == recent_hyps[1]:
            failed_approaches = [h.get("hypothesis", "")[:100]
                                 for h in prompt_history[-5:]]
            stagnation_note = (
                "停滞警示:最近几次提议的假设完全雷同且均被门禁拒绝"
                f"（失败原因: {[h.get('gate_failures') for h in prompt_history[-5:]]}）。"
                "必须换一个完全不同的机制角度,不得重复以下已失败思路: "
                + " / ".join(failed_approaches)
            )
        context = {
            "current_section": pe.current_section(state),
            "original_section": state["original_section"],
            "failure_trajectory": failures,
            "prompt_history": prompt_history,
            "stagnation_note": stagnation_note,
            "golden_names": golden_names,
            "guard_snapshot": {k: round(v, 4) for k, v in parent_vec.items()
                               if ".prompt." in k},
        }
        print(f"[evolve][analyze] 失败样例 {len(failures)} 条; "
              f"prompt 变异史 {len(prompt_history)} 条")

        # 2. PROPOSE(GEPA 反思,冻结提议器 prompt)
        operator = pe.GEPAReflectOperator(llm_budget, cost_acc)
        try:
            candidate = asyncio.run(operator.propose_async(genome, context))
        except LlmBudgetExceeded as err:
            print(f"[evolve][propose] ❌ {err},该代记失败变异")
            candidate = None
            record = {
                "generation": generation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operator": "prompt_gepa_reflect", "hypothesis": "",
                "genome_diff": {}, "eval_backend": "live", "metrics": None,
                "gate": {"passed": False, "failures": ["llm_budget"]},
                "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                         "llm_calls": llm_budget.calls,
                         "cost_usd": round(cost_acc["cost_usd"], 4)},
                "parent": parent_ref, "decision": "failed_llm_budget",
                "error": str(err), "dry_run": False, "stage": 3,
            }
            commit_journal(record)
            journal_records.append(record)
            no_improve_streak += 1
            if no_improve_streak >= pause_after:
                print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。")
                break
            continue
        except Exception as err:
            print(f"[evolve][propose] ❌ 提议异常: {type(err).__name__}: {err}")
            record = {
                "generation": generation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operator": "prompt_gepa_reflect", "hypothesis": "",
                "genome_diff": {}, "eval_backend": "live", "metrics": None,
                "gate": {"passed": False, "failures": ["proposal_error"]},
                "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                         "llm_calls": llm_budget.calls,
                         "cost_usd": round(cost_acc["cost_usd"], 4)},
                "parent": parent_ref, "decision": "failed_proposal",
                "error": f"{type(err).__name__}: {err}", "dry_run": False, "stage": 3,
            }
            commit_journal(record)
            journal_records.append(record)
            no_improve_streak += 1
            if no_improve_streak >= pause_after:
                print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。")
                break
            continue

        print(f"[evolve][propose] 假设: {candidate['hypothesis']}")

        # anti-hack 拒绝(新增文本含 golden 答案串)
        if candidate.get("rejected_anti_hack"):
            print(f"[evolve][anti-hack] 拒绝:新增文本含 golden 答案串 "
                  f"{candidate['rejected_anti_hack']}")
            record = {
                "generation": generation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operator": candidate["operator"],
                "hypothesis": candidate["hypothesis"],
                "genome_diff": {}, "eval_backend": "live", "metrics": None,
                "gate": {"passed": False, "failures": ["anti_hack_golden_terms"]},
                "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                         "llm_calls": llm_budget.calls,
                         "cost_usd": round(cost_acc["cost_usd"], 4)},
                "parent": parent_ref, "decision": "rejected_anti_hack",
                "anti_hack_rejected": candidate["rejected_anti_hack"],
                "dry_run": False, "stage": 3,
            }
            commit_journal(record)
            journal_records.append(record)
            no_improve_streak += 1
            if no_improve_streak >= pause_after:
                print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。")
                break
            continue

        new_section = candidate["new_section"]
        diff_text = "\n".join(difflib.unified_diff(
            pe.current_section(state).splitlines(), new_section.splitlines(),
            lineterm="", n=2))

        # 3. APPLY(段落替换;finally 逐字节还原到已提交状态)
        pe.apply_section(state, new_section)
        gen_parent_ref = parent_ref
        prev_parent_vec = parent_vec
        decision = "failed_error"
        vec: dict | None = None
        gate_result = {"passed": False, "failures": ["eval_error"]}
        judge_result: dict | None = None
        error: str | None = None
        try:
            # 4. EVAL 快速层(冻结子集重抽)
            trial_state = dict(state, override_section=new_section)
            result = asyncio.run(_eval_stage3_candidate(
                trial_state, fixtures, cost_acc, llm_budget))
            extracted = result["extracted"]
            vec = _stage3_vec_from_metrics(result["metrics"])
            elapsed = round(time.monotonic() - t0, 3)
            over_wall = elapsed > budget_s
            over_cost = cost_acc["cost_usd"] > cost_limit
            print(f"[evolve][eval] 快速层 {len(vec)} 维 耗时 {elapsed}s "
                  f"llm_calls={llm_budget.calls} 成本=${cost_acc['cost_usd']:.4f}"
                  f"（预算 {budget_s:.0f}s/${cost_limit}）"
                  + (" ⚠️超预算记失败" if over_wall or over_cost else ""))

            # 5. GATE(快速层门禁)
            gate_result = gate(vec, parent_vec, policy)
            gate_passed = gate_result["passed"] and not over_wall and not over_cost
            if over_wall:
                gate_result["failures"] += ["wall_clock_budget"]
            if over_cost:
                gate_result["failures"] += ["cost_budget"]
            print(f"[evolve][gate] {'通过' if gate_passed else '拒绝'} "
                  f"(failures: {gate_result['failures'] or '无'})")

            # 6. 确认层:judge 抽检(§6.3 逐级加严,过门禁才花这个钱)
            if gate_passed:
                judge_result = asyncio.run(pe.judge_spotcheck(
                    extracted, {s: {"names": fixtures["e0"]["novels"][s]["names"]}
                                for s in pe.INNER}, cost_acc, llm_budget))
                # 元层(§4.4):judge 逐条 verdict 落盘,journal 只存路径+摘要
                verdicts_dir = OUT_DIR / "judge_verdicts"
                verdicts_dir.mkdir(parents=True, exist_ok=True)
                verdicts_path = verdicts_dir / f"gen{generation}.json"
                verdicts_path.write_text(
                    json.dumps(judge_result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                judge_result["verdicts_path"] = str(
                    verdicts_path.relative_to(_EVOLVE_DIR))
                rate = judge_result.get("supported_rate")
                if rate is not None and rate < judge_min:
                    gate_passed = False
                    gate_result["failures"] += ["judge_spotcheck"]
                    print(f"[evolve][judge] ❌ 新增地名 supported_rate={rate:.2f} "
                          f"< {judge_min}(n={judge_result['n']})")
                else:
                    print(f"[evolve][judge] 抽检通过 supported_rate={rate} "
                          f"(n={judge_result['n']})")

            # 7. ARCHIVE(min_improvement 按噪声底预注册)
            if gate_passed:
                entry = {
                    "generation": generation,
                    "operator": candidate["operator"],
                    "genome_diff": candidate["genome_diff"],
                    "metrics": vec,
                    "parent_metrics": parent_vec,
                }
                frontier, decision = archive_candidate(frontier, entry, pop_max,
                                                       policy)
                save_frontier(frontier, FRONTIER_STAGE3_PATH)
            else:
                decision = "rejected_gate" if not gate_result["passed"] \
                    else "rejected_budget"
        except LlmBudgetExceeded as err:
            error = str(err)
            decision = "failed_llm_budget"
            gate_result = {"passed": False, "failures": ["llm_budget"]}
            print(f"[evolve][eval] ❌ {error}，该代记失败变异")
        except Exception as err:
            error = f"{type(err).__name__}: {err}"
            gate_result = {"passed": False, "failures": ["eval_error"]}
            print(f"[evolve][eval] ❌ 评估异常: {error}，该代记失败变异")
        finally:
            # 还原到已提交状态(接受时文件已是新状态,apply 幂等)
            pe.apply_section(state, pe.current_section(state))

        # 接受:提交 override 落 state
        if decision.startswith("archived"):
            state["override_section"] = new_section
            state["history"].append({
                "generation": generation,
                "hypothesis": candidate["hypothesis"],
                "sha256": hashlib.sha256(new_section.encode()).hexdigest()[:16],
            })
            pe.save_state(state)
            parent_vec = vec
            parent_ref = {"type": "generation", "generation": generation}
            current_extracted = extracted
        print(f"[evolve][archive] 决策: {decision} (前沿大小 {len(frontier)}/{pop_max})")

        # 8. COMMIT + review.md
        record = {
            "generation": generation,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operator": candidate["operator"],
            "hypothesis": candidate["hypothesis"],
            "genome_diff": candidate["genome_diff"],
            "eval_backend": "live",
            "metrics": vec,
            "gate": {"passed": gate_result["passed"],
                     "failures": gate_result["failures"]},
            "judge": judge_result and {"supported_rate": judge_result["supported_rate"],
                                       "n": judge_result["n"],
                                       "verdicts_path": judge_result.get("verdicts_path")},
            "cost": {"wall_clock_s": round(time.monotonic() - t0, 3),
                     "llm_calls": llm_budget.calls,
                     "cost_usd": round(cost_acc["cost_usd"], 4)},
            "parent": gen_parent_ref,
            "decision": decision,
            "attempts": candidate.get("attempts"),
            "retry_errors": candidate.get("retry_errors"),
            "context_hash": candidate.get("context_hash"),
            "error": error,
            "dry_run": False,
            "stage": 3,
        }
        commit_journal(record)
        journal_records.append(record)
        total_cost += cost_acc["cost_usd"]
        _append_review(REVIEW_MD_PATH, record, diff_text)

        # REPORT 每轮摘要
        if vec is not None and prev_parent_vec is not None:
            deltas = [f"{k}: {prev_parent_vec.get(k)}→{round(vec.get(k), 4)}"
                      for k in sorted(vec) if ".prompt.recall" in k
                      and vec.get(k) != prev_parent_vec.get(k)]
            print(f"[evolve][report] gen{generation}: 决策={decision}; recall 变化: "
                  + ("; ".join(deltas) if deltas else "无")
                  + f"; 累计成本 ${total_cost:.3f}")
        else:
            print(f"[evolve][report] gen{generation}: 决策={decision}")

        # §6.4 反漂移护栏
        if decision.startswith("archived"):
            no_improve_streak = 0
        else:
            no_improve_streak += 1
        if no_improve_streak >= pause_after:
            print(f"[evolve][guardrail] 连续 {no_improve_streak} 代无改进，自动暂停。"
                  f"诊断: 最近决策见 journal; prompt diff 复核见 {REVIEW_MD_PATH}")
            break

    # 收尾全量门禁
    print("\n[evolve] 收尾:quality_loop 全量门禁...")
    import quality_loop as ql

    _rec, _prev, rows, ql_exit = ql.run_loop(tag="evolve-stage3-final")
    n_fail = sum(1 for r in rows if r.get("verdict") == "fail")
    print(f"[evolve] quality_loop exit={ql_exit} hard_fails={n_fail}")
    print(f"[evolve] 阶段 3 总 LLM 成本 ≈ ${total_cost:.4f}")
    print(f"[evolve] 人工复核材料: {REVIEW_MD_PATH}")

    print("\n[evolve] 循环结束。")
    print(render_report())
    return ql_exit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GeoEvolve 进化循环（阶段 0 骨架 / 阶段 1 词表级 ACE / 阶段 2 权重级 Pareto）",
        epilog="规格: docs/analysis/geo-self-evolve-methodology.md §4.3/§5/§6",
    )
    parser.add_argument("--stage", type=int, choices=[0, 1, 2, 3], default=0,
                        help="进化阶段：0=恒等骨架；1=词表级 ACE；2=权重级 Pareto；3=prompt 级 GEPA")
    parser.add_argument("--generations", type=int, default=1, help="进化轮数")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="阶段 1 每代提议的 delta 条数")
    parser.add_argument("--dry-run", action="store_true",
                        help="空转模式：cached 评估后端 + 恒等变异，不花钱")
    parser.add_argument("--eval-backend", choices=["cached", "live"], default="cached",
                        help="评估后端：cached=读 baseline.json；live=跑 quality_loop 门禁")
    parser.add_argument("--no-pytest", action="store_true",
                        help="live 后端跳过 golden pytest 子集")
    parser.add_argument("--force-param", metavar="NAME=VALUE",
                        help="阶段 2 首代强制提议指定参数值（人工复测，走完整门禁）")
    parser.add_argument("--proposer-policy", default=None,
                        help="阶段 4 回放选出的提议策略(默认=现状行为)。"
                             "阶段 1: largest_pool(默认)/max_marginal/round_robin/ucb1;"
                             "阶段 2: round_robin(默认)/ucb1/epsilon_greedy")
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
        s1_policies = ("largest_pool", "max_marginal", "round_robin", "ucb1")
        policy = args.proposer_policy or "largest_pool"
        if policy not in s1_policies:
            sys.exit(f"FATAL: 阶段 1 不支持 --proposer-policy {policy}（可选 {s1_policies}）")
        return run_evolution_stage1(generations=args.generations,
                                    no_pytest=args.no_pytest,
                                    batch_size=args.batch_size,
                                    proposer_policy=policy)
    if args.stage == 2:
        s2_policies = ("round_robin", "ucb1", "epsilon_greedy")
        policy = args.proposer_policy or "round_robin"
        if policy not in s2_policies:
            sys.exit(f"FATAL: 阶段 2 不支持 --proposer-policy {policy}（可选 {s2_policies}）")
        return run_evolution_stage2(generations=args.generations,
                                    no_pytest=args.no_pytest,
                                    force_param=args.force_param,
                                    proposer_policy=policy)
    if args.stage == 3:
        if args.proposer_policy:
            print("[evolve] 阶段 3 提议器为 LLM(GEPA),--proposer-policy 不适用,忽略")
        return run_evolution_stage3(generations=args.generations)
    return run_evolution(generations=args.generations, eval_backend=args.eval_backend,
                         dry_run=args.dry_run, no_pytest=args.no_pytest)


if __name__ == "__main__":
    sys.exit(main())
