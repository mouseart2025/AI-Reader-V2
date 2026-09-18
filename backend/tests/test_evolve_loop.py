"""GeoEvolve 阶段 0 循环骨架测试（scripts/evolve/run_loop.py）。

覆盖（对应交付物 6）：
  - Pareto 支配 / 共存 / 种群上限逻辑（archive_candidate, dominates）
  - GATE 回归判定边界（gate：阈值压线不拒、超线即拒、分小说记账、golden 硬阈值）
  - frozen manifest 校验（build_manifest / verify_manifest：篡改检出、缺文件检出）
  - journal 追加格式（commit_journal：JSONL、lineage 必填键）

纯本地测试：不触发 LLM、不读真实 DB、不跑 golden pytest。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_EVOLVE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "evolve"
if str(_EVOLVE_DIR) not in sys.path:
    sys.path.insert(0, str(_EVOLVE_DIR))

import run_loop as rl  # noqa: E402

# ── 公共夹具 ─────────────────────────────────────────────────────────

POLICY = {
    "thresholds": {
        "metric_regression_abs": 0.01,
        "per_novel_regression_abs": 0.01,
        "golden_pass_rate_min": 1.0,
    },
    "pareto": {"population_max": 8},
    "budget": {"wall_clock_seconds_per_generation": 1800},
}

# 分小说指标键（higher/lower 各一），方向口径来自 run_loop.PER_NOVEL_METRICS
RECALL = "xiyouji.m2.recall_proxy"      # higher
ORPHAN = "xiyouji.m1.orphan_rate"       # lower


def _candidate(metrics: dict, generation: int = 1,
               parent_metrics: dict | None = None) -> dict:
    return {
        "generation": generation,
        "operator": "test",
        "genome_diff": {"weights_params.loci.recall_pass_min_signals.current": 3},
        "metrics": metrics,
        "parent_metrics": parent_metrics if parent_metrics is not None else {},
    }


# ── Pareto：支配 / 共存 / 上限 ───────────────────────────────────────

class TestDominates:
    def test_dominates_when_all_no_worse_and_one_strictly_better(self):
        a = {RECALL: 0.6, ORPHAN: 0.30}
        b = {RECALL: 0.5, ORPHAN: 0.30}
        assert rl.dominates(a, b)
        assert not rl.dominates(b, a)

    def test_lower_direction_normalized(self):
        # orphan_rate 越低越好：0.25 支配 0.30
        a = {RECALL: 0.5, ORPHAN: 0.25}
        b = {RECALL: 0.5, ORPHAN: 0.30}
        assert rl.dominates(a, b)

    def test_incomparable_when_tradeoff(self):
        a = {RECALL: 0.6, ORPHAN: 0.35}
        b = {RECALL: 0.5, ORPHAN: 0.30}
        assert not rl.dominates(a, b)
        assert not rl.dominates(b, a)

    def test_no_common_keys_not_dominating(self):
        assert not rl.dominates({RECALL: 0.6}, {"honglou.m2.recall_proxy": 0.5})


class TestArchive:
    def test_new_candidate_dominating_incumbent_replaces_it(self):
        incumbent = _candidate({RECALL: 0.5, ORPHAN: 0.30}, generation=0)
        challenger = _candidate(
            {RECALL: 0.6, ORPHAN: 0.30}, generation=1,
            parent_metrics={RECALL: 0.5, ORPHAN: 0.30},
        )
        frontier, decision = rl.archive_candidate([incumbent], challenger, 8)
        assert decision == "archived"
        assert len(frontier) == 1
        assert frontier[0]["generation"] == 1

    def test_dominated_candidate_rejected(self):
        incumbent = _candidate({RECALL: 0.6, ORPHAN: 0.30}, generation=0)
        challenger = _candidate(
            {RECALL: 0.5, ORPHAN: 0.30}, generation=1,
            parent_metrics={RECALL: 0.4, ORPHAN: 0.35},  # 比父代好，但输给现任
        )
        frontier, decision = rl.archive_candidate([incumbent], challenger, 8)
        assert decision == "rejected_dominated"
        assert len(frontier) == 1
        assert frontier[0]["generation"] == 0

    def test_incomparable_candidates_coexist(self):
        incumbent = _candidate({RECALL: 0.60, ORPHAN: 0.35}, generation=0)
        challenger = _candidate(
            {RECALL: 0.55, ORPHAN: 0.25}, generation=1,
            parent_metrics={RECALL: 0.50, ORPHAN: 0.40},  # 对父代严格改善 recall
        )
        frontier, decision = rl.archive_candidate([incumbent], challenger, 8)
        assert decision == "archived"
        assert len(frontier) == 2  # 互不支配 → 共存

    def test_identity_mutation_rejected_no_improvement(self):
        # 恒等变异：指标与父代完全一致 → 质量不降但无一维严格改善（dry-run 路径）
        vec = {RECALL: 0.5, ORPHAN: 0.30}
        frontier, decision = rl.archive_candidate(
            [], _candidate(vec, generation=0, parent_metrics=dict(vec)), 8,
        )
        assert decision == "rejected_no_improvement"
        assert frontier == []

    def test_regression_versus_parent_rejected(self):
        challenger = _candidate(
            {RECALL: 0.6, ORPHAN: 0.40}, generation=1,
            parent_metrics={RECALL: 0.5, ORPHAN: 0.30},  # recall 涨但 orphan 退化
        )
        frontier, decision = rl.archive_candidate([], challenger, 8)
        assert decision == "rejected_no_improvement"
        assert frontier == []

    def test_population_cap_evicts_oldest(self):
        frontier = []
        parent = {RECALL: 0.3, ORPHAN: 0.50}
        # 两个互不支配的改善候选，上限 1 → 第二个入档时淘汰最老者
        c0 = _candidate({RECALL: 0.50, ORPHAN: 0.45}, generation=0,
                        parent_metrics=parent)
        c1 = _candidate({RECALL: 0.45, ORPHAN: 0.40}, generation=1,
                        parent_metrics=parent)
        frontier, d0 = rl.archive_candidate(frontier, c0, 1)
        frontier, d1 = rl.archive_candidate(frontier, c1, 1)
        assert d0 == "archived"
        assert d1 == "archived_evicted"
        assert len(frontier) == 1
        assert frontier[0]["generation"] == 1


# ── GATE：回归判定边界 ───────────────────────────────────────────────

class TestGate:
    def test_regression_exactly_at_threshold_passes(self):
        # "单项 >0.01 绝对回归即拒"：恰好 0.01 不拒（严格大于）
        ref = {RECALL: 0.75}
        cur = {RECALL: 0.74}
        result = rl.gate(cur, ref, POLICY)
        assert result["passed"]
        assert result["failures"] == []

    def test_regression_beyond_threshold_fails(self):
        ref = {RECALL: 0.75}
        cur = {RECALL: 0.73}
        result = rl.gate(cur, ref, POLICY)
        assert not result["passed"]
        assert RECALL in result["failures"]

    def test_lower_metric_regression_direction(self):
        # orphan_rate 越低越好：上升超阈值即拒
        ref = {ORPHAN: 0.30}
        cur = {ORPHAN: 0.32}
        assert not rl.gate(cur, ref, POLICY)["passed"]
        assert rl.gate({ORPHAN: 0.31}, ref, POLICY)["passed"]  # 恰好压线

    def test_per_novel_regression_uses_novel_threshold(self):
        # 分小说键同样按 per_novel_regression_abs 判定（灾难性遗忘护栏）
        ref = {"shuihu.m5.m5": 0.85}
        cur = {"shuihu.m5.m5": 0.83}
        result = rl.gate(cur, ref, POLICY)
        assert not result["passed"]
        assert "shuihu.m5.m5" in result["failures"]

    def test_improvement_passes(self):
        ref = {RECALL: 0.50, ORPHAN: 0.30}
        cur = {RECALL: 0.60, ORPHAN: 0.25}
        assert rl.gate(cur, ref, POLICY)["passed"]

    def test_missing_keys_do_not_fail(self):
        ref = {RECALL: 0.50, "honglou.m2.recall_proxy": 0.39}
        cur = {RECALL: 0.50}  # honglou 缺失 → 记 missing，不算回归
        result = rl.gate(cur, ref, POLICY)
        assert result["passed"]
        assert any(r["verdict"] == "missing" for r in result["rows"])

    def test_golden_pass_rate_hard_floor(self):
        cur = {RECALL: 0.50, "golden.pass_rate": 0.9375}
        ref = {RECALL: 0.50, "golden.pass_rate": 1.0}
        result = rl.gate(cur, ref, POLICY)
        assert not result["passed"]
        assert "golden.pass_rate" in result["failures"]


# ── frozen manifest：篡改检出 ────────────────────────────────────────

@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """搭一个最小假仓库，含冻结清单要求的全部文件。"""
    files = {
        "backend/scripts/quality_dashboard.py": "DASH = 1\n",
        "backend/scripts/quality_loop.py": "LOOP = 1\n",
        "backend/src/utils/topology_metrics.py": "TOPO = 1\n",
        "backend/scripts/evolve/eval_policy.yaml": "version: 0\n",
        "backend/tests/fixtures/golden_standard_a.json": '{"a": 1}\n',
        "backend/tests/fixtures/golden_standard_b.json": '{"b": 2}\n',
    }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


class TestFrozenManifest:
    def test_roundtrip_passes(self, fake_repo: Path, tmp_path: Path):
        manifest = rl.build_manifest(fake_repo)
        assert len(manifest["files"]) == 6  # 4 固定 + 2 glob
        mp = tmp_path / "manifest.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        assert rl.verify_manifest(mp, fake_repo) == []

    def test_tampered_file_detected(self, fake_repo: Path, tmp_path: Path):
        manifest = rl.build_manifest(fake_repo)
        mp = tmp_path / "manifest.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        # 篡改冻结的评估器
        (fake_repo / "backend/scripts/quality_loop.py").write_text(
            "LOOP = 2  # hacked\n", encoding="utf-8"
        )
        mismatched = rl.verify_manifest(mp, fake_repo)
        assert any("quality_loop.py" in m and "sha256" in m for m in mismatched)

    def test_tampered_golden_fixture_detected(self, fake_repo: Path, tmp_path: Path):
        manifest = rl.build_manifest(fake_repo)
        mp = tmp_path / "manifest.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        (fake_repo / "backend/tests/fixtures/golden_standard_a.json").write_text(
            '{"a": 999}\n', encoding="utf-8"
        )
        mismatched = rl.verify_manifest(mp, fake_repo)
        assert any("golden_standard_a.json" in m for m in mismatched)

    def test_deleted_file_detected(self, fake_repo: Path, tmp_path: Path):
        manifest = rl.build_manifest(fake_repo)
        mp = tmp_path / "manifest.json"
        mp.write_text(json.dumps(manifest), encoding="utf-8")
        (fake_repo / "backend/src/utils/topology_metrics.py").unlink()
        mismatched = rl.verify_manifest(mp, fake_repo)
        assert any("topology_metrics.py" in m and "文件缺失" in m for m in mismatched)

    def test_missing_manifest_detected(self, tmp_path: Path):
        mismatched = rl.verify_manifest(tmp_path / "nope.json", tmp_path)
        assert mismatched and "manifest missing" in mismatched[0]

    def test_build_manifest_fails_on_missing_target(self, tmp_path: Path):
        # 冻结目标缺失时生成即失败（防静默漏冻结）
        with pytest.raises(SystemExit):
            rl.build_manifest(tmp_path)


# ── journal：追加格式 ────────────────────────────────────────────────

class TestJournal:
    def _record(self, generation: int) -> dict:
        return {
            "generation": generation,
            "timestamp": "2026-09-18T00:00:00+00:00",
            "operator": "identity",
            "hypothesis": "恒等变异",
            "genome_diff": {},
            "eval_backend": "cached",
            "metrics": {RECALL: 0.5},
            "gate": {"passed": True, "failures": []},
            "cost": {"wall_clock_s": 0.001, "llm_calls": 0, "cost_usd": 0.0},
            "parent": {"type": "baseline"},
            "decision": "rejected_no_improvement",
            "dry_run": True,
        }

    def test_append_creates_valid_jsonl(self, tmp_path: Path):
        jp = tmp_path / "sub" / "journal.jsonl"
        rl.commit_journal(self._record(0), jp)
        rl.commit_journal(self._record(1), jp)
        lines = jp.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        r0, r1 = (json.loads(line) for line in lines)
        assert r0["generation"] == 0 and r1["generation"] == 1

    def test_record_has_full_lineage_keys(self, tmp_path: Path):
        # §6.2：diff/假设/指标向量/成本/父代指针/决策 全部可溯源
        jp = tmp_path / "journal.jsonl"
        rl.commit_journal(self._record(0), jp)
        rec = json.loads(jp.read_text(encoding="utf-8").splitlines()[0])
        for key in ("generation", "timestamp", "operator", "hypothesis",
                    "genome_diff", "metrics", "cost", "parent", "decision"):
            assert key in rec, f"journal 记录缺 lineage 键: {key}"

    def test_next_generation_from_journal(self, tmp_path: Path):
        jp = tmp_path / "journal.jsonl"
        assert rl.next_generation(jp) == 0
        rl.commit_journal(self._record(0), jp)
        rl.commit_journal(self._record(1), jp)
        assert rl.next_generation(jp) == 2

    def test_append_is_append_only(self, tmp_path: Path):
        jp = tmp_path / "journal.jsonl"
        rl.commit_journal(self._record(0), jp)
        first = jp.read_text(encoding="utf-8")
        rl.commit_journal(self._record(1), jp)
        assert jp.read_text(encoding="utf-8").startswith(first)


# ── 配置校验（genome / eval_policy 结构）─────────────────────────────

class TestConfigValidation:
    def test_repo_genome_yaml_valid(self):
        data = rl.load_yaml_config(rl.GENOME_PATH, rl.validate_genome)
        assert set(data["genes"]) == {"vocab_dict", "weights_params", "prompts",
                                      "pipeline", "model"}

    def test_repo_eval_policy_yaml_valid(self):
        data = rl.load_yaml_config(rl.EVAL_POLICY_PATH, rl.validate_eval_policy)
        assert data["datasets"]["inner"]["novels"] == ["xiyouji", "honglou", "shuihu"]
        assert data["datasets"]["holdout"]["novels"] == ["sanguo", "fengshen"]

    def test_genome_missing_level_rejected(self):
        bad = {"version": 0, "genes": {"vocab_dict": {"level": 1, "loci": {}}}}
        with pytest.raises(rl.ConfigError):
            rl.validate_genome(bad)

    def test_eval_policy_bad_threshold_rejected(self):
        bad = {"datasets": {"inner": {"novels": ["a"]}, "holdout": {"novels": ["b"]}},
               "thresholds": {"metric_regression_abs": -1},
               "pareto": {"population_max": 8},
               "budget": {"wall_clock_seconds_per_generation": 1800}}
        with pytest.raises(rl.ConfigError):
            rl.validate_eval_policy(bad)
