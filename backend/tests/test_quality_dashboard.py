"""Story Q0 质量仪表盘单元测试（AC6）。

全部使用构造数据验证指标口径：不依赖真实 DB、不调用 LLM
（LLM 交互通过 monkeypatch 模块内 deepseek_chat 桩掉）。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "quality_dashboard.py"
_spec = importlib.util.spec_from_file_location("quality_dashboard", _SCRIPT)
qd = importlib.util.module_from_spec(_spec)
sys.modules["quality_dashboard"] = qd
_spec.loader.exec_module(qd)


def _run(coro):
    """运行协程并恢复主线程 event loop。

    asyncio.run() 结束后会把当前 loop 置 None，导致后续用
    asyncio.get_event_loop() 的既有测试（test_settings）在 3.13 下失败。
    """
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


# ── 构造数据：一棵小层级树 ──────────────────────────────────────────
# 天下(uber) ← 主世界 ← 东胜神洲 ← 傲来国 ← 花果山 ← 水帘洞
#                  ← 灵山
PARENTS = {
    "主世界": "天下",
    "东胜神洲": "主世界",
    "傲来国": "东胜神洲",
    "花果山": "傲来国",
    "水帘洞": "花果山",
    "灵山": "主世界",
}
UBER = "天下"


class TestFreezeHelpers:
    def test_md5_file(self, tmp_path):
        f = tmp_path / "x.bin"
        f.write_bytes(b"hello frozen db")
        assert qd.md5_file(f) == hashlib.md5(b"hello frozen db").hexdigest()

    def test_pick_sample_deterministic(self):
        items = list(range(100))
        a = qd.pick_sample(items, 10)
        b = qd.pick_sample(items, 10)
        assert a == b and len(a) == 10
        # seed=42 抽 10 章的可复现性锚点
        assert qd.pick_sample(items, 10, seed=42) == qd.pick_sample(items, 10)

    def test_pick_sample_small_list_returns_all(self):
        assert qd.pick_sample([1, 2, 3], 10) == [1, 2, 3]

    def test_cost_usd(self):
        assert qd.cost_usd(1_000_000, 1_000_000) == pytest.approx(0.27 + 1.10)


class TestFindUberRoot:
    def test_single_terminal(self):
        assert qd.find_uber_root(PARENTS) == "天下"

    def test_multiple_terminals_picks_most_children(self):
        parents = {"a": "root1", "b": "root1", "c": "root2"}
        assert qd.find_uber_root(parents) == "root1"

    def test_empty_map_fallback(self):
        assert qd.find_uber_root({}) == "天下"


class TestM1:
    def test_full_metrics(self):
        # 证据集缺 "灵山" → 灵山 是 orphan
        evidence = {"主世界", "东胜神洲", "傲来国", "花果山", "水帘洞"}
        m = qd.compute_m1(PARENTS, UBER, evidence)

        assert m["uber_root"] == "天下"
        assert m["total_nodes"] == 6  # 不含 uber-root
        # roots：parent 为空或指向 uber-root 的非 uber 节点 → 只有 主世界
        assert m["roots"] == 1 and m["root_names"] == ["主世界"]
        assert m["orphans"] == 1 and m["orphan_names"] == ["灵山"]
        assert m["orphan_rate"] == pytest.approx(1 / 6)
        # max_children：主世界有 2 个子节点
        assert m["max_children"] == 2 and m["max_children_node"] == "主世界"
        # 深度：主世界1 东胜神洲2 傲来国3 花果山4 水帘洞5 灵山2
        assert m["depth_distribution"] == {"1": 1, "2": 2, "3": 1, "4+": 2}
        assert m["depth_ge3_ratio"] == pytest.approx(3 / 6)

    def test_empty_tree(self):
        m = qd.compute_m1({}, "天下", set())
        assert m["total_nodes"] == 0 and m["roots"] == 0
        assert m["orphan_rate"] == 0.0 and m["max_children"] == 0

    def test_cycle_guard(self):
        parents = {"a": "b", "b": "a"}  # 环，不得死循环
        m = qd.compute_m1(parents, "天下", {"a", "b"})
        assert m["total_nodes"] == 2


class TestM2:
    def test_recall_with_alias_merge(self):
        t = {"水帘洞", "蟠桃园", "灵山"}
        e = {"水帘洞", "桃园"}
        alias_map = {"蟠桃园": "桃园"}
        m = qd.compute_m2(t, e, alias_map)
        assert m["T_size"] == 3 and m["E_size"] == 2
        assert m["T_intersect_E"] == 2  # 水帘洞 + 蟠桃园(→桃园)
        assert m["recall_proxy"] == pytest.approx(2 / 3)
        assert m["T_minus_E"] == ["灵山"]

    def test_empty_T(self):
        m = qd.compute_m2(set(), {"a"}, {})
        assert m["recall_proxy"] is None

    def test_canonicalize(self):
        assert qd.canonicalize({"a", "b"}, {"a": "A"}) == {"A", "b"}

    def test_scan_pipeline_with_mock_llm(self, monkeypatch):
        """run_m2_scan：seed=42 抽章 + JSON 解析 + 并集去重（LLM 桩掉）。"""
        chapters = [(i, f"第{i}回", f"正文{i}") for i in range(1, 101)]
        calls = []

        async def fake_chat(system, user, tag, cost_acc):
            calls.append(tag)
            cost_acc["prompt_tokens"] += 10
            cost_acc["completion_tokens"] += 5
            return '{"locations": ["花果山", "水帘洞"]}'

        monkeypatch.setattr(qd, "deepseek_chat", fake_chat)
        cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        text_names, name_chapters, sampled = _run(
            qd.run_m2_scan("西游记", chapters, cost_acc)
        )
        assert text_names == {"花果山", "水帘洞"}
        assert len(sampled) == 10 and sampled == sorted(sampled)
        assert len(calls) == 10
        assert name_chapters["花果山"] == sampled  # 每章都返回同两名
        # 抽样确定性
        assert sampled == sorted(qd.pick_sample(list(range(1, 101)), 10))


class TestM3:
    def test_screen_r1_child_contains_parent_name(self):
        cands = qd.screen_direction_candidates({"灵山胜境": "灵山"}, UBER)
        assert len(cands) == 1
        assert cands[0]["rules"] == ["R1_child_name_contains_parent"]

    def test_screen_r2_tier_contradiction(self):
        # 父=水帘洞(洞,rank6) 子=花果山(山,rank4) → 父级不高于子级 → 可疑
        cands = qd.screen_direction_candidates({"花果山": "水帘洞"}, UBER)
        assert len(cands) == 1 and "R2_suffix_tier_contradiction" in cands[0]["rules"]
        # 正常方向（父=花果山 子=水帘洞）不命中任何规则
        assert qd.screen_direction_candidates({"水帘洞": "花果山"}, UBER) == []

    def test_screen_r2_same_tier_flagged(self):
        cands = qd.screen_direction_candidates({"两界山": "五行山"}, UBER)
        assert len(cands) == 1

    def test_screen_skips_uber_edges(self):
        assert qd.screen_direction_candidates({"主世界": "天下"}, UBER) == []

    def test_tier_suffix_rank(self):
        assert qd.tier_suffix_rank("东胜神洲") == 1
        assert qd.tier_suffix_rank("花果山") == 4
        assert qd.tier_suffix_rank("水帘洞") == 6
        assert qd.tier_suffix_rank("主世界") == 0
        assert qd.tier_suffix_rank("天庭") is None

    def test_compute_m3_rates(self):
        verdicts = [
            {"parent": "a", "child": "b", "judgment": "correct"},
            {"parent": "c", "child": "d", "judgment": "reversed"},
            {"parent": "e", "child": "f", "judgment": "unrelated"},
        ]
        m = qd.compute_m3(100, [{"parent": "a", "child": "b"}] * 3, verdicts)
        assert m["total_parent_child_pairs"] == 100
        assert m["candidates"] == 3 and m["candidate_ratio"] == pytest.approx(0.03)
        assert m["reversed"] == 1 and m["unclear"] == 1
        # 错误率分母 = correct+reversed（unrelated 不计）
        assert m["direction_error_rate"] == pytest.approx(0.5)

    def test_arbitrate_with_mock_llm(self, monkeypatch):
        candidates = [{"parent": "水帘洞", "child": "花果山", "rules": ["R2"]}]

        async def fake_chat(system, user, tag, cost_acc):
            return '{"verdicts": [{"parent": "水帘洞", "child": "花果山", "judgment": "reversed"}]}'

        monkeypatch.setattr(qd, "deepseek_chat", fake_chat)
        cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        verdicts = _run(
            qd.run_m3_arbitrate("西游记", candidates, {}, cost_acc)
        )
        assert verdicts == [
            {"parent": "水帘洞", "child": "花果山", "judgment": "reversed"}
        ]


class TestM4:
    def test_generic_residue(self):
        stub = lambda n: "conceptual geo word" if n in ("天下", "江湖") else None  # noqa: E731
        m = qd.compute_m4({"花果山", "天下", "水帘洞", "江湖"}, stub)
        assert m["total_nodes"] == 4
        assert m["generic_hits"] == 2
        assert m["generic_residue"] == pytest.approx(0.5)
        assert {h["name"] for h in m["hit_details"]} == {"天下", "江湖"}

    def test_empty(self):
        m = qd.compute_m4(set(), lambda n: None)
        assert m["generic_residue"] == 0.0


# ── M5/M6(Epic 3,FR-3.2–FR-3.4)──────────────────────────────────

def _fake_judge_report() -> dict:
    return {
        "aggregate": {
            "precision": 0.9, "faithfulness": 0.8, "comprehensiveness": 0.7,
            "m5": 0.8, "total_items": 20, "evidence_coverage": 0.95,
            "span_located_rate": 0.9, "chapters_judged": 5,
        },
    }


def _fake_rel_eval() -> dict:
    return {
        "shuihu_subtype_target": 0.55,
        "shuihu": {"subtype": {"accuracy": 0.62}},
        "xiyouji": {"mock_category": {"accuracy": 0.88},
                    "legacy_category_baseline": {"accuracy": 0.85}},
    }


class TestM5:
    def test_calibrated(self):
        m = qd.compute_m5(_fake_judge_report(), {"kappa": 0.61, "calibrated": True})
        assert m["status"] == "ok"
        assert m["m5"] == pytest.approx(0.8)
        assert m["calibrated"] is True and m["calibration_label"] == "已校准"
        assert m["kappa"] == pytest.approx(0.61)

    def test_uncalibrated_below_threshold(self):
        """FR-3.3:kappa 低于阈值时 M5 标记为“未校准”。"""
        m = qd.compute_m5(_fake_judge_report(), {"kappa": 0.2, "calibrated": False})
        assert m["calibrated"] is False and m["calibration_label"] == "未校准"

    def test_missing_calibration_marks_uncalibrated(self):
        m = qd.compute_m5(_fake_judge_report(), None)
        assert m["status"] == "ok" and m["calibration_label"] == "未校准"

    def test_missing_judge_report(self):
        m = qd.compute_m5(None, None)
        assert m["status"] == "missing"


class TestM6:
    def test_metrics(self):
        m = qd.compute_m6(_fake_rel_eval())
        assert m["status"] == "ok"
        assert m["shuihu_subtype_accuracy"] == pytest.approx(0.62)
        assert m["shuihu_target_met"] is True
        assert m["xiyouji_not_below_baseline"] is True

    def test_target_not_met(self):
        ev = _fake_rel_eval()
        ev["shuihu"]["subtype"]["accuracy"] = 0.40
        m = qd.compute_m6(ev)
        assert m["shuihu_target_met"] is False

    def test_missing(self):
        assert qd.compute_m6(None)["status"] == "missing"


class TestParseLlmJson:
    def test_plain(self):
        assert qd.parse_llm_json('{"locations": ["a"]}') == {"locations": ["a"]}

    def test_fenced(self):
        assert qd.parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_surrounding_text(self):
        assert qd.parse_llm_json('说明文字 {"a": 2} 尾巴') == {"a": 2}


# ── 报告生成器（构造数据 smoke test）────────────────────────────────

def _fake_report() -> dict:
    return {
        "slug": "xiyouji",
        "title": "西游记",
        "novel_id": "3b2ef56c-1a55-466a-a7d1-34272446a198",
        "freeze": {"db_md5": "abc123", "chapter_facts_rows": 100},
        "m1": qd.compute_m1(PARENTS, UBER, {"主世界", "东胜神洲", "傲来国", "花果山", "水帘洞"}),
        "m2": {
            "status": "ok", "sampled_chapters": [3, 7], "T_size": 3, "E_size": 2,
            "T_intersect_E": 2, "recall_proxy": 2 / 3, "T_minus_E": ["灵山"],
            "calibration_file": "xiyouji.calibration-sample.json",
            "calibration_size": 3, "calibration_status": "待人工校准",
        },
        "m3": {
            "status": "ok", "total_parent_child_pairs": 100, "candidates": 5,
            "candidate_ratio": 0.05, "arbitrated": 5, "reversed": 1, "unclear": 0,
            "direction_error_rate": 0.2, "candidate_list": [], "verdicts": [],
        },
        "m4": qd.compute_m4({"花果山", "天下"}, lambda n: "x" if n == "天下" else None),
        "qa_samples": {"status": "待人工核对", "m3": [], "m2": []},
    }


class TestReports:
    def test_render_novel_md(self):
        md = qd.render_novel_md(_fake_report())
        for needle in ("abc123", "recall_proxy", "待人工校准", "direction_error_rate",
                       "generic_residue", "max_children", "QA 抽检"):
            assert needle in md

    def test_render_novel_md_error_path(self):
        r = _fake_report()
        r["error"] = "world_structures 无此行"
        md = qd.render_novel_md(r)
        assert "测量失败" in md

    def test_render_summary_md(self):
        freeze = {"generated_at": "2026-08-06T00:00:00+00:00", "db_md5": "abc123"}
        cost = {"prompt_tokens": 1000, "completion_tokens": 500, "cost_usd": 0.001}
        md = qd.render_summary_md([_fake_report()], freeze, cost)
        for needle in ("RQ1", "RQ2", "Phase 1", "待人工校准", "abc123", "$0.0010"):
            assert needle in md
        # 该构造数据：orphan_rate=1/6>15%、recall 2/3<70%、方向错误率 20%>10% → D1/D2/D3 全触发
        assert "D1 信号扩展" in md and "D2 召回补抽" in md and "D3 方向校正" in md

    def test_render_summary_no_rule_fired(self):
        r = _fake_report()
        r["m1"] = qd.compute_m1(PARENTS, UBER, set(PARENTS) | set(PARENTS.values()))
        r["m2"]["recall_proxy"] = 0.85
        r["m3"]["direction_error_rate"] = 0.05
        md = qd.render_summary_md([r],
                                  {"generated_at": "t", "db_md5": "x"},
                                  {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
        assert "均未触发" in md


# ── FR-3.4 六指标报告 ──

class TestSixMetricReport:
    def test_render_novel_md_with_m5(self):
        r = _fake_report()
        r["m5"] = qd.compute_m5(_fake_judge_report(), {"kappa": 0.61, "calibrated": True})
        md = qd.render_novel_md(r)
        assert "M5 抽取忠实度" in md
        assert "M5 综合: 80.0%" in md
        assert "已校准" in md and "κ=0.610" in md

    def test_render_novel_md_m5_missing(self):
        r = _fake_report()
        r["m5"] = qd.compute_m5(None, None)
        md = qd.render_novel_md(r)
        assert "M5 缺失" in md

    def test_summary_table_has_six_metrics(self):
        """指标总表含 M5/M6 两列;M6 只在水浒/西游行有值。"""
        m5 = qd.compute_m5(_fake_judge_report(), {"kappa": 0.2, "calibrated": False})
        m6 = qd.compute_m6(_fake_rel_eval())
        reports = []
        for slug, title in (("xiyouji", "西游记"), ("shuihu", "水浒传"),
                            ("honglou", "红楼梦")):
            r = _fake_report()
            r["slug"], r["title"] = slug, title
            r["m5"] = m5
            reports.append(r)
        md = qd.render_summary_md(
            reports, {"generated_at": "t", "db_md5": "x"},
            {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
            m6,
        )
        assert "M5 faithfulness" in md and "M6 关系维度" in md
        # M5 未校准标记进表
        assert "80.0%(未校准)" in md
        # M6:水浒类型级 / 西游 category / 红楼无
        assert "类型级 62.0%(达标)" in md
        assert "category 88.0%(不低于旧基线)" in md

    def test_summary_without_m6_backward_compatible(self):
        """m6=None 时 M6 列记 —,旧调用签名不变。"""
        md = qd.render_summary_md([_fake_report()],
                                  {"generated_at": "t", "db_md5": "x"},
                                  {"prompt_tokens": 0, "completion_tokens": 0,
                                   "cost_usd": 0.0})
        assert "M6 关系维度" in md
