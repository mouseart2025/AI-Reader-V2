"""Regression tests for ChapterFact model validation tolerance.

Covers the class of failures where a weak/local LLM emits null for a required
string field and that single bad value fails validation of the ENTIRE chapter,
discarding all characters/locations/relations/events (Zhihu user report,
v0.71.8: spatial_relationships[].value = None).
"""

from src.extraction.chapter_fact_extractor import _normalize_field_names
from src.models.chapter_fact import ChapterFact, SpatialRelationship


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
