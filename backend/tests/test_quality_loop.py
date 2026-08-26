"""quality_loop 质量改进循环编排单元测试(任务 B)。

全部离线:pytest subprocess / git / 各门采集函数均 mock,不触网不跑真实 pytest。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "quality_loop.py"
_spec = importlib.util.spec_from_file_location("quality_loop", _SCRIPT)
ql = importlib.util.module_from_spec(_spec)
sys.modules["quality_loop"] = ql
_spec.loader.exec_module(ql)


# ── parse_pytest_summary ───────────────────────────────────────────

class TestParsePytestSummary:
    def test_passed_only(self):
        out = "................                                                         [100%]\n16 passed in 0.96s\n"
        r = ql.parse_pytest_summary(out)
        assert r["passed"] == 16 and r["failed"] == 0
        assert r["pass_rate"] == pytest.approx(1.0)

    def test_mixed_counts(self):
        out = "=== short test summary ===\n12 passed, 2 failed, 1 skipped in 3.2s"
        r = ql.parse_pytest_summary(out)
        assert (r["passed"], r["failed"], r["skipped"]) == (12, 2, 1)
        assert r["pass_rate"] == pytest.approx(12 / 14)

    def test_no_counts(self):
        r = ql.parse_pytest_summary("garbage output")
        assert r["passed"] == 0 and r["pass_rate"] is None


# ── history 追加 / 读取 ────────────────────────────────────────────

def _record(tag: str, golden_failed: int = 0, m6_acc: float = 0.6,
            smoke_cov: float = 0.9) -> dict:
    return {
        "timestamp": f"2026-08-26T00:00:00+00:00",
        "tag": tag,
        "git": {"sha": "abc1234", "branch": "beta"},
        "switches": {"RECALL_PASS_ENABLED": True},
        "golden": {"status": "ok", "passed": 16 - golden_failed,
                   "failed": golden_failed, "skipped": 0,
                   "pass_rate": (16 - golden_failed) / 16},
        "m6": {"status": "ok", "shuihu_subtype_accuracy": m6_acc,
               "shuihu_target_met": m6_acc >= 0.55,
               "xiyouji_mock_category": 0.9,
               "xiyouji_not_below_baseline": True},
        "m5": {"status": "missing"},
        "smoke": {"status": "ok", "evidence_coverage": smoke_cov,
                  "span_located_rate": 0.8, "recall_additions": 3},
    }


class TestHistory:
    def test_append_and_load(self, tmp_path):
        path = tmp_path / "quality_history.jsonl"
        ql.append_history(_record("r1"), path)
        ql.append_history(_record("r2"), path)
        records = ql.load_history(path)
        assert len(records) == 2
        assert records[0]["tag"] == "r1"
        assert records[1]["tag"] == "r2"

    def test_load_missing_file(self, tmp_path):
        assert ql.load_history(tmp_path / "nope.jsonl") == []

    def test_load_skips_bad_lines(self, tmp_path):
        path = tmp_path / "h.jsonl"
        path.write_text('{"tag":"ok"}\nnot-json\n{"tag":"ok2"}\n', encoding="utf-8")
        assert [r["tag"] for r in ql.load_history(path)] == ["ok", "ok2"]


# ── delta 对比与回退退出码 ─────────────────────────────────────────

class TestCompare:
    def test_golden_fail_increase_is_hard_regression(self):
        rows = ql.compare_records(_record("a", golden_failed=0),
                                  _record("b", golden_failed=2))
        failed_row = next(r for r in rows if r["key"] == "golden.failed")
        assert failed_row["verdict"] == "fail"
        assert ql.has_hard_regression(rows)

    def test_golden_pass_rate_below_threshold_first_record(self):
        rows = ql.compare_records(None, _record("a", golden_failed=1))
        rate_row = next(r for r in rows if r["key"] == "golden.pass_rate")
        assert rate_row["verdict"] == "fail"
        assert ql.has_hard_regression(rows)

    def test_soft_metric_decline_warns_but_not_fail(self):
        rows = ql.compare_records(_record("a", m6_acc=0.7), _record("b", m6_acc=0.6))
        m6_row = next(r for r in rows if r["key"] == "m6.shuihu_subtype_accuracy")
        assert m6_row["verdict"] == "warn"
        # 但 m6 仍 ≥0.55,布尔门未翻转,不构成硬回退
        assert not ql.has_hard_regression(rows)

    def test_m6_target_flip_is_hard_regression(self):
        rows = ql.compare_records(_record("a", m6_acc=0.6), _record("b", m6_acc=0.4))
        flip_row = next(r for r in rows if r["key"] == "m6.shuihu_target_met")
        assert flip_row["verdict"] == "fail"
        assert ql.has_hard_regression(rows)

    def test_improvement_no_warning(self):
        rows = ql.compare_records(_record("a", smoke_cov=0.8), _record("b", smoke_cov=0.95))
        assert all(r["verdict"] in ("ok", "new") for r in rows)
        assert not ql.has_hard_regression(rows)

    def test_first_record_marks_new(self):
        rows = ql.compare_records(None, _record("a"))
        assert rows and all(r["verdict"] in ("new", "missing") for r in rows)

    def test_missing_gate_degrades(self):
        prev = _record("a")
        curr = _record("b")
        curr["smoke"] = {"status": "missing"}
        rows = ql.compare_records(prev, curr)
        smoke_row = next(r for r in rows if r["key"] == "smoke.evidence_coverage")
        assert smoke_row["verdict"] == "missing"
        assert not ql.has_hard_regression(rows)


class TestRender:
    def test_delta_md_content(self):
        rows = ql.compare_records(_record("a"), _record("b"))
        md = ql.render_delta_md(_record("b"), _record("a"), rows)
        assert "| 指标 | 上次 | 本次 | delta | 判定 |" in md
        assert "golden 正确率" in md
        assert "beta@abc1234" in md


# ── run_loop 集成(mock 各门采集)──────────────────────────────────

def _patch_collectors(monkeypatch):
    monkeypatch.setattr(ql, "git_info", lambda: {"sha": "abc1234", "branch": "beta"})
    monkeypatch.setattr(ql, "current_switches", lambda: {"RECALL_PASS_ENABLED": True})
    monkeypatch.setattr(ql, "collect_m6", lambda: {"status": "missing"})
    monkeypatch.setattr(ql, "collect_m5", lambda: {"status": "missing"})
    monkeypatch.setattr(ql, "collect_smoke", lambda report_dir=None: {"status": "missing"})


class TestRunLoop:
    def test_first_run_appends_history(self, tmp_path, monkeypatch):
        _patch_collectors(monkeypatch)
        monkeypatch.setattr(ql, "HISTORY_PATH", tmp_path / "h.jsonl")
        record, prev, rows, code = ql.run_loop(
            tag="baseline", no_pytest=True, report_dir=tmp_path,
            history_path=tmp_path / "h.jsonl",
        )
        assert code == 0 and prev is None
        assert record["golden"]["status"] == "skipped"
        history = ql.load_history(tmp_path / "h.jsonl")
        assert len(history) == 1
        assert history[0]["tag"] == "baseline"
        assert history[0]["git"]["sha"] == "abc1234"

    def test_golden_regression_exit_code_1(self, tmp_path, monkeypatch):
        _patch_collectors(monkeypatch)
        hist = tmp_path / "h.jsonl"
        good = {"status": "ok", "passed": 16, "failed": 0, "skipped": 0, "pass_rate": 1.0}
        bad = {"status": "failed", "passed": 14, "failed": 2, "skipped": 0,
               "pass_rate": 14 / 16, "returncode": 1}
        ql.run_loop(tag="good", report_dir=tmp_path, history_path=hist,
                    golden_runner=lambda: good)
        _record, _prev, rows, code = ql.run_loop(
            tag="bad", report_dir=tmp_path, history_path=hist,
            golden_runner=lambda: bad,
        )
        assert code == 1
        assert ql.has_hard_regression(rows)
        assert len(ql.load_history(hist)) == 2

    def test_missing_gates_do_not_crash(self, tmp_path, monkeypatch):
        _patch_collectors(monkeypatch)
        _record, _prev, rows, code = ql.run_loop(
            tag="x", no_pytest=True, report_dir=tmp_path,
            history_path=tmp_path / "h.jsonl",
        )
        assert code == 0
        assert rows == []  # 全部 missing,无可对比项


# ── 冒烟产物消费 / missing 降级 ────────────────────────────────────

class TestSmokeGate:
    def test_missing_smoke_artifact(self, tmp_path):
        assert ql.collect_smoke(tmp_path)["status"] == "missing"

    def test_smoke_metrics_extraction(self, tmp_path):
        smoke_json = {
            "mode": "mock",
            "dimensions": {"polarity_fill_rate": 1.0, "rel_subtype_fill_rate": 0.5,
                           "closeness_fill_rate": None},
            "evidence": {"overall_coverage": 0.75, "span_located_rate": 0.5},
            "sanitize": {"invalid_dimension_total": 2, "vote_overrides": 1},
            "recall_pass": {"characters": 1, "relationships": 0, "events": 2},
            "hallucination": {"candidates": ["x"],
                              "actions": [{"name": "x", "action": "removed"}]},
            "usage": {"total_tokens": 750, "cost_usd": 0.001},
        }
        (tmp_path / "smoke_extraction_quality_20260826.json").write_text(
            json.dumps(smoke_json), encoding="utf-8",
        )
        m = ql.collect_smoke(tmp_path)
        assert m["status"] == "ok"
        assert m["evidence_coverage"] == pytest.approx(0.75)
        assert m["recall_additions"] == 3
        assert m["hallucination_removed"] == 1
        assert m["invalid_dimension_total"] == 2

    def test_corrupt_smoke_artifact(self, tmp_path):
        (tmp_path / "smoke_extraction_quality_20260826.json").write_text(
            "not-json", encoding="utf-8",
        )
        assert ql.collect_smoke(tmp_path)["status"] == "missing"
