"""FR-1.5 关系维度回测脚本单元测试。

全部使用构造 mock 输入:不读真实 gold/silver 文件(除 main 端到端用例)、
不调用 LLM。加载方式对齐 test_quality_dashboard.py 的 importlib 模式。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCRIPT = _BACKEND_DIR / "scripts" / "eval_relation_dimensions.py"
_spec = importlib.util.spec_from_file_location("eval_relation_dimensions", _SCRIPT)
ev = importlib.util.module_from_spec(_spec)
sys.modules["eval_relation_dimensions"] = ev
_spec.loader.exec_module(ev)


# ── map_legacy_type_to_subtype ─────────────────────────────────────

def test_map_legacy_type_to_subtype_known_types():
    assert ev.map_legacy_type_to_subtype("结拜兄弟") == "结拜"
    assert ev.map_legacy_type_to_subtype("师徒") == "师门-师徒"
    assert ev.map_legacy_type_to_subtype("师兄弟") == "师门-同门"
    assert ev.map_legacy_type_to_subtype("主仆") == "主从"
    assert ev.map_legacy_type_to_subtype("上下级") == "君臣-上下级"
    assert ev.map_legacy_type_to_subtype("敌对") == "敌对"
    # schema v1:夫妻为平辈姻亲 → 辈分-亲属(旧路径归 family)
    assert ev.map_legacy_type_to_subtype("夫妻") == "辈分-亲属"
    assert ev.map_legacy_type_to_subtype("父子") == "辈分-亲属"


def test_map_legacy_type_to_subtype_normalizes_alias():
    # “义结金兰” 经 normalize_relation_type 归一为 “结拜兄弟”
    assert ev.map_legacy_type_to_subtype("义结金兰") == "结拜"


def test_map_legacy_type_to_subtype_fallback_and_none():
    # 词表外类型落入 schema 兜底槽位 “其他”
    assert ev.map_legacy_type_to_subtype("渡河") == "其他"
    assert ev.map_legacy_type_to_subtype("") is None
    assert ev.map_legacy_type_to_subtype(None) is None


def test_mock_polarity_for_subtype():
    assert ev.mock_polarity_for_subtype("敌对") == "negative"
    assert ev.mock_polarity_for_subtype("结拜") == "positive"
    assert ev.mock_polarity_for_subtype("君臣-上下级") == "neutral"
    assert ev.mock_polarity_for_subtype(None) is None


# ── accuracy / top_confusions ──────────────────────────────────────

def test_accuracy_basic():
    result = ev.accuracy([("a", "a"), ("b", "c"), ("a", "a")])
    assert result["n"] == 3
    assert result["correct"] == 2
    assert abs(result["accuracy"] - 2 / 3) < 1e-9


def test_accuracy_skips_missing_and_empty():
    result = ev.accuracy([("a", None), (None, "a"), ("a", "a")])
    assert result["n"] == 1
    assert result["accuracy"] == 1.0
    empty = ev.accuracy([])
    assert empty["n"] == 0
    assert empty["accuracy"] is None


def test_top_confusions_orders_and_limits():
    pairs = [("x", "y"), ("x", "y"), ("p", "q"), ("a", "a"), ("x", "y")]
    confusions = ev.top_confusions(pairs, limit=2)
    assert confusions[0] == ("x", "y", 3)
    assert confusions[1] == ("p", "q", 1)
    assert ev.top_confusions([("a", "a")]) == []


# ── evaluate_shuihu(mock 输入)──────────────────────────────────────

def _shuihu_record(system_type, rel_subtype, polarity="positive"):
    return {
        "person_a": "甲", "person_b": "乙",
        "system_type": system_type,
        "system_all_types": [system_type],
        "system_category": None,
        "mention_count": 1, "first_seen": "ch1",
        "rel_subtype": rel_subtype,
        "polarity": polarity,
        "closeness": "unknown",
        "reason": "mock", "label_source": "silver_draft",
    }


def test_evaluate_shuihu_subtype_and_polarity():
    records = [
        _shuihu_record("结拜兄弟", "结拜"),          # 类型对、极性对
        _shuihu_record("上下级", "结拜"),            # 类型错、极性错(neutral vs positive)
        _shuihu_record("敌对", "敌对", "negative"),  # 全对
        _shuihu_record("师徒", "师门-师徒", "neutral"),
    ]
    result = ev.evaluate_shuihu(records)
    assert result["n"] == 4
    assert result["subtype"]["correct"] == 3
    assert abs(result["subtype"]["accuracy"] - 0.75) < 1e-9
    assert result["polarity"]["correct"] == 3
    # 混淆:上下级→(君臣-上下级, 标注结拜)
    assert ("君臣-上下级", "结拜", 1) in result["subtype_confusions"]


def test_evaluate_shuihu_legacy_baseline_vs_new():
    # 旧路径把 夫妻→family(与标注派生 family 一致)、把 结拜兄弟→intimate
    # (与标注派生 intimate 一致):旧口径在此 mock 上应满分
    records = [
        _shuihu_record("夫妻", "辈分-亲属"),
        _shuihu_record("结拜兄弟", "结拜"),
    ]
    result = ev.evaluate_shuihu(records)
    assert result["legacy_category_baseline"]["accuracy"] == 1.0
    assert result["subtype"]["accuracy"] == 1.0
    assert result["derivation_agreement"]["accuracy"] == 1.0


# ── evaluate_xiyouji(mock 输入)─────────────────────────────────────

def _xiyouji_record(system_type, correct_category):
    return {
        "person_a": "甲", "person_b": "乙",
        "system_type": system_type,
        "correct_type": system_type,
        "correct_category": correct_category,
    }


def test_evaluate_xiyouji_mock_not_below_legacy():
    # 敬拜:旧路径落入 other,但 gold 标 hierarchical;mock 映射到
    # 君臣-上下级 → hierarchical,新口径应高于旧基线
    records = [
        _xiyouji_record("敬拜", "hierarchical"),
        _xiyouji_record("师徒", "hierarchical"),
        _xiyouji_record("敌对", "hostile"),
        _xiyouji_record("父子", "family"),
    ]
    result = ev.evaluate_xiyouji(records)
    assert result["legacy_category_baseline"]["correct"] == 3  # 敬拜 错
    assert result["mock_category"]["correct"] == 4
    assert result["mock_category"]["accuracy"] > result["legacy_category_baseline"]["accuracy"]
    assert result["mock_subtype"]["accuracy"] == 1.0  # system_type == correct_type


# ── render_report / main 端到端 ────────────────────────────────────

def test_render_report_contains_disclosures():
    shuihu = ev.evaluate_shuihu([_shuihu_record("结拜兄弟", "结拜")])
    xiyouji = ev.evaluate_xiyouji([_xiyouji_record("师徒", "hierarchical")])
    report = ev.render_report(shuihu, xiyouji)
    assert "口径声明" in report
    assert "28%" in report and "引自论文" in report
    assert "不可直接比较" in report
    assert "silver_draft" in report
    assert "验收对照" in report
    assert "样本量" in report


def test_main_end_to_end_real_inputs(tmp_path, monkeypatch):
    """CI 复跑检查:真实 silver + gold 输入,产出 markdown 报告。"""
    out = tmp_path / "report.md"
    monkeypatch.setattr(sys, "argv", ["eval_relation_dimensions.py", "--out", str(out)])
    assert ev.main() == 0
    report = out.read_text(encoding="utf-8")
    assert "# 关系维度回测报告(FR-1.5)" in report
    assert "水浒(silver 小样)" in report
    assert "西游(gold,冻结只读)" in report
    # 真实输入下验收行必须存在
    assert "水浒类型级 ≥55%" in report
    assert "西游类型级不低于旧单标签口径基线" in report


def test_real_silver_file_shape():
    """silver 小样结构契约:条数在 80–120,rel_subtype 必填且取值合法。"""
    records = ev.load_shuihu_silver()
    assert 80 <= len(records) <= 120
    for r in records:
        assert r["label_source"] == "silver_draft"
        assert r["rel_subtype"] in ev.VALID_SUBTYPES
        assert r["person_a"] and r["person_b"]
        assert r["system_type"] and r["reason"]
        if r.get("polarity") is not None:
            assert r["polarity"] in {"positive", "negative", "neutral"}
        if r.get("closeness") is not None:
            assert r["closeness"] in {"close", "distant", "unknown"}
