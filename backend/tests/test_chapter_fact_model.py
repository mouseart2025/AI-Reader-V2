"""Regression tests for ChapterFact model validation tolerance.

Covers the class of failures where a weak/local LLM emits null for a required
string field and that single bad value fails validation of the ENTIRE chapter,
discarding all characters/locations/relations/events (Zhihu user report,
v0.71.8: spatial_relationships[].value = None).
"""

from src.extraction.chapter_fact_extractor import _normalize_field_names
from src.models.chapter_fact import ChapterFact, RelationshipFact, SpatialRelationship


class TestRelationshipFactDimensions:
    """FR-1.1: polarity / rel_subtype / closeness optional dimension fields."""

    def test_legacy_payload_deserializes_with_none_defaults(self):
        """Old JSON without dimension fields must deserialize unchanged."""
        rel = RelationshipFact.model_validate(
            {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟"}
        )
        assert rel.polarity is None
        assert rel.rel_subtype is None
        assert rel.closeness is None

    def test_dimension_fields_round_trip(self):
        payload = {
            "person_a": "宋江",
            "person_b": "武松",
            "relation_type": "结拜兄弟",
            "polarity": "positive",
            "rel_subtype": "结拜",
            "closeness": "close",
        }
        rel = RelationshipFact.model_validate(payload)
        assert (rel.polarity, rel.rel_subtype, rel.closeness) == ("positive", "结拜", "close")
        # Round-trip: dump -> re-validate yields an identical model
        rel2 = RelationshipFact.model_validate(rel.model_dump())
        assert rel2 == rel
        assert rel2.model_dump() == rel.model_dump()

    def test_partial_dimensions_round_trip(self):
        rel = RelationshipFact.model_validate(
            {"person_a": "林冲", "person_b": "高俅", "relation_type": "仇人", "rel_subtype": "敌对"}
        )
        assert rel.rel_subtype == "敌对"
        assert rel.polarity is None
        rel2 = RelationshipFact.model_validate_json(rel.model_dump_json())
        assert rel2 == rel


class TestSpatialRelationshipNoneTolerance:
    def test_value_none_coerced_to_empty(self):
        """value=None (common for contains/adjacent) must not raise."""
        sr = SpatialRelationship.model_validate(
            {"source": "花果山", "target": "傲来国", "relation_type": "contains", "value": None}
        )
        assert sr.value == ""
        assert sr.source == "花果山"
        assert sr.relation_type == "contains"

    def test_all_string_fields_none_coerced(self):
        sr = SpatialRelationship.model_validate(
            {
                "source": None,
                "target": None,
                "relation_type": None,
                "value": None,
                "confidence": None,
                "narrative_evidence": None,
            }
        )
        assert (sr.source, sr.target, sr.relation_type, sr.value) == ("", "", "", "")
        assert sr.confidence == ""
        assert sr.narrative_evidence == ""

    def test_value_omitted_defaults_empty(self):
        sr = SpatialRelationship.model_validate(
            {"source": "A", "target": "B", "relation_type": "adjacent"}
        )
        assert sr.value == ""


class TestChapterFactDoesNotFailOnNullSpatialValue:
    def _payload(self):
        return {
            "chapter_id": 11,
            "novel_id": "test",
            "characters": [{"name": "孙悟空"}],
            "locations": [{"name": "花果山", "type": "mountain"}],
            "spatial_relationships": [
                {"source": "花果山", "target": "傲来国", "relation_type": "contains", "value": None},
                {"source": "东海", "target": "花果山", "relation_type": "adjacent", "value": None},
            ],
            "events": [{"summary": "石猴出世", "type": "成长"}],
        }

    def test_whole_chapter_survives_null_spatial_value(self):
        """One null spatial value must not discard the rest of the chapter."""
        fact = ChapterFact.model_validate(self._payload())
        assert len(fact.characters) == 1
        assert fact.characters[0].name == "孙悟空"
        assert len(fact.locations) == 1
        assert len(fact.events) == 1
        assert len(fact.spatial_relationships) == 2
        assert all(sr.value == "" for sr in fact.spatial_relationships)

    def test_extractor_normalize_then_validate(self):
        """Mirror _call_and_parse: normalize field names then model_validate."""
        payload = self._payload()
        _normalize_field_names(payload)
        fact = ChapterFact.model_validate(payload)
        assert len(fact.spatial_relationships) == 2
        assert fact.spatial_relationships[0].relation_type == "contains"
