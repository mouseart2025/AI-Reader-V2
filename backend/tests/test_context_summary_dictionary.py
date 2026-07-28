"""Tests for entity-dictionary prompt hygiene."""

from unittest.mock import AsyncMock, patch

import pytest

from src.extraction.context_summary_builder import ContextSummaryBuilder
from src.models.entity_dict import EntityDictEntry


@pytest.mark.asyncio
async def test_dictionary_prompt_omits_ambiguous_frequency_candidates():
    entries = [
        EntityDictEntry(
            name="一口气",
            entity_type="unknown",
            frequency=1000,
            confidence="medium",
            aliases=[],
            source="freq",
        ),
        EntityDictEntry(
            name="孟奇",
            entity_type="person",
            frequency=500,
            confidence="high",
            aliases=[],
            source="dialogue",
        ),
        EntityDictEntry(
            name="神话",
            entity_type="unknown",
            frequency=10,
            confidence="high",
            aliases=[],
            source="naming",
        ),
    ]
    builder = ContextSummaryBuilder()

    with patch(
        "src.extraction.context_summary_builder.entity_dictionary_store.get_all",
        new=AsyncMock(return_value=entries),
    ):
        result = await builder._build_dictionary_section("novel-id")

    assert "孟奇（person" in result
    assert "一口气" not in result
    assert "神话" not in result
