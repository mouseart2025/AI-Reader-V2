"""GeoEvolve 阶段 2 测试（weight_jitter.py + evolve_params.py + run_loop 阶段 2 部分）。

覆盖：
  - 参数注入钩子开/关行为（evolve_param：关=代码原值,开=注入生效,类型对齐）
  - 权重算子：clamp 边界、int 取整、零值加性退化、扰动必变、轮询确定性、
    动量（改善同向/被拒反向）
  - 参数历史重建（build_param_history）
  - 阶段 2 指标向量拍平（stage2_metric_vector）
  - 复测降级判定（stability_check_stage2 的阈值逻辑,子进程打桩）

纯本地：不触 LLM、不读真实 DB、不跑 rebuild/golden 子进程。
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

import run_loop as rl  # noqa: E402
import weight_jitter as wj  # noqa: E402

from src.services.geo_skills import evolve_params as ep  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("EVOLVE_PARAMS_JSON", raising=False)
    ep.reset_cache()
    yield
    ep.reset_cache()


# ── 注入钩子（生产行为不变 + 注入生效）──────────────────────────────

class TestEvolveParam:
    def test_off_returns_code_default(self):
        # 开关关：返回代码里的字面量（生产路径）
        assert ep.evolve_param("vote_builder.peer_discount", 0.33) == 0.33
        assert ep.evolve_param("knowledge_prior.prior_weight", 20) == 20
        assert ep.evolve_param("edmonds.max_children", 30) == 30

    def test_on_injects_override(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "params.json"
        p.write_text(json.dumps({
            "vote_builder.peer_discount": 0.5,
            "edmonds.max_children": 45,
            "vote_builder.baseline_weight": 1.15,
        }), encoding="utf-8")
        monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
        assert ep.evolve_param("vote_builder.peer_discount", 0.33) == 0.5
        assert ep.evolve_param("edmonds.max_children", 30) == 45
        assert ep.evolve_param("vote_builder.baseline_weight", 1) == 1.15

    def test_on_missing_key_falls_back_to_default(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "params.json"
        p.write_text(json.dumps({"other.key": 1}), encoding="utf-8")
        monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
        assert ep.evolve_param("edmonds.name_contain_weight", 25.0) == 25.0

    def test_missing_file_treated_as_off(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(tmp_path / "nope.json"))
        assert ep.evolve_param("knowledge_prior.prior_weight", 20) == 20

    def test_corrupt_file_treated_as_off(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
        assert ep.evolve_param("knowledge_prior.prior_weight", 20) == 20

    def test_reload_on_mtime_change(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "params.json"
        p.write_text(json.dumps({"k": 1}), encoding="utf-8")
        monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
        assert ep.evolve_param("k", 0) == 1
        import os
        import time

        p.write_text(json.dumps({"k": 2}), encoding="utf-8")
        os.utime(p, (time.time() + 2, time.time() + 2))  # 保证 mtime 变化
        assert ep.evolve_param("k", 0) == 2


# ── 权重算子 ────────────────────────────────────────────────────────

LOCI: dict = {
    "a.float_param": {"default": 0.5, "range": [0.0, 2.0], "type": "float",
                      "description": "浮点"},
    "b.int_param": {"default": 30, "range": [15, 60], "type": "int",
                    "description": "整数"},
    "c.weight": {"default": 20, "range": [5.0, 40.0], "type": "float",
                 "description": "权重"},
    "d.not_in_pool": {"default": 2, "current": 2},  # 无 range → 不进池
}


class TestWeightJitterOperator:
    def setup_method(self):
        self.op = wj.WeightJitterOperator(LOCI)

    def test_pool_excludes_loci_without_range(self):
        assert "d.not_in_pool" not in self.op.param_names
        assert len(self.op.param_names) == 3

    def test_round_robin_deterministic(self):
        seq = [self.op.pick_param(g) for g in range(6)]
        assert seq == ["a.float_param", "b.int_param", "c.weight"] * 2

    def test_perturb_within_range(self):
        for _ in range(50):
            new, _d = self.op.perturb("a.float_param", 0.5, +1, 0.15)
            assert 0.0 <= new <= 2.0

    def test_perturb_clamps_and_flips_at_boundary(self):
        # 1.9 + 20% 乘性 = 2.28 > hi=2.0 → 反向 → 1.9*0.8=1.52
        new, direction = self.op.perturb("a.float_param", 1.9, +1, 0.20)
        assert direction == -1
        assert new == pytest.approx(1.52)

    def test_perturb_int_rounding(self):
        new, _d = self.op.perturb("b.int_param", 30, +1, 0.10)
        assert isinstance(new, int)
        assert 15 <= new <= 60

    def test_perturb_int_minimum_step(self):
        # int 参数 ±10% 可能四舍五入回原值 → 最小步 ±1
        new, _d = self.op.perturb("b.int_param", 16, -1, 0.10)  # 16*0.9=14.4→clamp15?
        assert new != 16 or new == 15  # clamp 到 lo=15
        assert 15 <= new <= 60

    def test_perturb_zero_current_additive_fallback(self):
        # current=0 时乘性扰动恒 0 → 加性(量程比例)
        new, _d = self.op.perturb("a.float_param", 0.0, +1, 0.15)
        assert new > 0

    def test_momentum_same_direction_after_success(self):
        ctx = {"weights_state": {"a.float_param": 0.6}, "generation": 0,
               "param_history": {"a.float_param": {"decision": "archived",
                                                   "direction": +1}}}
        c = self.op({}, ctx)
        assert c["param"] == "a.float_param"
        new_val = c["genome_diff"]["weights.set"]["a.float_param"]
        assert new_val > 0.6  # 同向(+)

    def test_reverse_direction_after_rejection(self):
        ctx = {"weights_state": {"a.float_param": 0.6}, "generation": 0,
               "param_history": {"a.float_param": {"decision": "rejected_no_improvement",
                                                   "direction": +1}}}
        c = self.op({}, ctx)
        new_val = c["genome_diff"]["weights.set"]["a.float_param"]
        assert new_val < 0.6  # 反向(-)

    def test_single_param_attribution(self):
        ctx = {"weights_state": {}, "generation": 3, "param_history": {}}
        c = self.op({}, ctx)
        assert len(c["genome_diff"]["weights.set"]) == 1  # 单参数,归因清晰

    def test_history_build(self):
        journal = [
            {"operator": "weight_jitter", "param": "a.float_param",
             "decision": "archived", "direction": -1},
            {"operator": "weight_jitter", "param": "c.weight",
             "decision": "rejected_gate", "direction": +1},
            {"operator": "weight_probe", "param": "c.weight",
             "decision": "archived", "direction": +1},  # probe 也算入动量谱系
            {"operator": "geo_supplement_delta", "decision": "archived"},  # 其它算子忽略
        ]
        h = wj.build_param_history(journal)
        assert h == {"a.float_param": {"decision": "archived", "direction": -1},
                     "c.weight": {"decision": "archived", "direction": +1}}


class TestWeightProbeOperator:
    def test_step_range_larger_than_jitter(self):
        probe = wj.WeightProbeOperator(LOCI)
        assert probe.name == "weight_probe"
        assert probe.step_min >= 0.30
        assert probe.step_max <= 0.60

    def test_probe_can_cross_semantic_threshold(self):
        # jitter 跨不过的先验阈值(20 vs 15),probe 可以跨(20*(1-0.6)=8 < 15)
        probe = wj.WeightProbeOperator(LOCI)
        new, direction = probe.perturb("c.weight", 20, -1, 0.60)
        assert direction == -1
        assert new <= 20 * 0.7  # 至少 -30%
        assert new >= 5.0       # clamp 在范围内

    def test_probe_int_param(self):
        probe = wj.WeightProbeOperator(LOCI)
        new, _d = probe.perturb("b.int_param", 30, +1, 0.40)
        assert isinstance(new, int) and 15 <= new <= 60 and new != 30

    def test_effective_params_merge(self):
        eff = wj.effective_params(LOCI, {"a.float_param": 1.2})
        assert eff["a.float_param"] == 1.2
        assert eff["b.int_param"] == 30  # default
        assert "d.not_in_pool" not in eff


# ── 阶段 2 指标向量 ─────────────────────────────────────────────────

class TestStage2MetricVector:
    def test_flatten_topo_and_struct(self):
        raw = {
            "xiyouji": {"parent_precision": 0.34, "parent_recall": 0.32,
                        "chain_accuracy": 0.26, "max_children": 109,
                        "root_count": 1, "rebuild_s": 0.9},
            "sanguo": {"max_children": 64, "root_count": 1, "rebuild_s": 1.0},
        }
        vec = rl.stage2_metric_vector(raw)
        assert vec["xiyouji.topo.parent_precision"] == 0.34
        assert vec["xiyouji.rebuild.max_children"] == 109.0
        assert "sanguo.topo.parent_precision" not in vec  # 留出集无 golden
        assert vec["sanguo.rebuild.root_count"] == 1.0
        assert "xiyouji.rebuild_s" not in vec  # 过程元数据不入向量

    def test_directions_registered(self):
        assert rl.direction_for("xiyouji.topo.parent_precision") == "higher"
        assert rl.direction_for("xiyouji.topo.chain_accuracy") == "higher"
        assert rl.direction_for("sanguo.rebuild.max_children") == "lower"
        assert rl.direction_for("sanguo.rebuild.root_count") == "lower"


# ── 复测降级（§6.3,子进程打桩）──────────────────────────────────────

class TestStabilityCheck:
    POLICY: ClassVar[dict] = {"metrics": {"stability_jitter_threshold": 0.01}}

    def _ref_vec(self):
        return {"xiyouji.topo.parent_precision": 0.40,
                "xiyouji.topo.parent_recall": 0.38,
                "honglou.topo.parent_precision": 0.60,
                "sanguo.rebuild.max_children": 64.0}

    def _raw(self, precision_xy: float, precision_hl: float):
        return {
            "xiyouji": {"parent_precision": precision_xy, "parent_recall": 0.38,
                        "chain_accuracy": 0.3, "max_children": 100, "root_count": 1},
            "honglou": {"parent_precision": precision_hl, "parent_recall": 0.5,
                        "chain_accuracy": 0.3, "max_children": 90, "root_count": 1},
            "sanguo": {"max_children": 64, "root_count": 1},
        }

    def test_stable_when_jitter_under_threshold(self, monkeypatch):
        monkeypatch.setattr(rl, "compute_weight_metrics_subprocess",
                            lambda **kw: self._raw(0.405, 0.598))
        stab = rl.stability_check_stage2({}, self._ref_vec(), self.POLICY,
                                         seeds=(1,))
        assert stab["stable"]
        assert stab["max_jitter"] <= 0.01

    def test_unstable_when_jitter_exceeds(self, monkeypatch):
        monkeypatch.setattr(rl, "compute_weight_metrics_subprocess",
                            lambda **kw: self._raw(0.44, 0.60))  # 西游 precision 抖 0.04
        stab = rl.stability_check_stage2({}, self._ref_vec(), self.POLICY,
                                         seeds=(1,))
        assert not stab["stable"]
        assert stab["max_jitter"] == pytest.approx(0.04, abs=1e-6)

    def test_struct_keys_not_judged(self, monkeypatch):
        # rebuild.* 结构护栏键不参与 topo 抖动判定(只判 .topo. 键)
        raw = self._raw(0.40, 0.60)
        raw["sanguo"]["max_children"] = 999
        monkeypatch.setattr(rl, "compute_weight_metrics_subprocess",
                            lambda **kw: raw)
        stab = rl.stability_check_stage2({}, self._ref_vec(), self.POLICY,
                                         seeds=(1,))
        assert stab["stable"]

    def test_baseline_relative_rule_stable_at_noise_floor(self, monkeypatch):
        # v2.2:候选抖动 == 基线噪声底(0.0209) → 不降级(旧绝对口径会误杀)
        monkeypatch.setattr(rl, "compute_weight_metrics_subprocess",
                            lambda **kw: self._raw(0.4209, 0.60))
        stab = rl.stability_check_stage2({}, self._ref_vec(), self.POLICY,
                                         baseline_jitter=0.0209, seeds=(1,))
        assert stab["stable"]
        assert stab["baseline_jitter"] == 0.0209

    def test_baseline_relative_rule_downgrade_beyond_noise(self, monkeypatch):
        # 候选抖动 0.06 > 基线 0.0209 + 0.01 → 降级
        monkeypatch.setattr(rl, "compute_weight_metrics_subprocess",
                            lambda **kw: self._raw(0.46, 0.60))
        stab = rl.stability_check_stage2({}, self._ref_vec(), self.POLICY,
                                         baseline_jitter=0.0209, seeds=(1,))
        assert not stab["stable"]
        assert "基线噪声底" in stab["rule"]


# ── ARCHIVE 的 policy 容差(JIT 与 GATE 同口径)──────────────────────

class TestArchiveWithPolicy:
    POLICY: ClassVar[dict] = {
        "thresholds": {"metric_regression_abs": 0.01,
                       "per_novel_regression_abs": 0.01,
                       "golden_pass_rate_min": 1.0,
                       "guard_overrides": {"rebuild.max_children": {"relative": 1.5}}},
    }

    def _candidate(self, metrics, parent):
        return {"generation": 1, "operator": "weight_probe",
                "genome_diff": {}, "metrics": metrics, "parent_metrics": parent}

    def test_guard_increase_within_relative_caliber_not_worse(self):
        # topo 严格改善 + max_children +36%(1.5x 口径内) → 入档
        parent = {"shuihu.topo.parent_precision": 0.60,
                  "sanguo.rebuild.max_children": 64.0}
        cand = {"shuihu.topo.parent_precision": 0.62,
                "sanguo.rebuild.max_children": 87.0}
        frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8, self.POLICY)
        assert decision == "archived"
        assert len(frontier) == 1

    def test_guard_increase_beyond_relative_caliber_is_worse(self):
        parent = {"shuihu.topo.parent_precision": 0.60,
                  "sanguo.rebuild.max_children": 64.0}
        cand = {"shuihu.topo.parent_precision": 0.62,
                "sanguo.rebuild.max_children": 100.0}  # +56% > 1.5x
        frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8, self.POLICY)
        assert decision == "rejected_no_improvement"
        assert frontier == []

    def test_rate_metric_still_strict(self):
        # 率型指标仍按 0.01:topo 改善但另一小说 recall 退 0.02 → 拒
        parent = {"shuihu.topo.parent_precision": 0.60,
                  "honglou.topo.parent_recall": 0.54}
        cand = {"shuihu.topo.parent_precision": 0.62,
                "honglou.topo.parent_recall": 0.52}
        _frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8, self.POLICY)
        assert decision == "rejected_no_improvement"

    def test_no_policy_strictest(self):
        # 无 policy:任何维(含计数护栏)变差即拒
        parent = {"shuihu.topo.parent_precision": 0.60,
                  "sanguo.rebuild.max_children": 64.0}
        cand = {"shuihu.topo.parent_precision": 0.62,
                "sanguo.rebuild.max_children": 65.0}
        _frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8)
        assert decision == "rejected_no_improvement"
