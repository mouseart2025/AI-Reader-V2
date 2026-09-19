"""Tests for protected high-confidence edges in EdmondsResolver.

2026-09-19 水浒实测:Phase 5 度均衡把先验覆盖过的 文德殿→东京(w=35)
重排为 文德殿→内苑,确定性知识被结构启发式错挂成跳层。name-containment
与 prior override 产生的边进入 protected 集合,Phase 4 幻父上提与
Phase 5 度均衡均不得改动其子节点归属。
"""
from collections import Counter

from src.services.geo_skills.edmonds_resolver import EdmondsResolver


class TestProtectedEdges:
    def test_phantom_lift_skips_protected_child(self):
        """protected 子节点即使 mc=0 也不被幻父上提。"""
        parents = {"phantom": "root"}
        for i in range(11):
            parents[f"child_{i}"] = "phantom"
        freq = Counter({"phantom": 1, "root": 100})

        new_parents, lifted = EdmondsResolver._lift_phantom_parent_children(
            parents, freq, uber_root="root",
            protected={"child_0", "child_1", "child_2"},
        )
        for c in ("child_0", "child_1", "child_2"):
            assert new_parents[c] == "phantom"
        assert lifted >= 2  # 其余未保护子节点仍正常上提

    def test_balance_degrees_skips_protected_leaf(self):
        """protected 叶节点在度均衡中保持原父节点。"""
        # hub 有 31 个子节点(超 max_children=30),其中 聚义厅 受保护
        parents = {"hub": "root", "absorber": "hub"}
        kids = [f"kid_{i}" for i in range(29)] + ["聚义厅", "文庙"]
        for k in kids:
            parents[k] = "hub"
        tiers = {"hub": "city", "absorber": "region", "root": "world"}
        for k in kids:
            tiers[k] = "site"

        new_parents = EdmondsResolver._balance_degrees(
            parents, tiers, max_children=30, protected={"聚义厅"},
        )
        assert new_parents["聚义厅"] == "hub"

    def test_balance_degrees_still_moves_unprotected(self):
        """未保护的叶节点仍正常重排(保护不影响原有均衡能力)。"""
        parents = {"hub": "root", "absorber": "hub"}
        kids = [f"kid_{i}" for i in range(31)]
        for k in kids:
            parents[k] = "hub"
        tiers = {"hub": "city", "absorber": "region", "root": "world"}
        for k in kids:
            tiers[k] = "site"

        new_parents = EdmondsResolver._balance_degrees(
            parents, tiers, max_children=30, protected=set(),
        )
        moved = [k for k in kids if new_parents[k] != "hub"]
        assert moved  # 至少有叶节点被重排到 absorber
        assert all(new_parents[k] == "absorber" for k in moved)
