"""repro_check 可复现性 harness 纯函数单元测试 (issue #70)。

全部离线:只测 jaccard / event 近似匹配 / snapshot 对比等纯函数,
不触 DB、不调 LLM。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "repro_check.py"
_spec = importlib.util.spec_from_file_location("repro_check", _SCRIPT)
repro = importlib.util.module_from_spec(_spec)
sys.modules["repro_check"] = repro
_spec.loader.exec_module(repro)


def _snapshot(
    persons=("刘备", "曹操"),
    locations=("许都",),
    alias_map=None,
    relations=None,
    events=None,
    edges=None,
) -> dict:
    return {
        "alias_map": alias_map or {},
        "entities": {
            "person": list(persons),
            "location": list(locations),
            "item": [],
            "org": [],
        },
        "relations": relations or [],
        "events": events or [],
        "hierarchy_edges": edges or [],
    }


# ── jaccard_stats ────────────────────────────────────────────────────

def test_jaccard_identical():
    s = repro.jaccard_stats({"a", "b"}, {"a", "b"})
    assert s["jaccard"] == 1.0
    assert s["intersection"] == 2 and s["union"] == 2


def test_jaccard_disjoint():
    s = repro.jaccard_stats({"a"}, {"b"})
    assert s["jaccard"] == 0.0
    assert s["intersection"] == 0 and s["union"] == 2


def test_jaccard_partial():
    s = repro.jaccard_stats({"a", "b", "c"}, {"b", "c", "d"})
    assert s["intersection"] == 2 and s["union"] == 4
    assert s["jaccard"] == 0.5


def test_jaccard_both_empty_is_perfect():
    assert repro.jaccard_stats(set(), set())["jaccard"] == 1.0


# ── canon ────────────────────────────────────────────────────────────

def test_canon_maps_alias_and_passthrough():
    amap = {"玄德": "刘备"}
    assert repro.canon("玄德", amap) == "刘备"
    assert repro.canon("曹操", amap) == "曹操"


# ── event 近似匹配 ───────────────────────────────────────────────────

def _ev(chapter, summary, participants):
    return {"chapter": chapter, "type": "战斗",
            "summary": summary, "participants": participants}


def test_event_match_near_duplicate():
    a = [_ev(1, "刘备率军攻打黄巾军", ["刘备", "关羽"])]
    b = [_ev(1, "刘备率军攻打黄巾军大胜", ["刘备", "关羽"])]
    r = repro.match_events(a, b)
    assert r["intersection"] == 1
    assert r["jaccard"] == 1.0


def test_event_no_match_different_story():
    a = [_ev(1, "刘备三顾茅庐请诸葛亮出山", ["刘备", "诸葛亮"])]
    b = [_ev(1, "曹操煮酒论英雄", ["曹操"])]
    r = repro.match_events(a, b)
    assert r["intersection"] == 0
    assert r["union"] == 2
    assert r["jaccard"] == 0.0


def test_event_match_requires_same_chapter():
    # 相同摘要不同章,不允许跨章匹配
    a = [_ev(1, "刘备攻城", ["刘备"])]
    b = [_ev(2, "刘备攻城", ["刘备"])]
    r = repro.match_events(a, b)
    assert r["intersection"] == 0


def test_event_greedy_one_to_one():
    # B 侧一个事件只能匹配一次:A 两个相似事件争夺同一个 B
    a = [_ev(1, "刘备率军攻城", ["刘备"]), _ev(1, "刘备率军攻城大胜", ["刘备"])]
    b = [_ev(1, "刘备率军攻城", ["刘备"])]
    r = repro.match_events(a, b)
    assert r["intersection"] == 1
    assert r["union"] == 2
    assert r["jaccard"] == 0.5


# ── compare_snapshots 五层汇总 ───────────────────────────────────────

def test_compare_identical_snapshots():
    snap = _snapshot(
        alias_map={"玄德": "刘备"},
        relations=[["刘备", "关羽", "结义"]],
        events=[_ev(1, "桃园结义", ["刘备", "关羽"])],
        edges=[["许都", "兖州"]],
    )
    r = repro.compare_snapshots(snap, snap)
    for layer in ("entity", "alias", "relation", "event", "hierarchy"):
        assert r["layers"][layer]["jaccard"] == 1.0, layer
    assert r["macro_jaccard"] == 1.0


def test_compare_disjoint_snapshots():
    a = _snapshot(persons=("刘备",), locations=("成都",),
                  relations=[["刘备", "关羽", "结义"]],
                  edges=[["成都", "益州"]])
    b = _snapshot(persons=("曹操",), locations=("许都",),
                  relations=[["曹操", "曹丕", "父子"]],
                  edges=[["许都", "兖州"]])
    r = repro.compare_snapshots(a, b)
    assert r["layers"]["entity"]["jaccard"] == 0.0
    assert r["layers"]["relation"]["jaccard"] == 0.0
    assert r["layers"]["hierarchy"]["jaccard"] == 0.0
    assert r["macro_jaccard"] < 1.0


def test_compare_alias_pairs_layer():
    a = _snapshot(alias_map={"玄德": "刘备", "孟德": "曹操"})
    b = _snapshot(alias_map={"玄德": "刘备", "翼德": "张飞"})
    r = repro.compare_snapshots(a, b)
    al = r["layers"]["alias"]
    assert al["size_a"] == 2 and al["size_b"] == 2
    assert al["intersection"] == 1  # 玄德→刘备 共有
    assert al["jaccard"] == 1 / 3


def test_compare_entity_type_prefix_no_cross_type_collision():
    # 同名不同类型(人物"荆州" vs 地点"荆州")不得误判为同一实体
    a = _snapshot(persons=("荆州",), locations=())
    b = _snapshot(persons=(), locations=("荆州",))
    r = repro.compare_snapshots(a, b)
    assert r["layers"]["entity"]["jaccard"] == 0.0
    assert r["layers"]["entity"]["by_type"]["person"]["size_a"] == 1
    assert r["layers"]["entity"]["by_type"]["location"]["size_b"] == 1


# ── 报告渲染 ─────────────────────────────────────────────────────────

def test_render_report_md_contains_table_and_methodology():
    a = _snapshot(events=[_ev(1, "桃园结义", ["刘备"])], edges=[["成都", "益州"]])
    b = _snapshot(events=[_ev(1, "桃园结义", ["刘备"])], edges=[["成都", "益州"]])
    comparison = repro.compare_snapshots(a, b)
    report = {
        "generated_at": "2026-09-05T00:00:00+00:00",
        "novel_file": "/tmp/x.txt",
        "chapters": 10,
        "model": "m",
        "provider": "openai",
        "runs": {
            "A": {"task_status": "completed",
                  "usage": {"llm_calls": 1, "prompt_tokens": 10,
                            "completion_tokens": 5},
                  "elapsed_s": 1.0},
            "B": {"task_status": "completed",
                  "usage": {"llm_calls": 1, "prompt_tokens": 10,
                            "completion_tokens": 5},
                  "elapsed_s": 1.0},
        },
        "total_cost_usd": 0.001,
        "runs_dir": "/tmp/runs",
        "notes": [],
        **comparison,
    }
    md = repro.render_report_md(report)
    assert "五层 overlap 总表" in md
    assert "口径说明" in md
    assert "近似匹配" in md
    assert "100.0%" in md
