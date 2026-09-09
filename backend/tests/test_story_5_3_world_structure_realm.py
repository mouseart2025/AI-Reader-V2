"""Story 5.3 task 3 收尾：验证 world_structure_agent._classify_tier 真正输出 `realm`。

后端桥接点：world_structure 的 location_tiers 由 _classify_tier 填充（初始构建 + rebuild 两条路径），
而 Story 5.3 的 is_special_space SSOT 此前只作用于 tier_classifier，未进入该路径。
本测试确认特殊空间经 _classify_tier 落入 realm，且三国常规地名零误命中（无漂移）。
"""

import pytest

from src.services.world_structure_agent import WorldStructureAgent
from src.utils.location_names import is_special_space


def _make_agent():
    """绕过重型 __init__，仅注入 _classify_tier 所需的 structure 桩。"""
    agent = WorldStructureAgent.__new__(WorldStructureAgent)
    agent.structure = type(
        "StubStructure",
        (),
        {"novel_genre_hint": None, "location_tiers": {}, "type_hierarchy": {}},
    )()
    return agent


POSITIVE = ["仙界", "魔域", "秘境", "洞天", "结界", "天界", "银河", "灵界", "妖境", "幻境"]


@pytest.mark.parametrize("name", POSITIVE)
def test_classify_tier_special_space_to_realm(name):
    agent = _make_agent()
    assert agent._classify_tier(name, "", None, 0) == "realm", name
    assert is_special_space(name) is True


def test_classify_tier_subrealm_stays_non_realm():
    """天庭 由 Layer 0 强制 continent，且不属于 is_special_space → 不应被 realm 早返回劫持。"""
    agent = _make_agent()
    assert agent._classify_tier("天庭", "", None, 0) == "continent"
    assert is_special_space("天庭") is False


def test_classify_tier_three_kingdoms_no_realm():
    """三国常规地名：既不落入 realm，is_special_space 也全 False（AC4 无漂移）。"""
    agent = _make_agent()
    for name in ["荆州", "长安", "洛阳", "建业", "成都", "冀州", "许昌"]:
        assert agent._classify_tier(name, "", None, 0) != "realm", name
        assert is_special_space(name) is False
