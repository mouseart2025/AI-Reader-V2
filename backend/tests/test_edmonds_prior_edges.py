"""EdmondsResolver 权威先验边(prior_edges)回归测试。

背景(2026-09-19 三国诊断):「高票即覆盖」阶段不区分先验票与有机票,
荆州→益州(有机票 50)压过先验 荆州→天下(w=20),造成 sibling 州倒挂。
修复:KnowledgePrior 硬编码路径经 SkillResult.prior_edges →
HierarchySnapshot.prior_edges 发出确定性边,Edmonds 将其权威化:
裸边清除豁免、高票覆盖跳过、Phase 4/5 保护。
"""
import asyncio
from collections import Counter

from src.services.geo_skills.edmonds_resolver import EdmondsResolver
from src.services.geo_skills.snapshot import HierarchySnapshot


def _snap(**kw) -> HierarchySnapshot:
    base = dict(
        location_parents={}, location_tiers={}, parent_votes={},
        location_frequencies=Counter(), chapter_settings={},
        location_chapters={},
    )
    base.update(kw)
    return HierarchySnapshot(**base)


def test_prior_edge_beats_higher_organic_vote():
    """荆州→益州(有机票 50)不得压过权威先验边 荆州→天下。"""
    snap = _snap(
        location_parents={"荆州": "益州", "益州": "天下"},
        location_tiers={"天下": "world", "益州": "region", "荆州": "region"},
        parent_votes={
            "荆州": Counter({"益州": 50.0, "天下": 20.0}),
            "益州": Counter({"天下": 30.0}),
        },
        location_frequencies=Counter({"天下": 500, "益州": 200, "荆州": 200}),
        prior_edges=frozenset({("荆州", "天下"), ("益州", "天下")}),
    )
    result = asyncio.run(EdmondsResolver().execute(snap))
    assert result.parent_overrides["荆州"] == "天下"
    assert result.parent_overrides["益州"] == "天下"


def test_prior_edge_survives_bare_edge_drop():
    """先验边即使当前票表中没有该对票,也不被裸边清除。"""
    snap = _snap(
        location_parents={"襄樊": "荆州"},  # 旧边;襄樊有票但不含荆州
        location_tiers={"天下": "world", "荆州": "region", "襄樊": "city"},
        parent_votes={"襄樊": Counter({"南阳": 3.0})},
        location_frequencies=Counter({"天下": 500, "荆州": 200, "襄樊": 5}),
        prior_edges=frozenset({("襄樊", "荆州"), ("荆州", "天下")}),
    )
    result = asyncio.run(EdmondsResolver().execute(snap))
    assert result.parent_overrides["襄樊"] == "荆州"


def test_snapshot_apply_threads_prior_edges():
    """SkillResult.prior_edges 经 apply 累积进 snapshot。"""
    from src.services.geo_skills.snapshot import SkillResult

    snap = _snap()
    r = SkillResult(skill_name="t", prior_edges=[("a", "b")])
    snap2 = snap.apply(r)
    assert ("a", "b") in snap2.prior_edges
    # 不可变:原 snapshot 不变
    assert not snap.prior_edges


def test_prior_edges_serialization_roundtrip():
    """snapshot_store 序列化/反序列化保留 prior_edges。"""
    from src.services.geo_skills.snapshot_store import (
        _deserialize_snapshot, _serialize_snapshot,
    )

    snap = _snap(prior_edges=frozenset({("a", "b"), ("c", "d")}))
    snap2 = _deserialize_snapshot(_serialize_snapshot(snap))
    assert snap2.prior_edges == snap.prior_edges
