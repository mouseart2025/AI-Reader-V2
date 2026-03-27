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

    def test_intimate_still_works(self):
        assert classify_relation_category("夫妻") == "intimate"
        assert classify_relation_category("恋人") == "intimate"
