"""GeoEvolve 阶段 2 —— 权重/参数级变异算子（Pareto 小种群实战）。

要点：
  - 变异面 = genome.yaml level 2 声明的 11 个权重参数（真值 + 取值范围）；
    单代只扰动 1 个参数（归因清晰），±10-20% 乘性小步，clamp 到声明范围，
    int 参数取整；current=0 时退化为加性步（按量程比例）。
  - 提议目标选择 = 轮询（generation % 参数数）+ 动量：上次该参数改善则同向
    再试，被拒则反向（替代阶段 1"最大池贪心"的单点集中问题）。
  - APPLY 不改源码：候选参数写 JSON，EVAL 子进程经 EVOLVE_PARAMS_JSON 注入；
    崩溃零污染（源文件全程不动）。接受值落 weights_state.json（单一事实源）。
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
WEIGHTS_STATE_PATH = _EVOLVE_DIR / "weights_state.json"

STEP_MIN = 0.10
STEP_MAX = 0.20
PROBE_STEP_MIN = 0.30
PROBE_STEP_MAX = 0.60

# 动量历史算入所有权重的算子（机制切换后谱系连续）
WEIGHT_OPERATORS = ("weight_jitter", "weight_probe")


# ── 状态（单一事实源）──────────────────────────────────────────────

def load_weights_state(path: Path = WEIGHTS_STATE_PATH) -> dict:
    """{version, updated_at, baseline_metrics, overrides: {param: value}}。"""
    if not path.exists():
        return {"version": 1, "updated_at": None,
                "baseline_metrics": None, "overrides": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("overrides", {})
    return data


def save_weights_state(state: dict, path: Path = WEIGHTS_STATE_PATH) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def effective_params(loci: dict, overrides: dict) -> dict[str, float]:
    """当前生效参数值 = default 被 overrides 覆盖后的全量视图。"""
    return {
        name: overrides.get(name, locus["default"])
        for name, locus in loci.items()
        if "default" in locus and "range" in locus
    }


# ── 算子 ────────────────────────────────────────────────────────────

class WeightJitterOperator:
    """单参数小步扰动算子（轮询 + 动量 + clamp）。"""

    name = "weight_jitter"

    def __init__(self, loci: dict, step_min: float = STEP_MIN,
                 step_max: float = STEP_MAX,
                 param_policy: str = "round_robin"):
        # 只收声明了 default+range 的参数（recall_pass_min_signals 等不在池内）
        self.loci = {n: loc for n, loc in loci.items()
                     if "default" in loc and "range" in loc}
        self.step_min = step_min
        self.step_max = step_max
        self.param_names = sorted(self.loci)
        # 阶段 4 可选策略(默认 round_robin = 阶段 2 实际行为):
        # ucb1=UCB1(奖励=是否入档,note_outcome 回写);
        # epsilon_greedy=ε=0.3 探索/利用历史最佳
        self.param_policy = param_policy
        self._sel: dict = {"counts": {}, "gains": {}}
        self._eps_rng = random.Random(42)

    def note_outcome(self, pname: str, archived: bool) -> None:
        """选择策略的奖励回写（入档=1 否则=0）。"""
        self._sel["counts"][pname] = self._sel["counts"].get(pname, 0) + 1
        self._sel["gains"][pname] = (self._sel["gains"].get(pname, 0.0)
                                     + (1.0 if archived else 0.0))

    def perturb(self, pname: str, current: float, direction: int,
                magnitude: float) -> tuple[float, int]:
        """对 current 施加 direction* magnitude 的乘性扰动并 clamp。

        撞边界自动反向一次；仍撞则贴界。返回 (新值, 实际方向)。
        保证新值 ≠ current（除非已贴界无可动）。
        """
        lo, hi = self.loci[pname]["range"]
        span = hi - lo

        def step(cur: float, d: int) -> float:
            if cur == 0:
                return cur + d * magnitude * span  # 乘性退化 → 加性
            return cur * (1 + d * magnitude)

        new = step(current, direction)
        if new < lo or new > hi:
            direction = -direction
            new = step(current, direction)
        new = max(lo, min(hi, new))
        if self.loci[pname].get("type") == "int":
            new = round(new)
            if new == current and lo <= current + direction <= hi:
                new = current + direction  # int 最小步
        return new, direction

    def pick_param(self, generation: int) -> str:
        """参数目标选择：round_robin(默认,确定性) / ucb1 / epsilon_greedy。"""
        if self.param_policy == "round_robin":
            return self.param_names[generation % len(self.param_names)]
        counts, gains = self._sel["counts"], self._sel["gains"]
        if self.param_policy == "ucb1":
            import math

            total = sum(counts.values())

            def score(p: str) -> float:
                if counts.get(p, 0) == 0:
                    return float("inf")
                return (gains.get(p, 0.0) / counts[p]
                        + math.sqrt(2 * math.log(max(total, 1)) / counts[p]))

            return max(self.param_names, key=score)
        if self.param_policy == "epsilon_greedy":
            if self._eps_rng.random() < 0.3 or not gains:
                return self._eps_rng.choice(self.param_names)
            return max(self.param_names,
                       key=lambda p: gains.get(p, 0.0) / max(counts.get(p, 1), 1))
        raise ValueError(f"未知 param_policy: {self.param_policy}")

    def __call__(self, genome: dict, context: dict) -> dict:
        """OPERATORS 协议。context 需带:
        weights_state(overrides dict)、generation、param_history。
        """
        overrides = context.get("weights_state", {})
        generation = context["generation"]
        history = context.get("param_history", {})
        current = effective_params(self.loci, overrides)

        pname = self.pick_param(generation)
        rng = random.Random(f"s2:{generation}:{pname}")
        magnitude = rng.uniform(self.step_min, self.step_max)

        prev = history.get(pname)
        if prev and prev["decision"].startswith("archived"):
            direction = prev["direction"]       # 动量：同向再试
        elif prev:
            direction = -prev["direction"]      # 被拒：反向
        else:
            direction = rng.choice([+1, -1])

        new_val, direction = self.perturb(pname, current[pname], direction, magnitude)
        locus = self.loci[pname]
        if new_val == current[pname]:
            return {
                "operator": self.name,
                "hypothesis": f"{pname} 已贴边界 {locus['range']}，无可扰动空间",
                "genome_diff": {},
                "exhausted": True,
            }
        return {
            "operator": self.name,
            "hypothesis": (
                f"{pname}: {current[pname]} → {new_val}"
                f"（{'动量同向' if prev and prev['decision'].startswith('archived') else '探索/反向'},"
                f"范围 {locus['range']}）；{locus['description']}"
            ),
            "genome_diff": {"weights.set": {pname: new_val}},
            "param": pname,
            "direction": direction,
            "old_value": current[pname],
        }


def build_param_history(journal_records: list[dict]) -> dict:
    """从 journal 重建 param → {decision, direction}（动量用，纯函数）。"""
    history: dict[str, dict] = {}
    for r in journal_records:
        if r.get("operator") not in WEIGHT_OPERATORS:
            continue
        param = r.get("param")
        if not param:
            continue
        history[param] = {
            "decision": r.get("decision", ""),
            "direction": (r.get("genome_diff", {}).get("weights.direction")
                          or r.get("direction") or +1),
        }
    return history


class WeightProbeOperator(WeightJitterOperator):
    """大步探针算子（§4.3 停滞强制切换机制的对应物）。

    与 jitter 的差异仅在步长量程：±30-60% 乘性（int 参数等比取整），
    用于 jitter 连续无改进（平台期）后跨越语义阈值
    （如 prior_weight 跌破 prior_threshold 关闭先验覆盖通道）。
    轮询/动量/clamp/单参数归因全部继承。
    """

    name = "weight_probe"

    def __init__(self, loci: dict, param_policy: str = "round_robin"):
        super().__init__(loci, step_min=PROBE_STEP_MIN, step_max=PROBE_STEP_MAX,
                         param_policy=param_policy)
