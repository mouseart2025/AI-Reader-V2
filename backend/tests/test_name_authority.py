"""Tests for name_authority — single source of truth for character name decisions.

These tests verify that the shared module correctly handles all naming
scenarios that previously caused regressions across NameResolver and AliasResolver.
"""

import pytest

from src.services.name_authority import (
    alias_safety_level,
    is_blocked_name,
    is_nickname_or_title,
    is_unsafe_alias,
    pick_canonical,
    CANONICAL_BLOCKLIST,
    GENERIC_PERSON_ALIASES,
    KINSHIP_TERMS,
)


# ── alias_safety_level ────────────────────────────────────────


class TestAliasSafetyLevel:
    """Test the unified safety level function."""

    def test_level0_kinship(self):
        for term in ["哥哥", "妈妈", "嫂子", "媳妇", "外公", "父王", "母后"]:
            assert alias_safety_level(term) == 0, f"{term} should be level 0"

    def test_level0_generic_person(self):
        for term in ["妖精", "老和尚", "师父", "泼猴", "呆子", "菩萨"]:
            assert alias_safety_level(term) == 0, f"{term} should be level 0"

    def test_level0_non_cjk_generic_person(self):
        for term in ["ông", "vị tướng", "nhà vua", "người lính", "soldier", "king"]:
            assert alias_safety_level(term) == 0, f"{term} should be level 0"

    def test_level0_title_prefix(self):
        for term in ["长老", "掌门", "帮主", "教主", "太尉", "知府"]:
            assert alias_safety_level(term) == 0, f"{term} should be level 0"

    def test_level0_surname_title(self):
        """韩前辈, 王师兄 etc. — contextual address, not stable alias."""
        for term in ["韩前辈", "林道友", "王师兄", "李大人"]:
            assert alias_safety_level(term) == 0, f"{term} should be level 0"

    def test_level1_soft_block(self):
        for term in ["众猴", "孩儿们"]:
            assert alias_safety_level(term) == 1, f"{term} should be level 1"

    def test_level1_single_char(self):
        assert alias_safety_level("僧") == 1

    def test_level2_real_names(self):
        for name in ["孙悟空", "唐僧", "猪八戒", "韩立", "贾宝玉", "林黛玉"]:
            assert alias_safety_level(name) == 2, f"{name} should be level 2"

    def test_level2_real_aliases(self):
        for alias in ["齐天大圣", "美猴王", "弼马温", "金蝉子", "陈玄奘"]:
            assert alias_safety_level(alias) == 2, f"{alias} should be level 2"

    def test_level2_qualified_royal(self):
        """Specific royal names should pass (not blocked by generic royal terms)."""
        for name in ["铁扇公主", "乌鸡国国王", "哪吒太子"]:
            assert alias_safety_level(name) >= 1, f"{name} should be safe"

    def test_level2_vietnamese_multi_word_names(self):
        """Vietnamese names and aliases are not subject to Chinese length heuristics."""
        for name in ["Trần Quốc Tuấn", "Hưng Đạo Vương", "Nguyễn Trãi", "Ức Trai", "Bình Định Vương"]:
            assert alias_safety_level(name) == 2, f"{name} should be level 2"
            assert not is_unsafe_alias(name), f"{name} should be safe"


# ── is_blocked_name ───────────────────────────────────────────


class TestIsBlockedName:
    """Test the unified blocked-name check (replaces _GENERIC_BLOCK)."""

    def test_blocks_generics(self):
        for name in ["哥哥", "师父", "徒弟", "大王", "妖精", "菩萨",
                      "老和尚", "那长老", "泼猴", "呆子"]:
            assert is_blocked_name(name), f"{name} should be blocked"

    def test_allows_real_names(self):
        for name in ["孙悟空", "唐僧", "猪八戒", "沙僧", "牛魔王", "哪吒"]:
            assert not is_blocked_name(name), f"{name} should NOT be blocked"

    def test_all_former_generic_block_entries_blocked(self):
        """Every term from the old _GENERIC_BLOCK must be blocked."""
        _OLD_GENERIC_BLOCK = {
            "哥哥", "弟弟", "姐姐", "妹妹", "外公", "师父", "师傅", "徒弟",
            "师兄", "师弟", "师姐", "师妹", "大哥", "大王", "大爷", "二爷",
            "老爷", "贤弟", "兄弟", "长老", "贫僧", "法师", "和尚", "陛下",
            "万岁", "圣上", "菩萨", "老师", "那厮", "泼猴", "呆子", "那长老",
            "老和尚", "小妖", "妖精", "妖怪", "那怪", "客官", "官人",
            "前辈", "晚辈", "道友", "仙子", "主人", "夫君",
        }
        for name in _OLD_GENERIC_BLOCK:
            assert is_blocked_name(name), f"{name} was in _GENERIC_BLOCK but is_blocked_name returns False"


# ── is_nickname_or_title ──────────────────────────────────────


class TestIsNicknameOrTitle:

    def test_title_suffix(self):
        for name in ["韩大夫", "张神医", "李大人", "赵施主"]:
            assert is_nickname_or_title(name), f"{name} should be nickname/title"

    def test_nickname_suffix(self):
        for name in ["齐天大圣", "混江龙", "花和尚"]:
            assert is_nickname_or_title(name), f"{name} should be nickname"

    def test_honglou_address(self):
        for name in ["宝二爷", "琏二爷"]:
            assert is_nickname_or_title(name), f"{name} should be nickname"

    def test_real_names_pass(self):
        for name in ["孙悟空", "宋江", "贾宝玉", "林黛玉", "唐僧"]:
            assert not is_nickname_or_title(name), f"{name} should NOT be nickname"

    def test_vietnamese_title_like_names_pass(self):
        for name in ["Hưng Đạo Vương", "Bình Định Vương", "Ức Trai"]:
            assert not is_nickname_or_title(name), f"{name} should NOT be nickname"


# ── pick_canonical ────────────────────────────────────────────


class TestPickCanonical:
    """Test the unified canonical selection function."""

    def test_prefer_high_freq_common_name(self):
        """唐僧(829) beats 陈玄奘(14) — the regression that triggered this refactor."""
        result = pick_canonical(
            ["陈玄奘", "唐僧", "三藏", "唐三藏"],
            {"陈玄奘": 14, "唐僧": 829, "三藏": 200, "唐三藏": 30},
        )
        assert result == "唐僧"

    def test_prefer_3char_full_name_with_freq(self):
        """孙悟空(152) preferred over 悟空(374) — 3-char full name rule."""
        result = pick_canonical(
            ["孙悟空", "悟空", "行者", "大圣"],
            {"孙悟空": 152, "悟空": 374, "行者": 300, "大圣": 100},
        )
        assert result == "孙悟空"

    def test_3char_needs_min_freq(self):
        """陈玄奘(14) does NOT beat 唐僧(829) — below freq threshold."""
        result = pick_canonical(
            ["陈玄奘", "唐僧"],
            {"陈玄奘": 14, "唐僧": 829},
        )
        assert result == "唐僧"

    def test_zhu_bajie_over_zhu_gangli(self):
        """猪八戒(182) beats 猪刚鬣(5) — common name over formal name."""
        result = pick_canonical(
            ["猪八戒", "猪刚鬣", "八戒", "天蓬元帅"],
            {"猪八戒": 182, "猪刚鬣": 5, "八戒": 1700, "天蓬元帅": 10},
        )
        assert result == "猪八戒"

    def test_song_jiang_over_song_gongming(self):
        """宋江(freq high) beats 宋公明(freq low) — v0.66 regression test."""
        result = pick_canonical(
            ["宋江", "宋公明", "及时雨"],
            {"宋江": 800, "宋公明": 30, "及时雨": 20},
        )
        assert result == "宋江"

    def test_dict_primary_preference(self):
        """When dict_primary_names provided, prefer dict members."""
        result = pick_canonical(
            ["孙悟空", "行者", "大圣"],
            {"孙悟空": 152, "行者": 300, "大圣": 100},
            dict_primary_names={"孙悟空"},
        )
        assert result == "孙悟空"

    def test_blocklist_excluded(self):
        """Canonical blocklist terms never become canonical."""
        result = pick_canonical(
            ["师父", "唐僧"],
            {"师父": 999, "唐僧": 100},
        )
        assert result == "唐僧"

    def test_nickname_demoted(self):
        """Nicknames (齐天大圣) demoted vs real names (孙悟空)."""
        result = pick_canonical(
            ["齐天大圣", "孙悟空"],
            {"齐天大圣": 200, "孙悟空": 152},
        )
        assert result == "孙悟空"

    def test_honglou_jia_baoyu(self):
        """贾宝玉(3-char, freq high) over 宝玉(2-char, freq higher)."""
        result = pick_canonical(
            ["贾宝玉", "宝玉", "宝二爷"],
            {"贾宝玉": 500, "宝玉": 2000, "宝二爷": 100},
        )
        assert result == "贾宝玉"

    def test_honglou_wang_xifeng(self):
        """王熙凤(3-char, freq ok) over 凤姐(2-char, freq higher)."""
        result = pick_canonical(
            ["王熙凤", "凤姐", "凤丫头", "琏二奶奶"],
            {"王熙凤": 200, "凤姐": 800, "凤丫头": 50, "琏二奶奶": 30},
        )
        assert result == "王熙凤"


# ── Consistency checks ────────────────────────────────────────


class TestConsistency:
    """Cross-check that constants are consistent."""

    def test_canonical_blocklist_covers_critical_generics(self):
        """CANONICAL_BLOCKLIST should include key generic person aliases."""
        critical = {"师父", "菩萨", "大王", "长老", "呆子", "泼猴",
                    "妖精", "妖怪", "老和尚"}
        for term in critical:
            assert term in CANONICAL_BLOCKLIST, \
                f"{term} is a critical generic but missing from CANONICAL_BLOCKLIST"

    def test_all_kinship_are_level0(self):
        """Every kinship term must be level 0."""
        for term in KINSHIP_TERMS:
            assert alias_safety_level(term) == 0, \
                f"kinship term {term} is not level 0"

    def test_all_generic_person_are_level0(self):
        """Every generic person alias must be level 0."""
        for term in GENERIC_PERSON_ALIASES:
            assert alias_safety_level(term) == 0, \
                f"generic person alias {term} is not level 0"
