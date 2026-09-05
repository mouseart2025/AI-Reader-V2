"""Tests for Epic D3 follow-up: 裸旧边淘汰(证据不足的旧 parent 不再永生).

两条残留通道的收口:
- (a) VoteBuilder 基线注入:旧 parents 作为 baseline 票(w=1)无条件回注,
  即使当前章节证据完全不支持该 pair —— 改为仅当 pair 有当前证据
  (loc.parent / contains / 主场景推断)时才注入。
- (b) EdmondsResolver 增量保留:snapshot.location_parents 原样作为
  base_parents 保留 —— 改为「child 有当前票但均不支持该旧 pair」时淘汰,
  由 Edmonds 从真实票中重新解析;child 完全无票(无证据可判)时保留旧边
  以维持重跑稳定性(避免零证据节点坠入天下兜底后被 degree balancing
  反复打散)。
"""

import json
from collections import Counter
from unittest.mock import patch

import pytest

from src.services.geo_skills.snapshot import HierarchySnapshot
from src.services.geo_skills.vote_builder import VoteBuilder
from src.services.geo_skills.edmonds_resolver import EdmondsResolver


class _NonClosingConnection:
    """包装共享 memory_db:业务代码里的 close() 变为 no-op。"""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


def _make_snapshot(parents, tiers, votes=None):
    return HierarchySnapshot(
        location_parents=parents,
        location_tiers=tiers,
        parent_votes=votes or {},
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )


async def _seed_facts(memory_db, novel_id, facts):
    """插入小说/章节/fact 行,facts 为 [(chapter_id, fact_dict), ...]。"""
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, '测试')", (novel_id,)
    )
    for ch_id, fact in facts:
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, '章', '')",
            (ch_id, novel_id, ch_id),
        )
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)",
            (novel_id, ch_id, json.dumps(fact, ensure_ascii=False)),
        )
    await memory_db.commit()


def _run_vote_builder(memory_db, snapshot, novel_id="nv1"):
    async def _factory():
        return _NonClosingConnection(memory_db)

    return patch("src.db.sqlite_db.get_connection", _factory), VoteBuilder(novel_id)


class TestBaselineInjectionEvidenceGated:
    """通道 (a):VoteBuilder 基线注入只回注有当前证据佐证的旧边。"""

    @pytest.mark.asyncio
    async def test_bare_legacy_edge_not_reinjected(self, memory_db):
        """旧 parents 含裸边(汜水关→洛阳,仅 D3 前 adjacent 传播产生),
        当前证据中无任何支持 → 不再注入 baseline 票。"""
        fact = {
            "chapter_id": 1,
            "novel_id": "nv1",
            "characters": [],
            "locations": [
                {"name": "洛阳", "type": "城市", "role": "setting"},
                # referenced 角色不参与主场景推断投票,保证汜水关无证据
                {"name": "汜水关", "type": "关隘", "role": "referenced"},
                {"name": "花果山", "type": "山"},
                {"name": "水帘洞", "type": "洞府"},
            ],
            "spatial_relationships": [
                {"source": "花果山", "target": "水帘洞",
                 "relation_type": "contains", "confidence": "high"},
                # 汜水关与洛阳仅 adjacent —— D3 后不产生票
                {"source": "汜水关", "target": "洛阳",
                 "relation_type": "adjacent", "value": "adjacent"},
            ],
            "events": [],
        }
        await _seed_facts(memory_db, "nv1", [(1, fact)])

        snapshot = _make_snapshot(
            parents={"汜水关": "洛阳", "水帘洞": "花果山"},
            tiers={"汜水关": "site", "洛阳": "city",
                   "花果山": "region", "水帘洞": "site"},
        )
        patcher, vb = _run_vote_builder(memory_db, snapshot)
        with patcher:
            result = await vb.execute(snapshot)

        votes = result.new_votes
        # 裸边:无 baseline 票
        assert votes.get("汜水关", Counter()).get("洛阳", 0) == 0
        # contains 佐证边:contains 票 + baseline 票都在
        assert votes["水帘洞"]["花果山"] >= 2

    @pytest.mark.asyncio
    async def test_setting_inferred_edge_gets_baseline(self, memory_db):
        """旧边只有主场景推断佐证(无 loc.parent/contains)也算有证据,
        baseline 正常注入(稳定性保护)。"""
        fact = {
            "chapter_id": 1,
            "novel_id": "nv1",
            "characters": [],
            "locations": [
                {"name": "长安城", "type": "城市", "role": "setting"},
                # 无 parent、非 referenced、比主场景低一级 → 主场景推断投票
                {"name": "青牛村", "type": "村庄"},
            ],
            "spatial_relationships": [],
            "events": [],
        }
        await _seed_facts(memory_db, "nv1", [(1, fact)])

        snapshot = _make_snapshot(
            parents={"青牛村": "长安城"},
            tiers={"青牛村": "site", "长安城": "city"},
        )
        patcher, vb = _run_vote_builder(memory_db, snapshot)
        with patcher:
            result = await vb.execute(snapshot)

        # 主场景推断票(2)+ baseline(1)
        assert result.new_votes["青牛村"]["长安城"] >= 3


class TestEdmondsDropsBareBaseParents:
    """通道 (b):EdmondsResolver 不再无条件保留零票 base parents。"""

    @pytest.mark.asyncio
    async def test_bare_base_parent_reresolved_from_votes(self):
        """旧边 汜水关→洛阳 零票,但汜水关有指向荥阳的票
        → 旧边淘汰,汜水关由 Edmonds 改挂荥阳。"""
        snapshot = _make_snapshot(
            parents={"汜水关": "洛阳", "洛阳": "天下"},
            tiers={"汜水关": "site", "洛阳": "city", "荥阳": "city",
                   "天下": "world"},
            votes={
                "汜水关": Counter({"荥阳": 3.0}),
                "洛阳": Counter({"天下": 5.0}),
            },
        )
        result = await EdmondsResolver().execute(snapshot)
        parents = result.parent_overrides
        assert parents.get("汜水关") == "荥阳"
        # 有票的旧边保留
        assert parents.get("洛阳") == "天下"

    @pytest.mark.asyncio
    async def test_zero_vote_child_keeps_old_parent(self):
        """child 完全无票(无证据可判)→ 保留旧边。

        稳定性保护:零证据节点若强行淘汰会坠入天下兜底,再被 degree
        balancing 随机打散,导致每次重跑都抖动;且此类旧边很多是正确的
        (如 LLM SET_PARENT 设置的 parent)。真正要清的是「有票但矛盾」的边。
        """
        snapshot = _make_snapshot(
            parents={"幽灵谷": "旧父", "旧父": "天下"},
            tiers={"幽灵谷": "region", "旧父": "region", "天下": "world"},
            votes={"旧父": Counter({"天下": 2.0})},
        )
        result = await EdmondsResolver().execute(snapshot)
        parents = result.parent_overrides
        assert parents.get("幽灵谷") == "旧父"
        assert parents.get("旧父") == "天下"

    @pytest.mark.asyncio
    async def test_prior_only_edge_preserved(self):
        """知识先验票(w=20)是合法佐证:无章节证据但有先验票的旧边保留。"""
        snapshot = _make_snapshot(
            parents={"成都": "益州", "益州": "天下"},
            tiers={"成都": "city", "益州": "kingdom", "天下": "world"},
            votes={
                "成都": Counter({"益州": 20.0}),  # KnowledgePrior 权重
                "益州": Counter({"天下": 20.0}),
            },
        )
        result = await EdmondsResolver().execute(snapshot)
        assert result.parent_overrides.get("成都") == "益州"

    @pytest.mark.asyncio
    async def test_contains_backed_edge_preserved(self):
        """contains 空间关系票(合法佐证,D3 特意保留)支撑的旧边不动。"""
        snapshot = _make_snapshot(
            parents={"水帘洞": "花果山", "花果山": "天下"},
            tiers={"水帘洞": "site", "花果山": "region", "天下": "world"},
            votes={
                "水帘洞": Counter({"花果山": 2.0}),  # contains high
                "花果山": Counter({"天下": 1.0}),
            },
        )
        result = await EdmondsResolver().execute(snapshot)
        assert result.parent_overrides.get("水帘洞") == "花果山"
