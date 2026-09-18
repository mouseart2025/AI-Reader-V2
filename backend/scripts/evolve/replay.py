"""GeoEvolve 阶段 4 —— Dream-RSI 式元层回放模拟器（离线，零 LLM 成本）。

思想（Dream-RSI）：累积的 journal 即"历史树"，候选提议策略先在历史树里离线
回放评估，胜出者才花真实预算上线。

三个阶段的回放保真度不同（诚实分层，详见 README 阶段 4 节）：
  阶段 1（词表）：**精确反事实模拟**。评估全规则（无 LLM），候选池/anti-hack/
    Curator 全是确定性函数 → 任意小说选择策略的结果可离线精确重演。
    重放校验：largest_pool 策略必须复现真实 journal（20 代全水浒、142 条入档、
    58 条黑名单、末代 shuihu 未解析率一致）。
  阶段 2（权重）：**记录支撑回放**。动作=(参数,方向,机制)；journal 有同动作
    记录则用真实结果；无记录=UNEXPLORED → 保守规则：记平均评估成本、
    按 rejected_no_improvement 处理、不改变状态（宁低估策略不夸大）。
    匹配含父代状态（已接受覆盖集合）——父代不同则结果不可迁移。
    幅度差异在 jitter/probe 量程内视为等价（单调性假设，记录在案）。
  阶段 3（prompt）：动作是 LLM 生成的连续文本，反事实不可知 →
    **轨迹策略回放**：只对"停滞 K 代即停"类停止规则在已实现轨迹上精确求值。

Usage:
    cd backend && .venv/bin/python scripts/evolve/replay.py
    .venv/bin/python scripts/evolve/replay.py --json out/replay_result.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

JOURNAL_PATH = _EVOLVE_DIR / "evolution_journal.jsonl"

# 实测每代评估成本（journal 均值；用于 UNEXPLORED 保守计费与离线成本折算）
STAGE1_WALL_S = 8.3
STAGE2_WALL_S = 5.4
UNEXPLORED = "unexplored"


def load_journal(path: Path = JOURNAL_PATH) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ═══════════════════════════════════════════════════════════════════
# 阶段 1：精确反事实模拟（规则评估 → 确定性重演）
# ═══════════════════════════════════════════════════════════════════

class Stage1World:
    """阶段 1 的确定性世界模型（gen0 起点）：pristine supplement + 冻结 DB。

    当前 geo_resolver.py 末尾带阶段 1 的 delta 块（已提交 142 条），直接 import
    得到的是"进化后"的字典。世界模型必须是 gen0 起点：delta 块只增不改
    （Curator 拒重），从模块字典中减除 vocab_delta.json 的键即得 pristine。
    模拟每步用 patched = pristine + 已提交 delta 重跑 resolve_names ——
    自动复现生产的全部二阶效应（祖先解锁、pass-2 父坐标校验淘汰）。
    """

    def __init__(self):
        import geo_vocab as gv

        from src.services import geo_resolver as gr
        from src.services.geo_resolver import GeoResolver

        self._gv = gv
        self._gr = gr
        delta_path = _EVOLVE_DIR / "vocab_delta.json"
        delta_names = set(json.loads(delta_path.read_text(encoding="utf-8"))
                          .get("entries", {})) if delta_path.exists() else set()
        self.pristine_geo = {k: v for k, v in gr._SUPPLEMENT_GEO.items()
                             if k not in delta_names}
        self.pristine_cn = {k: v for k, v in gr._SUPPLEMENT_CN.items()
                            if k not in delta_names}
        self.golden_text = gv.load_golden_texts()
        self.resolver = GeoResolver(dataset_key="cn")
        self.resolver._load_index()
        conn = gv.open_db_readonly()
        try:
            ids = gv.resolve_novel_ids(conn)
            self.novels: dict[str, dict] = {}
            for slug, _t, _ in gv.NOVELS:
                lp = gv.load_location_parents(conn, ids[slug])
                names = sorted(set(lp) | {p for p in lp.values() if p})
                freq = gv.load_name_frequency(conn, ids[slug])
                self.novels[slug] = {"names": names, "n_names": len(names),
                                     "parent_map": lp, "freq": freq}
        finally:
            conn.close()
        # gen0 基线（pristine，无任何 committed）
        base = self.snapshot({}, set())
        for slug, s in base.items():
            self.novels[slug]["base_unresolved_rate"] = s["unresolved_rate"]

    def snapshot(self, committed: dict, blacklist: set) -> dict:
        """在给定 committed delta 下的全量重解析 + 候选池重建（生产同口径）。"""
        gv = self._gv
        gr = self._gr
        from src.services.geo_resolver import _find_resolved_ancestor

        patched_geo = {**self.pristine_geo, **committed}
        orig_geo, orig_cn = gr._SUPPLEMENT_GEO, gr._SUPPLEMENT_CN
        gr._SUPPLEMENT_GEO = patched_geo  # 生产函数读模块级字典
        gr._SUPPLEMENT_CN = self.pristine_cn
        try:
            existing = {**patched_geo, **self.pristine_cn}
            out = {}
            for slug, nv in self.novels.items():
                names, lp = nv["names"], nv["parent_map"]
                resolved = self.resolver.resolve_names(names, lp)
                unresolved = [n for n in names if n not in resolved]
                pool = []
                for n in unresolved:
                    if n in blacklist:
                        continue
                    coords = _find_resolved_ancestor(n, lp, resolved)
                    if coords is None:
                        continue
                    if gv.curator_check(n, coords, existing=existing,
                                        committed=committed) is not None:
                        continue
                    pool.append({"name": n, "coords": coords,
                                 "frequency": nv["freq"].get(n, 0)})
                pool.sort(key=lambda c: (-c["frequency"], c["name"]))
                out[slug] = {"unresolved_rate": len(unresolved) / len(names),
                             "pool": pool}
            return out
        finally:
            gr._SUPPLEMENT_GEO, gr._SUPPLEMENT_CN = orig_geo, orig_cn

    def simulate(self, choose_novel, generations: int = 20,
                 batch_size: int = 10,
                 strategy_state: dict | None = None,
                 on_step=None) -> dict:
        """按 choose_novel(state) 策略逐步模拟（每步全量重解析，精确）。

        与真实循环同构：pool 含 golden 名（anti-hack 在提议后过滤）→
        batch 取前 10 → golden 名入黑名单、其余入档 → 重解析。
        """
        gv = self._gv
        committed: dict[str, tuple] = {}
        blacklist: set[str] = set()
        traj = []
        for gen in range(generations):
            snap = self.snapshot(committed, blacklist)
            state = {"pools": {s: snap[s]["pool"] for s in snap}, "gen": gen,
                     "strategy_state": strategy_state if strategy_state is not None else {}}
            slug = choose_novel(state)
            if slug is None or not snap[slug]["pool"]:
                traj.append({"gen": gen, "slug": slug, "added": 0,
                             "blacklisted": 0})
                continue
            batch = snap[slug]["pool"][:batch_size]
            keep, rejected = gv.anti_hack_filter(
                {c["name"]: c["coords"] for c in batch}, self.golden_text)
            blacklist.update(rejected)
            committed.update({n: tuple(c) for n, c in keep.items()})
            if on_step:
                on_step(state, slug, len(keep),
                        len(keep) / self.novels[slug]["n_names"])
            traj.append({"gen": gen, "slug": slug, "added": len(keep),
                         "blacklisted": len(rejected),
                         "rates_pre": {s: snap[s]["unresolved_rate"]
                                       for s in snap}})
        final = self.snapshot(committed, blacklist)
        per_novel_committed: dict[str, int] = {}
        for t in traj:
            if t["slug"]:
                per_novel_committed[t["slug"]] = \
                    per_novel_committed.get(t["slug"], 0) + t["added"]
        return {"trajectory": traj,
                "final_rates": {s: final[s]["unresolved_rate"] for s in final},
                "committed": per_novel_committed,
                "blacklist_count": len(blacklist),
                "total_committed": sum(per_novel_committed.values()),
                "wall_s": generations * STAGE1_WALL_S, "cost_usd": 0.0}


def s1_cumulative_reduction(world: Stage1World, sim: dict) -> list[float]:
    """每代后的累计总未解析率降幅(用下一代快照的 rates_pre,末代用终态)。"""
    base = {s: world.novels[s]["base_unresolved_rate"] for s in world.novels}
    snaps = [t["rates_pre"] for t in sim["trajectory"][1:]]
    snaps.append(sim["final_rates"])
    return [round(sum(base[s] - snap[s] for s in base), 4) for snap in snaps]


def s1_gens_to_reach(cum: list[float], target: float) -> int | None:
    """达到 target 累计降幅所需代数(1 起);达不到返回 None。

    cum 已按 4 位小数取整,target 先同口径取整再比(浮点尾差不误判"未达到")。
    """
    target_r = round(target, 4)
    for i, c in enumerate(cum, start=1):
        if c >= target_r - 1e-9:
            return i
    return None



# ── 阶段 1 候选策略（小说选择）──────────────────────────────────────

def s1_actual(state) -> str:
    """实际策略：最大候选池（与 geo_vocab.GeoSupplementDeltaOperator 一致）。"""
    pools = state["pools"]
    best, best_n = None, -1
    for slug in ("xiyouji", "honglou", "shuihu", "sanguo", "fengshen"):
        if len(pools.get(slug, [])) > best_n:
            best, best_n = slug, len(pools[slug])
    return best


def s1_round_robin(state) -> str:
    """轮询五本（阶段 1 事后建议）。"""
    order = ["xiyouji", "honglou", "shuihu", "sanguo", "fengshen"]
    for i in range(5):
        slug = order[(state["gen"] + i) % 5]
        if state["pools"].get(slug):
            return slug
    return None


def s1_max_marginal(state) -> str:
    """边际收益最大：每代边际 = 批次条数/该本地名总数（小书每条降幅更大）。"""
    pools = state["pools"]
    best, best_gain = None, -1.0
    for slug, cands in pools.items():
        if not cands:
            continue
        total_names = _S1_WORLD_REF.novels[slug]["n_names"] if _S1_WORLD_REF else 1
        gain = min(len(cands), 10) / total_names
        if gain > best_gain:
            best, best_gain = slug, gain
    return best


def s1_ucb1(state) -> str:
    """UCB1：奖励=该本累计未解析率降幅;未试过的优先。状态存 strategy_state。"""
    pools = state["pools"]
    ss = state["strategy_state"]
    counts = ss.setdefault("counts", {})
    rewards = ss.setdefault("rewards", {})
    total = sum(counts.values())
    best, best_score = None, -1.0
    for slug, cands in pools.items():
        if not cands:
            continue
        if counts.get(slug, 0) == 0:
            score = float("inf")  # 未探索优先
        else:
            score = (rewards.get(slug, 0.0) / counts[slug]
                     + math.sqrt(2 * math.log(max(total, 1)) / counts[slug]))
        if score > best_score:
            best, best_score = slug, score
    return best


_S1_WORLD_REF: Stage1World | None = None  # max_marginal 需要 names 总数


def s1_ucb1_on_step(state, slug: str, _added: int, rate_gain: float) -> None:
    ss = state["strategy_state"]
    ss["counts"][slug] = ss["counts"].get(slug, 0) + 1
    ss["rewards"][slug] = ss["rewards"].get(slug, 0.0) + rate_gain


# ═══════════════════════════════════════════════════════════════════
# 阶段 2：记录支撑回放
# ═══════════════════════════════════════════════════════════════════

def stage2_online_overrides(records: list[dict]) -> dict[int, frozenset]:
    """重建每代的在线父代覆盖状态（archived 累积 + state_correction 回退）。"""
    overrides: dict = {}
    out: dict[int, frozenset] = {}
    for r in records:
        if r.get("stage") != 2:
            continue
        gen = r.get("generation")
        if not isinstance(gen, int):
            # state-correction 等特殊记录按位置生效
            for k in (r.get("genome_diff", {}).get("weights.unset") or {}):
                overrides.pop(k, None)
            continue
        out[gen] = frozenset(overrides.items())
        if str(r.get("decision", "")).startswith("archived"):
            overrides.update(r.get("genome_diff", {}).get("weights.set") or {})
    return out


class Stage2Replay:
    """动作=(param, direction, mechanism)。匹配同动作同父代状态的真实记录。"""

    def __init__(self, records: list[dict]):
        self.records = [r for r in records
                        if r.get("stage") == 2 and isinstance(r.get("generation"), int)
                        and r.get("operator") in ("weight_jitter", "weight_probe")]
        self.parents = stage2_online_overrides(records)
        # 索引:(param, direction, mechanism, parent_key) → 同动作记录列表
        # (同键可重复:gen33 与 gen39 同参数同方向同父代——人工复测重评;
        # 回放取首个记录,重放校验用身份匹配)
        self.index: dict[tuple, list[dict]] = {}
        for r in self.records:
            key = (r.get("param"), r.get("direction"), r.get("operator"),
                   self.parents.get(r["generation"], frozenset()))
            self.index.setdefault(key, []).append(r)

    def resolve(self, param: str, direction: int, mechanism: str,
                parent_key: frozenset) -> dict:
        """返回 {"outcome": record} 或 UNEXPLORED 保守结果。

        同动作多条记录(人工复测)取首个——保守:复测通常是拒绝原因消失后的
        重评,取较早(更不利)的结果。
        """
        recs = self.index.get((param, direction, mechanism, parent_key))
        if recs:
            return {"status": "matched", "record": recs[0]}
        return {"status": UNEXPLORED,
                "record": {"decision": "rejected_no_improvement",
                           "cost": {"wall_clock_s": STAGE2_WALL_S,
                                    "llm_calls": 0, "cost_usd": 0.0},
                           "metrics": None}}

    def validate_against_journal(self) -> list[str]:
        """重放校验:用真实动作序列回放,结果必须与 journal 逐代一致。"""
        mismatches = []
        for r in self.records:
            parent_key = self.parents.get(r["generation"], frozenset())
            key = (r.get("param"), r.get("direction"), r.get("operator"), parent_key)
            recs = self.index.get(key, [])
            if not any(rec is r for rec in recs):
                mismatches.append(f"gen{r['generation']}: 未匹配到自身记录")
        return mismatches


# ── 阶段 2 候选策略（参数选择;方向/动量规则与实际一致以隔离变量）──────

def s2_actual_policy(gen: int, _history: list, _state: dict) -> tuple[str, str]:
    """实际策略:轮询(gen % 11),机制按停滞切换(jitter 5 连停 → probe)。"""
    return "round_robin", "stall5"


def s2_ucb1_policy(gen: int, history: list, state: dict) -> tuple[str, str]:
    return "ucb1", "stall5"


def s2_epsilon_greedy_policy(gen: int, history: list,
                             state: dict) -> tuple[str, str]:
    return "epsilon_greedy", "stall5"


def s2_aggressive_switch_policy(gen: int, history: list,
                                state: dict) -> tuple[str, str]:
    return "round_robin", "stall2"


PARAM_NAMES = None  # 由 run_stage2_replay 注入(genome.yaml 顺序)


def _s2_choose_param(selection: str, gen: int, tried: dict, gains: dict,
                     rng: random.Random) -> str:
    params = PARAM_NAMES
    if selection == "round_robin":
        return params[gen % len(params)]
    if selection == "ucb1":
        total = sum(tried.values())
        best, best_score = None, -1.0
        for p in params:
            if tried.get(p, 0) == 0:
                score = float("inf")
            else:
                score = (gains.get(p, 0.0) / tried[p]
                         + math.sqrt(2 * math.log(max(total, 1)) / tried[p]))
            if score > best_score:
                best, best_score = p, score
        return best
    if selection == "epsilon_greedy":
        if rng.random() < 0.3 or not gains:
            return rng.choice(params)
        return max(params, key=lambda p: gains.get(p, 0.0) / max(tried.get(p, 1), 1))
    raise ValueError(selection)


def run_stage2_replay(replay: Stage2Replay, selection: str, stall_limit: int,
                      generations: int = 33, seed: int = 42) -> dict:
    """回放一个阶段 2 策略。状态=覆盖集合(仅 archived 改变);方向=动量规则。"""
    overrides: dict = {}
    direction_memo: dict[str, dict] = {}
    tried: dict[str, int] = {}
    gains: dict[str, float] = {}
    rng = random.Random(seed)
    stall = 0
    mechanism = "weight_jitter"
    traj = []
    first_archive_gen = None
    total_wall = 0.0
    total_cost = 0.0
    unexplored = 0
    for i in range(generations):
        param = _s2_choose_param(selection, i, tried, gains, rng)
        prev = direction_memo.get(param)
        if prev and prev["decision"].startswith("archived"):
            direction = prev["direction"]
        elif prev:
            direction = -prev["direction"]
        else:
            direction = random.Random(f"s2:{i}:{param}").choice([+1, -1])
        parent_key = frozenset(overrides.items())
        res = replay.resolve(param, direction, mechanism, parent_key)
        rec = res["record"]
        decision = rec["decision"]
        total_wall += rec["cost"]["wall_clock_s"]
        total_cost += rec["cost"].get("cost_usd") or 0.0
        if res["status"] == UNEXPLORED:
            unexplored += 1
        tried[param] = tried.get(param, 0) + 1
        # 增益:archived 记 1(阶段 2 无显著指标收益,按接受与否计)
        gain = 1.0 if decision.startswith("archived") else 0.0
        gains[param] = gains.get(param, 0.0) + gain
        direction_memo[param] = {"decision": decision, "direction": direction}
        if decision.startswith("archived"):
            if first_archive_gen is None:
                first_archive_gen = i
            overrides.update(rec.get("genome_diff", {}).get("weights.set") or {})
            stall = 0
        else:
            stall += 1
        traj.append({"gen": i, "param": param, "mechanism": mechanism,
                     "decision": decision, "status": res["status"]})
        if stall >= stall_limit:
            if mechanism == "weight_jitter":
                mechanism = "weight_probe"
                stall = 0
            else:
                break  # 机制穷尽,停
    return {"selection": selection, "stall_limit": stall_limit,
            "gens_run": len(traj), "first_archive_gen": first_archive_gen,
            "archived": sum(1 for t in traj if t["decision"].startswith("archived")),
            "unexplored": unexplored, "wall_s": round(total_wall, 1),
            "cost_usd": round(total_cost, 3), "trajectory": traj}


# ═══════════════════════════════════════════════════════════════════
# 阶段 3：轨迹策略回放（停止规则敏感性,已实现轨迹上精确）
# ═══════════════════════════════════════════════════════════════════

def run_stage3_trajectory_policies(records: list[dict],
                                   stall_ks: tuple[int, ...] = (2, 3, 5, 7, 99)
                                   ) -> list[dict]:
    """对"连续 K 代未入档即停"的停止规则在真实轨迹上精确求值。

    诚实注记:gen61+ 的轨迹依赖中途加入的停滞警示干预(非策略可回放部分),
    本回放回答的是"若用停止规则 K,何时停、省下多少、是否错过 gen64"。
    """
    s3 = [r for r in records
          if r.get("stage") == 3 and isinstance(r.get("generation"), int)]
    out = []
    for k in stall_ks:
        stall = 0
        cost = 0.0
        wall = 0.0
        captured_macro_gain = 0.0
        gens_run = 0
        stopped_at = None
        baseline_macro = 0.46644073024287236  # E0 冻结基线(阶段 3 父代)
        for r in s3:
            gens_run += 1
            cost += r["cost"].get("cost_usd") or 0.0
            wall += r["cost"].get("wall_clock_s") or 0.0
            if str(r["decision"]).startswith("archived"):
                stall = 0
                macro = (r.get("metrics") or {}).get("macro.prompt.recall")
                if macro:
                    captured_macro_gain = max(captured_macro_gain,
                                              macro - baseline_macro)
            else:
                stall += 1
                if stall >= k:
                    stopped_at = r["generation"]
                    break
        out.append({"stall_K": k, "gens_run": gens_run,
                    "cost_usd": round(cost, 3), "wall_s": round(wall, 1),
                    "macro_gain_captured": round(captured_macro_gain, 4),
                    "stopped_at": stopped_at})
    return out


# ═══════════════════════════════════════════════════════════════════
# 主编排
# ═══════════════════════════════════════════════════════════════════

def run_all(skip_stage1_sim: bool = False) -> dict:
    records = load_journal()
    result: dict = {"journal_records": len(records)}

    # ── 阶段 1:精确反事实模拟 ──
    s1_records = [r for r in records if r.get("stage") == 1]
    s1_actual_journal = {
        "gens": len(s1_records),
        "total_committed": None,
        "final_shuihu_rate": (s1_records[-1].get("metrics") or {}).get(
            "shuihu.geo.unresolved_rate"),
        "blacklist": None,
    }
    if not skip_stage1_sim:
        global _S1_WORLD_REF
        world = Stage1World()
        _S1_WORLD_REF = world

        # 重放校验:actual 策略必须复现真实 journal
        validation = world.simulate(s1_actual)
        real_shuihu = s1_actual_journal["final_shuihu_rate"]
        sim_shuihu = validation["final_rates"]["shuihu"]
        # 真实 blacklist/vocab_delta 终态
        vd = json.loads((_EVOLVE_DIR / "vocab_delta.json").read_text(encoding="utf-8"))
        real_committed_shuihu = sum(1 for e in vd["entries"].values()
                                    if e.get("novel") == "shuihu")
        s1_actual_journal["total_committed"] = len(vd["entries"])
        s1_actual_journal["blacklist"] = len(vd.get("rejected", {}))
        validation_ok = (
            validation["committed"]["shuihu"] == real_committed_shuihu
            and abs(sim_shuihu - real_shuihu) < 1e-9
        )
        strategies = {"actual(最大池)": s1_actual, "round_robin(轮询)": s1_round_robin,
                      "max_marginal(边际收益)": s1_max_marginal, "ucb1": s1_ucb1}
        s1_out = {}
        actual_sims = world.simulate(s1_actual)
        actual_total = sum(
            world.novels[s]["base_unresolved_rate"] - actual_sims["final_rates"][s]
            for s in world.novels)
        for name, fn in strategies.items():
            sim = (actual_sims if fn is s1_actual
                   else world.simulate(fn, strategy_state={},
                                       on_step=s1_ucb1_on_step if fn is s1_ucb1
                                       else None))
            total_reduction = sum(
                world.novels[s]["base_unresolved_rate"] - sim["final_rates"][s]
                for s in world.novels)
            cum = s1_cumulative_reduction(world, sim)
            gens_needed = s1_gens_to_reach(cum, actual_total)
            s1_out[name] = {
                "total_committed": sim["total_committed"],
                "blacklist_count": sim["blacklist_count"],
                "total_rate_reduction": round(total_reduction, 4),
                "gens_to_actual_total": gens_needed,
                "cum_reduction": cum,
                "final_rates": {k: round(v, 4)
                                for k, v in sim["final_rates"].items()},
                "wall_s": sim["wall_s"],
            }
        result["stage1"] = {
            "fidelity": "exact_counterfactual(规则评估确定性重演)",
            "validation_actual_matches_journal": validation_ok,
            "validation_detail": {
                "sim_committed_shuihu": validation["committed"]["shuihu"],
                "journal_committed_shuihu": real_committed_shuihu,
                "sim_shuihu_rate": round(sim_shuihu, 6),
                "journal_shuihu_rate": round(real_shuihu, 6),
                "journal_blacklist": s1_actual_journal["blacklist"],
            },
            "strategies": s1_out,
        }
    else:
        result["stage1"] = {"skipped": True}

    # ── 阶段 2:记录支撑回放 ──
    global PARAM_NAMES
    import yaml

    genome = yaml.safe_load(
        (_EVOLVE_DIR / "genome.yaml").read_text(encoding="utf-8"))
    PARAM_NAMES = sorted(
        n for n, loc in genome["genes"]["weights_params"]["loci"].items()
        if "default" in loc and "range" in loc)
    s2 = Stage2Replay(records)
    mismatches = s2.validate_against_journal()
    s2_strategies = {}
    for name, (sel, stall) in {
        "actual(轮询+stall5)": ("round_robin", 5),
        "ucb1+stall5": ("ucb1", 5),
        "eps_greedy(0.3)+stall5": ("epsilon_greedy", 5),
        "轮询+stall2(激进早切)": ("round_robin", 2),
    }.items():
        s2_strategies[name] = run_stage2_replay(s2, sel, stall)
    for v in s2_strategies.values():
        v.pop("trajectory", None)
    result["stage2"] = {
        "fidelity": "record_backed(UNEXPLORED 保守:计成本记拒绝)",
        "validation_mismatches": mismatches,
        "realized_actions": len(s2.records),
        "strategies": s2_strategies,
    }

    # ── 阶段 3:轨迹策略回放 ──
    result["stage3"] = {
        "fidelity": "trajectory_policy(停止规则在真实轨迹上精确求值)",
        "note": "gen61+ 依赖中途的停滞警示干预(非策略可回放);本表回答停止规则 K 的代价/收益",
        "policies": run_stage3_trajectory_policies(records),
    }
    return result


def render_md(result: dict) -> str:
    lines = ["# GeoEvolve 阶段 4 —— 元层回放报告", ""]
    s1 = result.get("stage1", {})
    if not s1.get("skipped"):
        v = s1["validation_actual_matches_journal"]
        lines.append(f"## 阶段 1(精确反事实模拟)——重放校验: {'✅ 一致' if v else '❌ 不一致'}")
        lines.append("")
        lines.append("| 策略 | 入档条数 | 总未解析率降幅 | 达实际总降幅所需代数 | 终态 rates |")
        lines.append("|---|---|---|---|---|")
        for name, s in s1["strategies"].items():
            g2r = s.get("gens_to_actual_total")
            lines.append(f"| {name} | {s['total_committed']} | "
                         f"{s['total_rate_reduction']} | "
                         f"{'未达到' if g2r is None else g2r} | {s['final_rates']} |")
        lines.append("")
    s2 = result["stage2"]
    lines.append("## 阶段 2(记录支撑回放)")
    lines.append(f"- 重放校验失配: {len(s2['validation_mismatches'])} 处; "
                 f"已实现动作 {s2['realized_actions']} 个")
    lines.append("")
    lines.append("| 策略 | 跑代数 | 首次入档代 | 入档数 | UNEXPLORED | wall(s) |")
    lines.append("|---|---|---|---|---|---|")
    for name, s in s2["strategies"].items():
        fa = s["first_archive_gen"]
        lines.append(f"| {name} | {s['gens_run']} | "
                     f"{'无' if fa is None else fa} | {s['archived']} | "
                     f"{s['unexplored']} | {s['wall_s']} |")
    lines.append("")
    lines.append("## 阶段 3(轨迹策略回放:停滞 K 代即停)")
    lines.append("")
    lines.append("| K | 跑代数 | 成本($) | 捕获 macro 增益 | 停止于 |")
    lines.append("|---|---|---|---|---|")
    for p in result["stage3"]["policies"]:
        lines.append(f"| {p['stall_K']} | {p['gens_run']} | {p['cost_usd']} | "
                     f"{p['macro_gain_captured']} | {p['stopped_at'] or '跑完'} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="阶段 4 元层回放模拟器")
    parser.add_argument("--json", type=Path, default=None, help="结果 JSON 输出路径")
    parser.add_argument("--skip-stage1-sim", action="store_true",
                        help="跳过阶段 1 精确模拟(需 DB+GeoNames,~10s)")
    args = parser.parse_args(argv)
    result = run_all(skip_stage1_sim=args.skip_stage1_sim)
    md = render_md(result)
    print(md)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                        default=str) + "\n", encoding="utf-8")
        print(f"[replay] 结果已写 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
