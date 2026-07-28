"""Focused tests for entity embedding batch construction."""

from unittest.mock import Mock, patch

from src.services import embedding_service


def test_index_entities_deduplicates_ids_within_upsert_batch():
    collection = Mock()
    fact = {
        "characters": [
            {"name": "言无疆", "appearance": "第一次描述"},
            {"name": "言无疆", "appearance": "重复描述"},
        ],
        "locations": [
            {"name": "隐皇堡", "type": "城堡"},
            {"name": "隐皇堡", "type": "城堡"},
        ],
        "new_concepts": [
            {"name": "开窍", "category": "境界", "definition": "定义"},
            {"name": "开窍", "category": "境界", "definition": "重复定义"},
        ],
        "org_events": [
            {"org_name": "少林寺", "org_type": "门派"},
            {"org_name": "少林寺", "org_type": "门派"},
        ],
    }

    with patch.object(
        embedding_service,
        "_entities_collection",
        return_value=collection,
    ):
        embedding_service.index_entities_from_fact("novel", 13, fact)

    kwargs = collection.upsert.call_args.kwargs
    assert kwargs["ids"] == [
        "person_言无疆",
        "location_隐皇堡",
        "concept_开窍",
        "org_少林寺",
    ]
    assert len(kwargs["documents"]) == len(kwargs["ids"])
    assert len(kwargs["metadatas"]) == len(kwargs["ids"])
