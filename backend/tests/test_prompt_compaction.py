"""Tests for lossless prompt JSON compaction."""

import json

from src.extraction.chapter_fact_extractor import _serialize_prompt_json


def test_compact_prompt_json_is_equivalent_and_shorter():
    value = {
        "type": "object",
        "properties": {
            "characters": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }

    pretty = _serialize_prompt_json(value, compact=False)
    compact = _serialize_prompt_json(value, compact=True)

    assert json.loads(compact) == json.loads(pretty) == value
    assert len(compact) < len(pretty)
    assert "\n" not in compact
