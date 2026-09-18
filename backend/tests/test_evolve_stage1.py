"""GeoEvolve 阶段 1 测试（scripts/evolve/geo_vocab.py + run_loop.py 阶段 1 部分）。

覆盖：
  - Curator 去重/冲突/语义冲突（geo_vocab.curator_check）
  - anti-hack 黄金集硬编码检测（geo_vocab.anti_hack_filter，§6.3）
  - APPLY 隔离：源文件渲染/剥离往返、write/heal、崩溃回退语义
  - LLM 预算真实计数（run_loop.LlmBudget）
  - 规则提议算子确定性（GeoSupplementDeltaOperator.__call__，伪 pool）
  - geo.unresolved_rate 指标方向接入 GATE

纯本地：不触 LLM、不读真实 DB、不跑 golden pytest、不改真实 geo_resolver.py。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import ClassVar

import pytest

_EVOLVE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "evolve"
if str(_EVOLVE_DIR) not in sys.path:
    sys.path.insert(0, str(_EVOLVE_DIR))

import geo_vocab as gv  # noqa: E402
import run_loop as rl  # noqa: E402

# ── Curator ──────────────────────────────────────────────────────────

class TestCurator:
    def test_pass_for_normal_name(self):
        reason = gv.curator_check(
            "黄泥冈", (35.0, 116.0), existing={}, committed={},
        )
        assert reason is None

    def test_duplicate_in_existing_rejected(self):
        reason = gv.curator_check(
            "长安", (34.0, 108.0), existing={"长安": (34.0, 108.0)}, committed={},
        )
        assert reason and "重复" in reason

    def test_duplicate_in_committed_rejected(self):
        reason = gv.curator_check(
            "黄泥冈", (35.0, 116.0), existing={},
            committed={"黄泥冈": (35.0, 116.0)},
        )
        assert reason and "重复" in reason

    def test_conflict_different_coords_rejected(self):
        reason = gv.curator_check(
            "黄泥冈", (36.0, 117.0), existing={},
            committed={"黄泥冈": (35.0, 116.0)},
        )
        assert reason and "冲突" in reason

    def test_generic_name_semantic_conflict(self):
        # 客栈 是 fact_validator 泛称设施词 → 不该入坐标字典
        reason = gv.curator_check("客栈", (35.0, 116.0), existing={}, committed={})
        assert reason and "语义冲突" in reason

    def test_single_char_rejected(self):
        reason = gv.curator_check("山", (35.0, 116.0), existing={}, committed={})
        assert reason and "形态" in reason


# ── anti-hack（§6.3）─────────────────────────────────────────────────

class TestAntiHack:
    def test_golden_fixture_name_rejected(self):
        golden_text = '{"locations": [{"name": "花果山"}, {"name": "长安"}]}'
        entries = {"花果山": (29.0, 119.0), "黄泥冈": (35.0, 116.0)}
        kept, rejected = gv.anti_hack_filter(entries, golden_text)
        assert rejected == ["花果山"]
        assert list(kept) == ["黄泥冈"]

    def test_real_golden_fixtures_loaded(self):
        # 真实 fixture 文本可加载且非空（防回归：glob 失效导致检测静默失效）
        text = gv.load_golden_texts()
        assert len(text) > 1000

    def test_no_false_positive_on_unrelated_name(self):
        entries = {"黄泥冈": (35.0, 116.0)}
        kept, rejected = gv.anti_hack_filter(entries, "完全不相关的文本")
        assert rejected == [] and kept == entries


# ── 源文件渲染/剥离/自愈（APPLY 隔离）────────────────────────────────

FAKE_SOURCE = '''"""fake geo_resolver."""

_SUPPLEMENT_GEO = {
    "长安": (34.34, 108.94),
}

def resolve():
    return _SUPPLEMENT_GEO
'''


class TestSourceRendering:
    def test_render_then_strip_roundtrip(self):
        entries = {"黄泥冈": (35.0, 116.0), "东门": (38.0, 116.0)}
        patched = gv.render_source(FAKE_SOURCE, entries)
        assert gv.BLOCK_BEGIN in patched
        assert '"黄泥冈": (35.0, 116.0)' in patched
        assert gv.strip_evolve_block(patched) == FAKE_SOURCE

    def test_rendered_block_is_valid_python(self):
        patched = gv.render_source(FAKE_SOURCE, {"黄泥冈": (35.0, 116.0)})
        ast.parse(patched)  # 语法错误即抛

    def test_render_empty_entries_is_pristine(self):
        assert gv.render_source(FAKE_SOURCE, {}) == FAKE_SOURCE

    def test_strip_without_block_is_idempotent(self):
        assert gv.strip_evolve_block(FAKE_SOURCE) == FAKE_SOURCE

    def test_chinese_names_escaped_correctly(self):
        entries = {"梁山泊聚义厅": (35.79, 116.13)}
        patched = gv.render_source(FAKE_SOURCE, entries)
        namespace: dict = {}
        exec(compile(patched, "<test>", "exec"), namespace)
        assert namespace["_SUPPLEMENT_GEO"]["梁山泊聚义厅"] == (35.79, 116.13)
        assert namespace["_SUPPLEMENT_GEO"]["长安"] == (34.34, 108.94)

    def test_write_and_heal_on_tmp_file(self, tmp_path: Path):
        f = tmp_path / "geo_resolver.py"
        f.write_text(FAKE_SOURCE, encoding="utf-8")
        committed = {"东门": (38.0, 116.0)}
        candidate = {**committed, "黄泥冈": (35.0, 116.0)}
        # APPLY 候选 → 模拟评估失败 → 回退到已提交
        gv.write_source_state(candidate, f)
        assert "黄泥冈" in f.read_text(encoding="utf-8")
        gv.write_source_state(committed, f)
        content = f.read_text(encoding="utf-8")
        assert "黄泥冈" not in content and "东门" in content
        # heal：文件与 store 一致 → 不修复
        store = {"entries": {"东门": {"coords": [38.0, 116.0]}}, "rejected": {}}
        assert gv.heal_source(store, f) is False
        # 脏状态（手改/崩溃残留）→ heal 重渲染
        f.write_text(FAKE_SOURCE, encoding="utf-8")  # delta 丢失的脏状态
        assert gv.heal_source(store, f) is True
        assert "东门" in f.read_text(encoding="utf-8")

    def test_crash_recovery_semantics(self, tmp_path: Path):
        """模拟"已应用未评估"崩溃：finally 必须还原到已提交状态。"""
        f = tmp_path / "geo_resolver.py"
        f.write_text(FAKE_SOURCE, encoding="utf-8")
        committed = {"东门": (38.0, 116.0)}
        candidate = {**committed, "黄泥冈": (35.0, 116.0)}
        gv.write_source_state(candidate, f)
        try:
            raise RuntimeError("模拟 EVAL 崩溃")
        except RuntimeError:
            pass
        finally:
            gv.write_source_state(committed, f)
        content = f.read_text(encoding="utf-8")
        assert "黄泥冈" not in content and "东门" in content
        # 语法仍合法
        ast.parse(content)


# ── LLM 预算真实计数 ─────────────────────────────────────────────────

class TestLlmBudget:
    def test_within_limit_ok(self):
        b = rl.LlmBudget(limit=3)
        b.charge()
        b.charge(2)
        assert b.calls == 3

    def test_over_limit_raises(self):
        b = rl.LlmBudget(limit=2)
        b.charge(2)
        with pytest.raises(rl.LlmBudgetExceeded):
            b.charge(1)
        assert b.calls == 3  # 计数照记（超限可见）


# ── 规则提议算子（伪 pool，确定性）───────────────────────────────────

def _fake_pools() -> dict:
    def cand(name, freq):
        return {"name": name, "coords": (35.0, 116.0),
                "ancestor": "父地", "frequency": freq}

    return {
        "shuihu": {"unresolved_rate": 0.76, "names": 1751, "pool_size": 3,
                   "candidates": [cand("乙地", 5), cand("甲地", 9), cand("丙地", 1)],
                   "curator_rejected": {}},
        "xiyouji": {"unresolved_rate": 0.81, "names": 1030, "pool_size": 2,
                    "candidates": [cand("丁地", 3), cand("戊地", 2)],
                    "curator_rejected": {}},
        "honglou": {"unresolved_rate": 0.87, "names": 860, "pool_size": 0,
                    "candidates": [], "curator_rejected": {}},
        "sanguo": {"unresolved_rate": 0.66, "names": 1322, "pool_size": 0,
                   "candidates": [], "curator_rejected": {}},
        "fengshen": {"unresolved_rate": 0.79, "names": 655, "pool_size": 0,
                     "candidates": [], "curator_rejected": {}},
    }


class TestProposer:
    def test_picks_largest_pool_and_top_k_by_frequency(self):
        op = gv.GeoSupplementDeltaOperator(batch_size=2)
        c = op({}, {"pools": _fake_pools()})
        assert c["target_novel"] == "shuihu"
        assert list(c["genome_diff"]["vocab_delta.add"]) == ["甲地", "乙地"]
        assert not c.get("exhausted")

    def test_exhausted_when_all_pools_empty(self):
        op = gv.GeoSupplementDeltaOperator(batch_size=10)
        pools = _fake_pools()
        for p in pools.values():
            p["pool_size"] = 0
            p["candidates"] = []
        c = op({}, {"pools": pools})
        assert c["exhausted"]
        assert c["genome_diff"] == {}

    def test_provenance_recorded(self):
        op = gv.GeoSupplementDeltaOperator(batch_size=1)
        c = op({}, {"pools": _fake_pools()})
        assert c["ancestors"] == {"甲地": "父地"}
        assert c["frequencies"] == {"甲地": 9}


# ── geo.unresolved_rate 指标方向（GATE 接入）──────────────────────────

class TestGeoMetricDirection:
    POLICY: ClassVar[dict] = {
        "thresholds": {"metric_regression_abs": 0.01,
                       "per_novel_regression_abs": 0.01,
                       "golden_pass_rate_min": 1.0},
    }

    def test_direction_registered(self):
        assert rl.direction_for("shuihu.geo.unresolved_rate") == "lower"

    def test_improvement_passes_gate(self):
        key = "shuihu.geo.unresolved_rate"
        ref = {key: 0.765}
        cur = {key: 0.759}
        assert rl.gate(cur, ref, self.POLICY)["passed"]

    def test_regression_beyond_threshold_fails(self):
        key = "shuihu.geo.unresolved_rate"
        ref = {key: 0.765}
        cur = {key: 0.780}  # +0.015 > 0.01
        result = rl.gate(cur, ref, self.POLICY)
        assert not result["passed"]
        assert key in result["failures"]


# ── delta 存储 ───────────────────────────────────────────────────────

class TestDeltaStore:
    def test_load_missing_returns_empty(self, tmp_path: Path):
        store = gv.load_delta(tmp_path / "nope.json")
        assert store["entries"] == {} and store["rejected"] == {}

    def test_save_load_roundtrip(self, tmp_path: Path):
        p = tmp_path / "delta.json"
        store = {"version": 1, "updated_at": None,
                 "entries": {"东门": {"coords": [38.0, 116.0], "novel": "shuihu",
                                     "ancestor": "沧州", "frequency": 9,
                                     "generation": 0}},
                 "rejected": {}}
        gv.save_delta(store, p)
        loaded = gv.load_delta(p)
        assert gv.committed_coords(loaded) == {"东门": (38.0, 116.0)}

    def test_mark_rejected_persisted(self, tmp_path: Path):
        p = tmp_path / "delta.json"
        store = gv.load_delta(p)
        gv.mark_rejected(store, ["济州"], "anti-hack: 命中 golden fixture 原文", p)
        loaded = gv.load_delta(p)
        assert "济州" in loaded["rejected"]
        assert "anti-hack" in loaded["rejected"]["济州"]["reason"]
