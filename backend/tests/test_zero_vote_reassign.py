"""edmonds.zero_vote_reassign(零票改挂禁止)与断环确定性 单测。

2026-09-22 确诊:_balance_degrees 是纯结构启发式(只读 tier/度),票盲
改挂把 红楼 省亲别墅({大观园: 4.5 全票})重排到 0 票的 紫菱洲,造成
两轮 rebuild 间金标边交替。守卫:当前 parent 有正票而候选 absorber
零票时禁止改挂;默认 forbid,"allow" 恢复旧行为。
"""

from collections import Counter
from pathlib import Path

import pytest

from src.services.geo_skills import evolve_params as ep
from src.services.geo_skills.edmonds_resolver import EdmondsResolver


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("EVOLVE_PARAMS_JSON", raising=False)
    ep.reset_cache()
    yield
    ep.reset_cache()


def _set_params(tmp_path: Path, monkeypatch, params: dict) -> None:
    import json

    p = tmp_path / "params.json"
    p.write_text(json.dumps(params), encoding="utf-8")
    monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
    ep.reset_cache()


def _overflow_parents() -> tuple[dict, dict]:
    """P 超 max_children=2:P 有 3 子(leaf1/leaf2/ab),ab 有 1 子(可作
    absorber);tiers 满足 ab(city=4) < leaf(site=5) 的吸收条件。"""
    parents = {
        "leaf1": "P", "leaf2": "P", "ab": "P", "x": "ab",
        "P": "ROOT",
    }
    tiers = {"leaf1": "site", "leaf2": "site", "ab": "city",
             "x": "site", "P": "region", "ROOT": "world"}
    return parents, tiers


def test_zero_vote_reassign_forbidden_by_default():
    """leaf1 对当前 parent P 有正票、候选 absorber ab 零票 → 禁止改挂;
    无票的 leaf2 照常重排。"""
    parents, tiers = _overflow_parents()
    votes = {"leaf1": Counter({"P": 4.5})}
    out = EdmondsResolver._balance_degrees(
        dict(parents), tiers, max_children=2, votes=votes)
    assert out["leaf1"] == "P"    # 全票叶不被改挂
    assert out["leaf2"] == "ab"   # 零票叶照常重排


def test_zero_vote_reassign_allow_restores_old_behavior(tmp_path, monkeypatch):
    """开关 allow:恢复旧行为,全票叶也被重排。"""
    _set_params(tmp_path, monkeypatch,
                {"edmonds.zero_vote_reassign": "allow"})
    parents, tiers = _overflow_parents()
    votes = {"leaf1": Counter({"P": 4.5})}
    out = EdmondsResolver._balance_degrees(
        dict(parents), tiers, max_children=2, votes=votes)
    assert out["leaf1"] == "ab"


def test_zero_vote_reassign_allows_voted_absorber():
    """候选 absorber 自身有正票(即使小于当前 parent 票)时不拦截——
    票仓之间存在竞争,交由票权裁决外的结构逻辑处理。"""
    parents, tiers = _overflow_parents()
    votes = {"leaf1": Counter({"P": 4.5, "ab": 2.0})}
    out = EdmondsResolver._balance_degrees(
        dict(parents), tiers, max_children=2, votes=votes)
    assert out["leaf1"] == "ab"


def test_cycle_break_picks_weakest_vote_edge_deterministic():
    """环必须断时:改指票权最弱边,且结果确定(两次调用一致)。"""
    # 环: A→B→C→A;票权 B→A 最弱(1.0)
    parents = {"A": "B", "B": "C", "C": "A", "R": "ROOT"}
    votes = {
        "A": Counter({"B": 5.0}),
        "B": Counter({"C": 1.0}),
        "C": Counter({"A": 3.0}),
    }
    edmonds_parents = {"B": "R"}  # Edmonds 为 B 选的环外候选
    out1, n1 = EdmondsResolver._break_cycles_fixpoint(
        parents, votes, edmonds_parents, "ROOT")
    out2, _ = EdmondsResolver._break_cycles_fixpoint(
        dict(parents), votes, edmonds_parents, "ROOT")
    assert n1 >= 1
    assert out1 == out2            # 确定性
    # 最弱边 (B→C) 被改指到 Edmonds 的环外选择 R
    assert out1["B"] == "R"
    assert out1["A"] == "B"        # 强边不动
    assert out1["C"] == "A"
