"""Tests for FactValidator — location/person filtering, char variant normalization, homonym disambiguation."""

import pytest

from src.extraction.fact_validator import (
    _is_generic_location,
    _is_generic_person,
    _normalize_char_variants,
)
from src.utils.location_names import is_homonym_prone


# ── _is_generic_location tests ──────────────────────────────────


class TestGenericLocation:
    """Filtering generic/invalid location names."""

    def test_single_char_suffix(self):
        assert _is_generic_location("山") is not None
        assert _is_generic_location("河") is not None

    def test_abstract_conceptual(self):
        assert _is_generic_location("江湖") is not None
        assert _is_generic_location("天下") is not None

    def test_generic_facilities(self):
        assert _is_generic_location("酒店") is not None
        assert _is_generic_location("客栈") is not None

    def test_valid_location_passes(self):
        assert _is_generic_location("花果山") is None
        assert _is_generic_location("青牛镇") is None
        assert _is_generic_location("长安城") is None
        assert _is_generic_location("七玄门") is None

    def test_descriptive_phrase(self):
        assert _is_generic_location("自己的地界") is not None

    def test_too_long(self):
        assert _is_generic_location("一个非常非常长的地名描述") is not None

    def test_relative_position(self):
        assert _is_generic_location("山上") is not None
        assert _is_generic_location("城中") is not None

    def test_generic_modifier_suffix(self):
        assert _is_generic_location("小城") is not None
        assert _is_generic_location("大山") is not None
        assert _is_generic_location("小路") is not None

    def test_character_room(self):
        assert _is_generic_location("宝玉屋内") is not None
        assert _is_generic_location("贾母房中") is not None

    def test_noise_suffix_rule19(self):
        """Rule 19: LLM noise suffixes like '花果山届'."""
        assert _is_generic_location("花果山届") is not None
        assert _is_generic_location("某地的时候") is not None

    def test_noise_suffix_does_not_block_valid(self):
        """Valid names that happen to end with common chars should pass."""
        assert _is_generic_location("花果山") is None
        assert _is_generic_location("东胜神洲") is None


# ── _is_generic_person tests ────────────────────────────────────


class TestGenericPerson:
    """Filtering generic/invalid person names."""

    def test_pronouns_and_collective(self):
        assert _is_generic_person("众人") is not None
        assert _is_generic_person("他们") is not None

    def test_classical_generics(self):
        assert _is_generic_person("妇人") is not None
        assert _is_generic_person("老者") is not None

    def test_mythological_generics(self):
        """Bug #4b: 小妖/众妖 should be filtered."""
        assert _is_generic_person("小妖") is not None
        assert _is_generic_person("众妖") is not None
        assert _is_generic_person("小鬼") is not None
        assert _is_generic_person("老妖") is not None
        assert _is_generic_person("妖精") is not None
        assert _is_generic_person("妖怪") is not None
        assert _is_generic_person("众猴") is not None

    def test_military_generics(self):
        assert _is_generic_person("士兵") is not None
        assert _is_generic_person("山贼") is not None

    def test_pure_titles(self):
        assert _is_generic_person("长老") is not None
        assert _is_generic_person("掌门") is not None
        assert _is_generic_person("大王") is not None

    def test_valid_person_passes(self):
        assert _is_generic_person("孙悟空") is None
        assert _is_generic_person("韩立") is None
        assert _is_generic_person("唐僧") is None
        assert _is_generic_person("牛魔王") is None


# ── is_homonym_prone tests ──────────────────────────────────────


class TestHomonymProne:
    """Location names that need parent-prefix disambiguation."""

    def test_architectural_names(self):
        assert is_homonym_prone("夹道") is True
        assert is_homonym_prone("后门") is True
        assert is_homonym_prone("书房") is True

    def test_natural_terrain(self):
        """Bug #3: natural terrain names should be homonym-prone."""
        assert is_homonym_prone("树林") is True
        assert is_homonym_prone("山洞") is True
        assert is_homonym_prone("小路") is True
        assert is_homonym_prone("山坡") is True
        assert is_homonym_prone("洞口") is True

    def test_military_temporary(self):
        """Bug #3: military/temporary scene names."""
        assert is_homonym_prone("中军帐") is True
        assert is_homonym_prone("辕门") is True
        assert is_homonym_prone("营地") is True
        assert is_homonym_prone("大帐") is True

    def test_specific_names_not_homonym(self):
        assert is_homonym_prone("花果山") is False
        assert is_homonym_prone("青牛镇") is False
        assert is_homonym_prone("水帘洞") is False

    def test_short_arch_suffix_chars(self):
        assert is_homonym_prone("门") is True
        assert is_homonym_prone("厅") is True
        assert is_homonym_prone("堂") is True


# ── _normalize_char_variants tests ──────────────────────────────


class TestCharVariants:
    """Bug #9: CJK character variant normalization."""

    def test_zhan_to_shan(self):
        """南瞻部洲 → 南赡部洲"""
        assert _normalize_char_variants("南瞻部洲") == "南赡部洲"

    def test_ju_variant(self):
        """北倶芦洲 → 北俱芦洲"""
        assert _normalize_char_variants("北倶芦洲") == "北俱芦洲"

    def test_feng_variant(self):
        """峯 → 峰"""
        assert _normalize_char_variants("天峯山") == "天峰山"

    def test_kun_lun_variants(self):
        """崑崙 → 昆仑"""
        assert _normalize_char_variants("崑崙山") == "昆仑山"

    def test_no_change_for_standard(self):
        """Standard characters should pass through unchanged."""
        assert _normalize_char_variants("花果山") == "花果山"
        assert _normalize_char_variants("长安城") == "长安城"

    def test_empty_and_short(self):
        assert _normalize_char_variants("") == ""
        assert _normalize_char_variants("山") == "山"
