"""Story 5.3 — 架空特殊空间 taxonomy (realm/dimension) 验收测试.

DB-free: 直接调用 src.utils.location_names.is_special_space 与
TierClassifier._multi_feature_refine / fact_validator._get_contains_rank,
无需 world_structures 或 ChapterFact 数据。

AC1: 名称含「异空间/领域/维度/结界/秘境/仙界/魔域」落入特殊空间类(realm),
     不出现「异空间=大陆」。
AC2: 特殊空间不参与 suffix rank 方向校验(_get_contains_rank 返回 None)。
AC4: 三国(无特殊空间语料)回归 —— is_special_space 对其地名全 False,
     分类结果无漂移。
"""

from collections import Counter

import pytest

from src.extraction.fact_validator import _get_contains_rank
from src.services.geo_skills.tier_classifier import TierClassifier
from src.utils.location_names import is_special_space

# ── AC1: 词表识别 ────────────────────────────────────────────────────────
POSITIVE = [
    # realm planes (界/域)
    "仙界", "魔界", "妖界", "灵界", "人界", "真仙界", "真魔界", "古魔界",
    "圣界", "冥界", "幽冥", "地府", "阴曹", "阴司", "黄泉", "天界",
    "魔域", "妖域", "灵域", "神域", "天域", "鬼域", "仙域", "佛界",
    "鬼界", "妖境", "魔境", "幻界",
    # pocket dimensions / secret realms
    "异空间", "封印空间", "法术空间", "特殊空间", "小世界", "次元", "维度",
    "洞天", "秘境", "结界", "幻境", "福地", "芥子空间", "须弥空间",
    # sci-fi planes
    "太阳系", "银河系", "银河", "三体世界", "三体星系", "三体行星",
    "三体游戏世界", "三体游戏", "蛮荒世界", "冥河之地",
]

# 必须保持地理/常规语义 (EXCLUDE 名单 + 普通地名)
NEGATIVE_GEOGRAPHIC = [
    "西域", "国界", "世界", "藏界", "仙景界", "国东界", "苦界", "法界",
    "境界", "海域",
    "天庭", "幽冥界",  # sub-realm, 应留 region(由 _TIER_OVERRIDES 处理)
]

# 普通/三国/西游常规地名 —— 绝不落入特殊空间
NEGATIVE_REGULAR = [
    "长安", "洛阳", "建业", "许昌", "许都", "成都", "汉中", "荆州", "益州",
    "徐州", "冀州", "幽州", "并州", "青州", "兖州", "豫州", "司隶", "赤壁",
    "官渡", "白帝城", "樊城", "襄阳", "新野", "麦城", "长坂", "街亭", "五丈原",
    "下邳", "寿春", "宛城", "江陵", "公安", "夏口", "庐江", "合肥", "吴郡",
    "会稽", "柴桑", "南郡", "濮阳", "平原", "邺城", "晋阳", "天水", "陇西",
    "武威", "张掖", "敦煌", "西凉", "江东", "江南", "中原", "河北", "河南",
    "河东", "关西", "山东", "山西", "淮南", "淮西", "淮东", "京畿", "京东",
    "京西", "两浙", "两广", "燕京", "汴京", "东京", "西京", "北京", "南京",
    "建康", "辽东", "辽西",
    "天下",  # uber-root, 非特殊空间
    "花果山", "水帘洞", "天竺国", "灵山", "龙宫", "荣国府", "大观园",
    "长安街", "黄原", "石圪节",
]


@pytest.mark.parametrize("name", POSITIVE)
def test_is_special_space_positive(name):
    assert is_special_space(name) is True, f"{name} 应识别为特殊空间"


@pytest.mark.parametrize("name", NEGATIVE_GEOGRAPHIC + NEGATIVE_REGULAR)
def test_is_special_space_negative(name):
    assert is_special_space(name) is False, f"{name} 不应识别为特殊空间"


def test_is_special_space_empty():
    assert is_special_space("") is False


# ── AC1: tier_classifier 把特殊空间归为 realm ─────────────────────────────
def test_tier_classifier_special_space_to_realm():
    tiers = {
        # 这些经 _NAME_SUFFIX_TIER 会被标 continent/region, 但应被 5.3 规则提升为 realm
        "仙界": "continent",
        "魔域": "continent",
        "灵界": "continent",
        "异空间": "site",
        "维度": "site",
        "秘境": "region",
        "结界": "continent",
        "洞天": "region",
        "幻境": "region",
        "太阳系": "continent",
        "三体世界": "site",
    }
    # mc>=2 模拟真实小说中这些地点有充分证据 (避免触发既有 mc==0 降级)
    freq = Counter({n: 5 for n in tiers})
    updates = TierClassifier._multi_feature_refine(
        tiers=tiers, parents={}, frequencies=freq, children_count={}
    )
    for name in tiers:
        assert updates.get(name) == "realm", f"{name} 应被重分类为 realm, 实际={updates.get(name)}"


def test_tier_classifier_subrealm_stays_region():
    # 天庭 / 幽冥界 是 sub-realm, 经 _TIER_OVERRIDES 强制 region, 不应变成 realm
    tiers = {"天庭": "continent", "幽冥界": "continent"}
    freq = Counter({n: 5 for n in tiers})
    updates = TierClassifier._multi_feature_refine(
        tiers=tiers, parents={}, frequencies=freq, children_count={}
    )
    assert updates.get("天庭") == "region"
    assert updates.get("幽冥界") == "region"
    assert is_special_space("天庭") is False
    assert is_special_space("幽冥界") is False


def test_tier_classifier_regular_unchanged():
    # 三国常规地名不应被改成 realm (5.3 不引入对常规地名的回归)
    tiers = {"长安": "city", "赤壁": "region", "长安街": "site"}
    freq = Counter({n: 5 for n in tiers})
    updates = TierClassifier._multi_feature_refine(
        tiers=tiers, parents={}, frequencies=freq, children_count={}
    )
    assert "长安" not in updates
    assert "赤壁" not in updates
    assert "长安街" not in updates


# ── AC2: 方向校验豁免 ─────────────────────────────────────────────────────
def test_contains_rank_exempts_special_space():
    # 特殊空间不参与 suffix rank 比较 → 返回 None (contains 方向修复会跳过)
    assert _get_contains_rank("仙界") is None
    assert _get_contains_rank("异空间") is None
    assert _get_contains_rank("魔域") is None
    # 常规地名仍返回 rank (_CONTAINS_SUFFIX_RANK 匹配: 上海=4, 青海=1)
    assert _get_contains_rank("上海") is not None
    assert _get_contains_rank("青海") is not None


# ── AC4: 三国无漂移 (regression) ─────────────────────────────────────────
def test_three_kingdoms_no_drift():
    drift = [n for n in NEGATIVE_REGULAR if is_special_space(n)]
    assert drift == [], f"三国地名被误判为特殊空间导致漂移: {drift}"
