"""GeoEvolve 阶段 4 测试（replay.py + backfill_journal.py + run_loop 元层字段）。

覆盖：
  - backfill：幂等性、policy_version 阶段映射、状态指纹重建、null 覆盖率语义
  - Stage2Replay：匹配/UNEXPLORED 保守规则、同键多记录（gen33/39 人工复测）、
    重放一致性（自身记录身份匹配）、online overrides 重建（含 state_correction）
  - 阶段 1 策略函数：最大池/轮询/边际收益/UCB1 选择逻辑；gens_to_reach 容差
  - 阶段 3 轨迹停止规则：K 值敏感性（早停错过改善）
  - commit_journal 元层自动字段（policy_version/state_sha256/context_hash）

纯本地：不触 LLM、不读真实 DB、不碰真实 journal（全部用 tmp/fake）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import ClassVar

import pytest

_EVOLVE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "evolve"
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_EVOLVE_DIR), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backfill_journal as bf  # noqa: E402
import replay as rp  # noqa: E402
import run_loop as rl  # noqa: E402

# ── backfill ─────────────────────────────────────────────────────────

class TestBackfill:
    def _records(self):
        return [
            {"generation": 0, "stage": 0, "operator": "identity",
             "decision": "rejected_no_improvement"},
            {"generation": 2, "stage": 1, "operator": "geo_supplement_delta",
             "decision": "archived",
             "genome_diff": {"vocab_delta.add": {"东门": [38.0, 116.0]}}},
            {"generation": 22, "stage": 2, "operator": "weight_jitter",
             "decision": "archived",
             "genome_diff": {"weights.set": {"p.a": 1.5}}},
            {"generation": "stability-check", "stage": 2, "operator": "stability_check",
             "decision": "stable"},
        ]

    def test_policy_version_mapping(self):
        out, stats = bf.backfill(self._records(), {})
        assert [r["policy_version"] for r in out] == [0, 1, 2, 2]
        assert stats["policy_version_filled"] == 4

    def test_state_fingerprint_filled_only_for_int_gens(self):
        out, _stats = bf.backfill(self._records(), {0: "h0", 2: "h2", 22: "h22"})
        assert out[0]["state_sha256"] == "h0"
        assert out[2]["state_sha256"] == "h22"
        assert out[3].get("state_sha256") is None  # 非整数代 → null

    def test_idempotent(self):
        once, _ = bf.backfill(self._records(), {0: "h0"})
        twice, stats2 = bf.backfill([dict(r) for r in once], {0: "h0"})
        assert json.dumps(once, sort_keys=True) == json.dumps(twice, sort_keys=True)
        assert stats2["policy_version_filled"] == 0

    def test_existing_fields_not_overwritten(self):
        recs = [{"generation": 5, "stage": 1, "policy_version": 99,
                 "state_sha256": "existing"}]
        out, _ = bf.backfill(recs, {5: "h5"})
        assert out[0]["policy_version"] == 99
        assert out[0]["state_sha256"] == "existing"

    def test_reconstruct_fingerprints(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(bf, "_EVOLVE_DIR", tmp_path)
        (tmp_path / "vocab_delta.json").write_text(json.dumps({
            "entries": {
                "甲": {"generation": 2, "coords": [1, 2]},
                "乙": {"generation": 5, "coords": [3, 4]},
            }}), encoding="utf-8")
        (tmp_path / "prompt_state.json").write_text(json.dumps({
            "history": [{"generation": 64, "sha256": "abc"}]}), encoding="utf-8")
        records = [
            {"generation": 2, "stage": 1, "decision": "archived"},
            {"generation": 4, "stage": 1, "decision": "archived"},
            {"generation": 6, "stage": 1, "decision": "archived"},
            {"generation": 65, "stage": 3, "decision": "archived"},
        ]
        fps = bf._reconstruct_state_fingerprints(records)
        # 乙(gen5):不进 gen2/gen4 的指纹,进 gen6 的
        assert fps[2] == fps[4]
        assert fps[6] != fps[4]
        # gen65 应含 prompt sha
        assert fps[65] != fps[6]


# ── Stage2Replay ─────────────────────────────────────────────────────

def _s2_record(gen, param, direction, operator="weight_probe",
               decision="rejected_no_improvement", weights_set=None):
    return {"generation": gen, "stage": 2, "operator": operator,
            "param": param, "direction": direction, "decision": decision,
            "genome_diff": {"weights.set": weights_set or {}},
            "metrics": None,
            "cost": {"wall_clock_s": 5.0, "llm_calls": 0, "cost_usd": 0.0}}


class TestStage2Replay:
    def _replay(self):
        recs = [
            _s2_record(0, "p.a", +1),
            _s2_record(1, "p.b", -1, decision="archived",
                       weights_set={"p.b": 2.0}),
            _s2_record(2, "p.a", +1),  # 父代含 p.b=2.0
            _s2_record(3, "p.a", +1),  # 同键重复(人工复测),父代同 gen2
            {"generation": "state-correction", "stage": 2,
             "operator": "state_correction", "decision": "state_correction",
             "genome_diff": {"weights.unset": {"p.b": 2.0}}},
            _s2_record(4, "p.c", +1),  # 父代已回退为空
        ]
        return rp.Stage2Replay(recs)

    def test_match_and_unexplored(self):
        r = self._replay()
        assert r.resolve("p.a", +1, "weight_probe", frozenset())["status"] == "matched"
        res = r.resolve("p.never", +1, "weight_probe", frozenset())
        assert res["status"] == rp.UNEXPLORED
        # 保守规则:未探索动作记拒绝+计平均成本,不改变状态
        assert res["record"]["decision"] == "rejected_no_improvement"
        assert res["record"]["cost"]["wall_clock_s"] == rp.STAGE2_WALL_S

    def test_parent_state_matters(self):
        r = self._replay()
        # gen2/3 的 p.a 父代含 p.b=2.0;空父代不匹配该记录(匹配 gen0 的)
        assert r.resolve("p.a", +1, "weight_probe",
                         frozenset({"p.b": 2.0}.items()))["record"]["generation"] == 2
        assert r.resolve("p.a", +1, "weight_probe",
                         frozenset())["record"]["generation"] == 0

    def test_state_correction_reverts(self):
        r = self._replay()
        # gen4 的父代应为空(state_correction 已回退 p.b)
        assert r.parents[4] == frozenset()

    def test_validate_self_consistency(self):
        r = self._replay()
        assert r.validate_against_journal() == []

    def test_duplicate_keys_both_match(self):
        r = self._replay()
        recs = r.index[("p.a", +1, "weight_probe", frozenset({"p.b": 2.0}.items()))]
        assert len(recs) == 2  # gen2 + gen3 同键


class TestStage2ReplayRun:
    def test_replay_with_all_matched(self, monkeypatch):
        monkeypatch.setattr(rp, "PARAM_NAMES", ["p.a", "p.b"])
        recs = [_s2_record(g, "p.a" if g % 2 == 0 else "p.b",
                           1 if g % 3 else -1) for g in range(6)]
        replay = rp.Stage2Replay(recs)
        out = rp.run_stage2_replay(replay, "round_robin", stall_limit=99,
                                   generations=6)
        assert out["gens_run"] == 6
        assert out["unexplored"] <= 6
        # 轮询与记录同序时全部命中(方向可能不同→部分 UNEXPLORED,保守处理)
        assert out["wall_s"] > 0

    def test_unexplored_counted_conservatively(self, monkeypatch):
        monkeypatch.setattr(rp, "PARAM_NAMES", ["p.x", "p.y"])  # 记录里没有的参数
        recs = [_s2_record(0, "p.a", +1)]
        replay = rp.Stage2Replay(recs)
        out = rp.run_stage2_replay(replay, "round_robin", stall_limit=3,
                                   generations=10)
        # 全部 UNEXPLORED → 全 rejected → 3 连停触发机制切换 → probe 仍全拒 → 停
        assert out["archived"] == 0
        assert out["unexplored"] == out["gens_run"]
        assert out["gens_run"] <= 7  # stall3×2 机制 + 停


# ── 阶段 1 策略函数 ──────────────────────────────────────────────────

class TestStage1Strategies:
    def _state(self, sizes, gen=0):
        return {"pools": {s: ["x"] * n for s, n in sizes.items()},
                "gen": gen, "strategy_state": {}}

    def test_actual_picks_largest_pool(self):
        s = self._state({"shuihu": 100, "xiyouji": 5})
        assert rp.s1_actual(s) == "shuihu"

    def test_round_robin_cycles(self):
        s = self._state({"xiyouji": 1, "honglou": 1}, gen=1)
        assert rp.s1_round_robin(s) == "honglou"
        s0 = self._state({"xiyouji": 1}, gen=3)
        assert rp.s1_round_robin(s0) == "xiyouji"  # 空的跳过

    def test_max_marginal_picks_smallest_novel(self, monkeypatch):
        class FakeWorld:
            novels: ClassVar[dict] = {"big": {"n_names": 1000},
                                      "small": {"n_names": 100}}

        monkeypatch.setattr(rp, "_S1_WORLD_REF", FakeWorld)
        s = self._state({"big": 50, "small": 50})
        assert rp.s1_max_marginal(s) == "small"  # 同池容,小书每条降幅大

    def test_ucb1_prefers_untried(self):
        s = self._state({"a": 5, "b": 5})
        ss = s["strategy_state"]
        ss["counts"] = {"a": 3}
        ss["rewards"] = {"a": 0.01}
        assert rp.s1_ucb1(s) == "b"  # 未探索优先

    def test_gens_to_reach_rounding_tolerance(self):
        assert rp.s1_gens_to_reach([0.01, 0.05, 0.0826], 0.08264) == 3
        assert rp.s1_gens_to_reach([0.01, 0.02], 0.5) is None


# ── 阶段 3 轨迹停止规则 ──────────────────────────────────────────────

class TestStage3TrajectoryPolicies:
    def _records(self):
        # 8 连败后 gen 9 入档,再 3 连败
        recs = []
        for i in range(13):
            archived = i == 8
            recs.append({
                "generation": i, "stage": 3,
                "decision": "archived" if archived else "rejected_gate",
                "metrics": {"macro.prompt.recall": 0.50} if archived else None,
                "cost": {"wall_clock_s": 70.0, "llm_calls": 16,
                         "cost_usd": 0.13},
            })
        return recs

    def test_early_stop_misses_improvement(self):
        out = rp.run_stage3_trajectory_policies(self._records(), stall_ks=(3, 99))
        k3 = next(p for p in out if p["stall_K"] == 3)
        k99 = next(p for p in out if p["stall_K"] == 99)
        assert k3["macro_gain_captured"] == 0.0
        assert k3["gens_run"] == 3
        assert k99["macro_gain_captured"] == pytest.approx(
            round(0.50 - 0.46644073024287236, 4))  # 回放输出按 4 位取整
        assert k99["gens_run"] == 13
        assert k99["cost_usd"] > k3["cost_usd"]


# ── commit_journal 元层自动字段 ──────────────────────────────────────

class TestCommitJournalMeta:
    def test_auto_fields_added(self, tmp_path: Path):
        jp = tmp_path / "journal.jsonl"
        rl.commit_journal({"generation": 0, "decision": "archived"}, jp)
        rec = json.loads(jp.read_text(encoding="utf-8").splitlines()[0])
        assert rec["policy_version"] is not None  # 真实 eval_policy 当前版本
        assert len(rec["state_sha256"]) == 64
        assert "context_hash" in rec

    def test_explicit_fields_not_overwritten(self, tmp_path: Path):
        jp = tmp_path / "journal.jsonl"
        rl.commit_journal({"generation": 0, "decision": "x",
                           "policy_version": 99, "state_sha256": "abc",
                           "context_hash": "ctx"}, jp)
        rec = json.loads(jp.read_text(encoding="utf-8").splitlines()[0])
        assert rec["policy_version"] == 99
        assert rec["state_sha256"] == "abc"
        assert rec["context_hash"] == "ctx"

    def test_context_hash_deterministic(self):
        h1 = rl.context_hash({"b": 1, "a": [2, 3]})
        h2 = rl.context_hash({"a": [2, 3], "b": 1})
        assert h1 == h2 and len(h1) == 64


# ── OOD 跨体裁护栏（eval_policy v4）──────────────────────────────────

class TestOodGuard:
    POLICY: ClassVar[dict] = {
        "ood_guard": {
            "metrics": ["m1.orphan_rate", "m4.generic_residue",
                        "geo.unresolved_rate"],
            "applies_to_levels": [3, 4],
            "regression_abs": 0.01,
        },
    }
    BASELINE: ClassVar[dict] = {
        "ood_guard": {"novels": {
            "fanren": {"m1.orphan_rate": 0.043, "m4.generic_residue": 0.095,
                       "geo.unresolved_rate": 0.916},
            "motrings": {"m1.orphan_rate": 0.027, "m4.generic_residue": 0.044,
                         "geo.unresolved_rate": 0.903},
            "pingfan": {"m1.orphan_rate": 0.034, "m4.generic_residue": 0.032,
                        "geo.unresolved_rate": 0.873},
        }},
    }

    def _fake_current(self, fanren_orphan=0.043):
        return {
            "fanren": {"m1.orphan_rate": fanren_orphan,
                       "m4.generic_residue": 0.095, "geo.unresolved_rate": 0.916},
            "motrings": {"m1.orphan_rate": 0.027, "m4.generic_residue": 0.044,
                         "geo.unresolved_rate": 0.903},
            "pingfan": {"m1.orphan_rate": 0.034, "m4.generic_residue": 0.032,
                        "geo.unresolved_rate": 0.873},
        }

    def _patch(self, monkeypatch, current):
        import build_ood_baseline as ood

        monkeypatch.setattr(ood, "compute_ood_metrics", lambda: current)

    def test_pass_at_baseline(self, monkeypatch):
        self._patch(monkeypatch, self._fake_current())
        r = rl.ood_guard_check(self.POLICY, self.BASELINE)
        assert r["passed"] and r["failures"] == []

    def test_regression_blocked(self, monkeypatch):
        self._patch(monkeypatch, self._fake_current(fanren_orphan=0.06))
        r = rl.ood_guard_check(self.POLICY, self.BASELINE)
        assert not r["passed"]
        assert r["failures"] == ["ood:fanren.m1.orphan_rate"]

    def test_at_threshold_passes(self, monkeypatch):
        # 恰好 +0.01 不拒(严格大于口径,与 gate 一致)
        self._patch(monkeypatch, self._fake_current(fanren_orphan=0.053))
        r = rl.ood_guard_check(self.POLICY, self.BASELINE)
        assert r["passed"]

    def test_missing_config_skips(self):
        r = rl.ood_guard_check({}, {})
        assert r["passed"] and "跳过" in r["note"]
