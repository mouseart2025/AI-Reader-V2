"""Tests for relation_utils — normalization and category classification."""

from src.services.relation_utils import classify_relation_category, normalize_relation_type


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
