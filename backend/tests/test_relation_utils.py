"""Tests for relation_utils — normalization and category classification."""

from src.services.relation_utils import (
    classify_relation_category,
    derive_category_from_dimensions,
    normalize_relation_type,
)


class TestNormalizeRelationType:

    def test_one_sided_relation_types(self):
        """One-sided/attempted relations should NOT normalize to intimate types."""
        assert normalize_relation_type("求亲") == "求亲"
        assert normalize_relation_type("招亲") == "求亲"
        assert normalize_relation_type("求婚") == "求亲"
        assert normalize_relation_type("逼婚") == "逼婚"
        assert normalize_relation_type("爱慕") == "爱慕"
        assert normalize_relation_type("单相思") == "爱慕"
        assert normalize_relation_type("暗恋") == "爱慕"
        assert normalize_relation_type("倾慕") == "爱慕"
        assert normalize_relation_type("未遂") == "求亲"

    def test_intimate_types_unchanged(self):
        assert normalize_relation_type("夫妻") == "夫妻"
        assert normalize_relation_type("恋人") == "恋人"
        assert normalize_relation_type("情侣") == "恋人"


class TestClassifyRelationCategory:

    def test_one_sided_not_intimate(self):
        """One-sided relations must NOT be classified as intimate."""
        assert classify_relation_category("求亲") != "intimate"
        assert classify_relation_category("爱慕") != "intimate"

    def test_forced_marriage_is_hostile(self):
        assert classify_relation_category("逼婚") == "hostile"

    def test_courtship_is_social(self):
        assert classify_relation_category("求亲") == "social"
        assert classify_relation_category("爱慕") == "social"

    def test_romantic_lovers_are_intimate(self):
        assert classify_relation_category("恋人") == "intimate"

    def test_marriage_is_family(self):
        """Marriage is the primary kinship institution, classified as family."""
        assert classify_relation_category("夫妻") == "family"

    def test_sworn_brotherhood_is_intimate(self):
        """结拜兄弟 is an intimate cultural bond, not a casual social tie."""
        assert classify_relation_category("结拜兄弟") == "intimate"

    def test_same_master_peers_are_social(self):
        """师兄弟 / 同门 are horizontal peers under a shared master, not a vertical
        master-subordinate tie. Gold annotations (3 novels × 14 rows) consistently
        label these as social; graph coloring depends on this being a peer bond."""
        assert classify_relation_category("师兄弟") == "social"
        assert classify_relation_category("同门") == "social"

    def test_sister_in_law_brother_in_law_is_family(self):
        """嫂叔 / 嫂弟 are kinship ties through marriage."""
        assert classify_relation_category("嫂叔") == "family"
        assert normalize_relation_type("嫂弟") == "嫂叔"
        assert classify_relation_category(normalize_relation_type("嫂弟")) == "family"


class TestDeriveCategoryFromDimensions:
    """FR-1.4: dimension schema v1 -> legacy six-class category derivation.

    Value table frozen in docs/analysis/relation-dimension-schema-v1.md."""

    def test_culture_specific_subtypes(self):
        """Culture-specific relations land in their own subtype slots."""
        assert derive_category_from_dimensions("结拜") == "intimate"
        assert derive_category_from_dimensions("师门-同门") == "social"
        assert derive_category_from_dimensions("师门-师徒") == "hierarchical"
        assert derive_category_from_dimensions("辈分-亲属") == "family"

    def test_required_subtypes(self):
        assert derive_category_from_dimensions("主从") == "hierarchical"
        assert derive_category_from_dimensions("同盟") == "social"
        assert derive_category_from_dimensions("敌对") == "hostile"
        assert derive_category_from_dimensions("爱慕") == "social"

    def test_full_frozen_table(self):
        expected = {
            "辈分-亲属": "family",
            "结拜": "intimate",
            "婚恋": "intimate",
            "爱慕": "social",
            "师门-师徒": "hierarchical",
            "师门-同门": "social",
            "主从": "hierarchical",
            "君臣-上下级": "hierarchical",
            "同盟": "social",
            "朋友-社交": "social",
            "恩怨-报恩": "social",
            "敌对": "hostile",
            "其他": "other",
        }
        for subtype, category in expected.items():
            assert derive_category_from_dimensions(subtype) == category, subtype

    def test_no_dimension_data_returns_none(self):
        """Missing / out-of-vocabulary subtype must return None so the caller
        falls back to the legacy normalize+classify path (NFR-3)."""
        assert derive_category_from_dimensions(None) is None
        assert derive_category_from_dimensions(None, "positive", "close") is None
        assert derive_category_from_dimensions("不存在的类型") is None

    def test_polarity_and_closeness_do_not_affect_category(self):
        """v1 derives from rel_subtype only; other dimensions are orthogonal."""
        assert derive_category_from_dimensions("同盟", "negative", "distant") == "social"
        assert derive_category_from_dimensions("敌对", "positive", "close") == "hostile"

    def test_legacy_fallback_path_unchanged(self):
        """Without dimension data the legacy chain must behave as before."""
        assert derive_category_from_dimensions(None) is None
        assert classify_relation_category(normalize_relation_type("结拜兄弟")) == "intimate"
        assert classify_relation_category(normalize_relation_type("爱慕")) == "social"
        assert classify_relation_category(normalize_relation_type("师兄弟")) == "social"
