"""精确名 rank 保护(_NAME_RANK_EXACT)单测。

背景(2026-09-20):瓜洲(镇)被"洲"判成 continent、高唐州地界/寿春县界
等"X地界/X县界"被"界"判成 continent,导致 Auditor 按误判 rank 剔除
合法边(瓜洲→扬州 TIER_INVERSION、瓜洲渡口→瓜洲 SCALE_SKIP)。
保护名单只豁免有证据的条目;通用后缀规则不动。
"""

from src.services.world_structure_agent import (
    _NAME_RANK_EXACT,
    TIER_ORDER,
    _get_suffix_rank,
)
from src.utils.spatial_quality import check_spatial_constraints


class TestExactNameProtection:
    def test_protected_names_return_listed_tier_rank(self):
        assert _get_suffix_rank("瓜洲") == TIER_ORDER["city"]
        assert _get_suffix_rank("高唐州地界") == TIER_ORDER["region"]
        assert _get_suffix_rank("寿春县界") == TIER_ORDER["site"]
        assert _get_suffix_rank("昌平县界") == TIER_ORDER["site"]
        assert _get_suffix_rank("鹦鹉洲") == TIER_ORDER["site"]
        assert _get_suffix_rank("水泊梁山水域") == TIER_ORDER["region"]
        assert _get_suffix_rank("高太尉府") == TIER_ORDER["site"]
        assert _get_suffix_rank("太师府") == TIER_ORDER["site"]

    def test_unprotected_macro_names_keep_suffix_rule(self):
        # 真宏观地名不受保护影响:洲/界 仍是 continent
        for name in ("东胜神洲", "西牛贺洲", "幽冥界", "天界"):
            assert _get_suffix_rank(name) == TIER_ORDER["continent"]
        # 紫菱洲故意不收(其子树金标直属大观园,mis-rank 是 Auditor
        # 正确剔除子树误挂边的依据)
        assert _get_suffix_rank("紫菱洲") == TIER_ORDER["continent"]

    def test_general_suffix_rule_unchanged(self):
        # 通用规则不动:未入名单的 州/县/山/村 仍按后缀判
        assert _get_suffix_rank("扬州") == TIER_ORDER["kingdom"]
        assert _get_suffix_rank("阳谷县") == TIER_ORDER["region"]
        assert _get_suffix_rank("五台山") == TIER_ORDER["region"]
        assert _get_suffix_rank("石碣村") == TIER_ORDER["site"]

    def test_protection_fixes_quirk_edges(self):
        # 瓜洲(city=4)→扬州(kingdom=2):正常序,gap=2 → 无 TIER_INVERSION/SCALE_SKIP
        assert check_spatial_constraints({"瓜洲": "扬州"}) == []
        # 瓜洲渡口(site=5)→瓜洲(city=4):gap=1 → 无 SCALE_SKIP
        assert check_spatial_constraints({"瓜洲渡口": "瓜洲"}) == []
        # 高唐州地界(region=3)→高唐州(kingdom=2):正常序 → 无 TIER_INVERSION
        assert check_spatial_constraints({"高唐州地界": "高唐州"}) == []
        # 金天圣帝庙(site=5)→寿春县界(site=3→site=5 保护后):gap=0 → 无 SCALE_SKIP
        assert check_spatial_constraints({"金天圣帝庙": "寿春县界"}) == []
        # 高太尉府(site=5)→东京(city=4):正常序 → 无 TIER_INVERSION(金标边)
        assert check_spatial_constraints({"高太尉府": "东京"}) == []

    def test_true_scale_skip_still_flagged(self):
        # 真跨级不受影响:building 直挂 continent 宏观根仍报
        violations = check_spatial_constraints({"怡红院": "东胜神洲"})
        assert any(v["code"] == "SCALE_SKIP" for v in violations)

    def test_all_listed_tiers_are_valid(self):
        for name, tier in _NAME_RANK_EXACT.items():
            assert tier in TIER_ORDER, f"{name}: 未知 tier {tier}"
            # 保护名单只用于把宏观误判降级,不允许把名字抬到 world/continent
            assert TIER_ORDER[tier] >= TIER_ORDER["kingdom"], (
                f"{name}: 保护项不应赋予宏观 rank")
