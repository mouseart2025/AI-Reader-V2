"""Story 5.5 回归测试 — EdmondsResolver 与 Story 5.2 拓扑节点的共存。

背景(在三国真实数据验收时发现的生产缺陷):

Story 5.2 把道路/通道类(passage-like)节点定义为 *topology 节点* 而非
层级子节点,做法是拒绝给它们挂 uber_root 兜底边(edmonds_resolver :161)。
但 ``nx.maximum_spanning_arborescence`` 要求**每个节点都从根可达**,
于是只要小说里存在任何一个 passage-like 节点,整个 arborescence 就抛
``No maximum spanning arborescence in G`` —— EdmondsResolver 返回空结果,
Phase 2-5(孤儿填充 / 环修复 / 幽灵父上提 / 度均衡)被**整体跳过**。

实测:三国 120 章重建时 Edmonds 直接失败;把 is_passage_like 打桩为
恒 False 后 Edmonds 立刻成功(217ms),证实因果。修复方式是只在
**根可达子图**上计算 arborescence,被故意孤儿化的节点保持无父。

本文件锁死三件事:
1. 存在孤儿化拓扑节点时,Edmonds 仍能正常解析其余节点(不再整体失效);
2. passage-like 节点永不出现在 parent 位置上(AC1,含遗留边);
3. passage-like 节点本身保持无父(topology 语义,不被强行挂到世界根)。
"""
from collections import Counter

from src.services.geo_skills.edmonds_resolver import EdmondsResolver
from src.services.geo_skills.snapshot import HierarchySnapshot
from src.utils.location_names import is_passage_like


def _resolve(parent_votes, tiers, location_parents=None):
    snap = HierarchySnapshot(
        location_parents=location_parents or {},
        location_tiers=tiers,
        parent_votes=parent_votes,
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )
    import asyncio

    result = asyncio.run(EdmondsResolver().execute(snap))
    return result.parent_overrides


# ── 1. 孤儿化拓扑节点不再拖垮整个 arborescence ────────────────────────
def test_edmonds_succeeds_despite_orphaned_passage_node():
    """回归:华容道 无入边时,其余节点仍须被解析(旧代码整体返回空)。"""
    tiers = {"天下": "world", "长安": "city", "长乐宫": "building", "华容道": "street"}
    parents = _resolve(
        parent_votes={"长乐宫": Counter({"长安": 5})},
        tiers=tiers,
    )
    # 旧行为:Edmonds 抛异常 → parent_overrides 为空 → 长乐宫 解析不到父
    assert parents.get("长乐宫") == "长安", (
        f"Edmonds 应在可达子图上正常求解, resolved={parents}"
    )


def test_passage_node_excluded_from_arborescence_not_fatal():
    """多个拓扑节点同时存在也不应让 Edmonds 失效。"""
    tiers = {
        "天下": "world", "长安": "city", "长乐宫": "building",
        "华容道": "street", "斜谷道": "street", "山路": "street",
    }
    parents = _resolve(
        parent_votes={"长乐宫": Counter({"长安": 7})},
        tiers=tiers,
    )
    assert parents.get("长乐宫") == "长安", f"resolved={parents}"


# ── 2. AC1:passage-like 永不作为 parent(含遗留边)──────────────────
def test_ac1_legacy_edge_with_passage_parent_dropped():
    """遗留边 战船→华容道 必须消失:华容道 不能当容器。"""
    tiers = {"天下": "world", "长安": "city", "华容道": "street", "战船": "site"}
    parents = _resolve(
        parent_votes={"长安": Counter({"天下": 3})},
        tiers=tiers,
        location_parents={"战船": "华容道"},
    )
    assert "华容道" not in set(parents.values()), (
        f"华容道 不得出现在 parent 位置, resolved={parents}"
    )
    assert parents.get("战船") != "华容道", f"resolved={parents}"


def test_ac1_no_passage_like_parent_anywhere():
    """不变式:输出中任何 parent 都不是 passage-like。"""
    tiers = {
        "天下": "world", "长安": "city", "长乐宫": "building",
        "华容道": "street", "狄道": "street", "未央宫": "building",
    }
    parents = _resolve(
        parent_votes={
            "长乐宫": Counter({"长安": 6}),
            "未央宫": Counter({"长安": 4}),
        },
        tiers=tiers,
        location_parents={"长乐宫": "华容道", "未央宫": "狄道"},
    )
    offenders = {p for p in parents.values() if p and is_passage_like(p)}
    assert not offenders, f"passage-like 节点被当成了 parent: {offenders}"


# ── 4. TierClassifier 判级修正(Story 5.5 抽查表暴露)────────────────
def _refine(tiers, parents=None, freq=None, children=None, era="sanguo"):
    """直接调用 Phase 2 精炼(静态方法,便于单测)。"""
    from src.services.geo_skills.tier_classifier import TierClassifier

    return TierClassifier._multi_feature_refine(
        tiers=tiers,
        parents=parents or {},
        frequencies=Counter(freq or {}),
        children_count=children or {},
        era=era,
    )


def test_rule10_passage_like_demoted_to_site():
    """华容道/斜谷道:道路是拓扑节点,不能因「道」后缀被判成 kingdom。"""
    ups = _refine({"华容道": "kingdom", "斜谷道": "kingdom"},
                  parents={"华容道": "荆州", "斜谷道": "汉中"})
    assert ups.get("华容道") == "site", f"updates={ups}"
    assert ups.get("斜谷道") == "site", f"updates={ups}"


def test_rule6b_residence_kingdom_demoted_to_site():
    """东府:府第是居所,Rule 6 只在 tier==city 时生效,kingdom 漏网需 Rule 6b。"""
    ups = _refine({"东府": "kingdom"}, parents={"东府": "荆州"})
    assert ups.get("东府") == "site", f"updates={ups}"


def test_overworld_layer_root_is_continent():
    """主世界:overworld 图层根,Phase 1 判 region 导致层级倒挂,须为 continent。"""
    ups = _refine({"主世界": "region"}, parents={"主世界": "天下"})
    assert ups.get("主世界") == "continent", f"updates={ups}"


def test_province_not_over_demoted():
    """正向对照:三国语境下 X州 应保持 kingdom,不能被新规则误降。"""
    ups = _refine({"荆州": "kingdom", "益州": "kingdom"},
                  parents={"荆州": "主世界", "益州": "主世界"},
                  freq={"荆州": 50, "益州": 40})
    assert ups.get("荆州") in (None, "kingdom"), f"updates={ups}"
    assert ups.get("益州") in (None, "kingdom"), f"updates={ups}"


def test_no_blanket_promotion_of_small_scale_nodes():
    """锁死已回退的 Rule 11:县/山/谷 不得因子节点数被自动提成 kingdom。

    回退原因:TierClassifier 在 Edmonds 之前运行,children_count 取自遗留
    层级,实测把 涿县/沛县/青城山/麴山/羌人谷/长江 全提成了 kingdom。
    """
    ups = _refine({"涿县": "region", "青城山": "region", "长江": "region"},
                  parents={"涿县": "青州", "青城山": "益州", "长江": "荆州"},
                  children={"涿县": 5, "青城山": 4, "长江": 6})
    for name in ("涿县", "青城山", "长江"):
        assert ups.get(name) != "kingdom", f"{name} 被误提为 kingdom: {ups}"


# ── 3. passage-like 节点本身保持无父(topology 语义)─────────────────
def test_passage_like_node_stays_orphaned():
    """华容道 是拓扑节点,不应被强行挂到世界根下。"""
    tiers = {"天下": "world", "华容道": "street", "长安": "city"}
    parents = _resolve(
        parent_votes={"长安": Counter({"天下": 3})},
        tiers=tiers,
    )
    # 未被赋予父(或显式 None),而不是被挂到 天下
    assert parents.get("华容道") is None, (
        f"华容道 应保持无父(topology 节点), got {parents.get('华容道')}"
    )
