"""KnowledgePrior 证据门槛注入缺失先验父节点的回归测试。

背景(2026-09-19 GeoEvolve 诊断):旧逻辑要求先验的 child 与 parent 都在
snapshot.location_tiers 中才注入选票。西游的四大部洲因历史 purge 不在
tiers 里,导致"傲来国→东胜神洲"等先验被整条丢弃,109 个节点退回天下兜底,
gold parent_precision 只有 0.34。修复后:parent 缺席但 (a) 在 priors 表中
有自身归属(链闭合)且 (b) 本小说有真实提及证据时,补入该节点并注入两段
选票。修复后西游 gold parent_precision 0.3438→0.7344。

本文件锁死:
1. 有证据的缺失先验父节点被补入(tier + 两段选票);
2. 无证据的缺失父节点不注入(防幻觉节点);
3. 双亲俱在的旧行为不变。
"""
import asyncio
from collections import Counter

from src.services.geo_skills.knowledge_prior import KnowledgePrior
from src.services.geo_skills.snapshot import HierarchySnapshot


def _snap(tiers: dict, freqs: dict, votes: dict | None = None) -> HierarchySnapshot:
    return HierarchySnapshot(
        location_parents={},
        location_tiers=tiers,
        parent_votes={k: Counter(v) for k, v in (votes or {}).items()},
        location_frequencies=Counter(freqs),
        chapter_settings={},
        location_chapters={},
    )


def test_missing_parent_with_evidence_is_injected():
    """西游:东胜神洲不在 tiers,但有频次证据 → 补入并注入两段选票。"""
    snap = _snap(
        tiers={"天下": "world", "傲来国": "kingdom", "花果山": "region"},
        freqs={"天下": 100, "傲来国": 30, "花果山": 40, "东胜神洲": 8},
        votes={"傲来国": {"东胜神洲": 4.0}},
    )
    result = asyncio.run(KnowledgePrior("西游记").execute(snap))

    assert result.tier_updates.get("东胜神洲") == "continent"
    assert result.new_votes["东胜神洲"].get("天下") == 20  # 补入节点的归属
    assert result.new_votes["傲来国"].get("东胜神洲") == 20  # 原断掉的先验接通


def test_missing_parent_without_evidence_is_not_injected():
    """无频次且非票目标 → 不注入(防止把先验表里的无关名变成节点)。"""
    snap = _snap(
        tiers={"天下": "world", "傲来国": "kingdom"},
        freqs={"天下": 100, "傲来国": 30},  # 东胜神洲零证据
    )
    result = asyncio.run(KnowledgePrior("西游记").execute(snap))

    assert "东胜神洲" not in result.tier_updates
    assert "东胜神洲" not in result.new_votes
    assert "傲来国" not in result.new_votes  # 该先验整体放弃


def test_both_present_behavior_unchanged():
    """双亲俱在:直接注入,不产生 tier_updates(旧行为)。"""
    snap = _snap(
        tiers={"天下": "world", "东胜神洲": "continent", "傲来国": "kingdom"},
        freqs={"天下": 100, "东胜神洲": 20, "傲来国": 30},
    )
    result = asyncio.run(KnowledgePrior("西游记").execute(snap))

    assert not result.tier_updates
    assert result.new_votes["傲来国"].get("东胜神洲") == 20
    assert result.new_votes["东胜神洲"].get("天下") == 20
