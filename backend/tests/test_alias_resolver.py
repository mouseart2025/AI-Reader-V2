"""Tests for alias_resolver — safety levels, char variant normalization, Union-Find merging."""

import pytest

from src.services.alias_resolver import _alias_safety_level


# ── _alias_safety_level tests ───────────────────────────────────


class TestAliasSafetyLevel:
    """Safety level classification for alias names."""

    # Level 0: hard-block

    def test_kinship_terms_blocked(self):
        assert _alias_safety_level("哥哥") == 0
        assert _alias_safety_level("妈妈") == 0

    def test_generic_person_blocked(self):
        assert _alias_safety_level("老人") == 0
        assert _alias_safety_level("妇人") == 0
        assert _alias_safety_level("妖精") == 0

    def test_pure_titles_blocked(self):
        assert _alias_safety_level("长老") == 0
        assert _alias_safety_level("掌门") == 0
        assert _alias_safety_level("师父") == 0

    def test_deictic_pattern_blocked(self):
        """那+role pattern: 那贼, 那厮."""
        assert _alias_safety_level("那贼") == 0
        assert _alias_safety_level("那厮") == 0

    def test_surname_title_blocked(self):
        """韩前辈, 林道友 etc."""
        assert _alias_safety_level("韩前辈") == 0
        assert _alias_safety_level("王师兄") == 0

    # Level 1: soft-block

    def test_too_long_suspicious(self):
        assert _alias_safety_level("独孤求败风清扬令狐冲") == 1  # 9 chars, no "的"

    def test_collective_markers(self):
        assert _alias_safety_level("众猴") == 1
        assert _alias_safety_level("孩儿们") == 1

    def test_single_char_suspicious(self):
        assert _alias_safety_level("僧") == 1

    def test_quantity_phrase(self):
        """Numeric prefix + measure word = quantity, not name."""
        assert _alias_safety_level("两个仙女") == 1
        assert _alias_safety_level("百余人") == 1

    # Level 2: safe

    def test_valid_names_safe(self):
        assert _alias_safety_level("孙悟空") == 2
        assert _alias_safety_level("韩立") == 2
        assert _alias_safety_level("唐僧") == 2
        assert _alias_safety_level("三藏") == 2
        assert _alias_safety_level("行者") == 2
        assert _alias_safety_level("悟空") == 2

    def test_journey_west_aliases_safe(self):
        """Bug #5: key Journey to the West aliases should be level 2."""
        assert _alias_safety_level("陈玄奘") == 2
        assert _alias_safety_level("唐三藏") == 2
        assert _alias_safety_level("江流") == 2
        assert _alias_safety_level("齐天大圣") == 2
        assert _alias_safety_level("美猴王") == 2
        assert _alias_safety_level("孙行者") == 2

    def test_legitimate_numeric_prefix_names(self):
        """Names like 二愣子, 三太子 should pass."""
        assert _alias_safety_level("二愣子") == 2
        assert _alias_safety_level("三太子") == 2
        assert _alias_safety_level("一灯大师") == 2

    def test_empty_blocked(self):
        assert _alias_safety_level("") == 0
