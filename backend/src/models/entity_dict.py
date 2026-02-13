"""Entity dictionary entry model for pre-scan results."""

from __future__ import annotations

from pydantic import BaseModel


class EntityDictEntry(BaseModel):
    name: str
    entity_type: str = "unknown"
    frequency: int = 0
    confidence: str = "medium"  # low / medium / high
    aliases: list[str] = []
    source: str  # freq / ngram / dialogue / title / suffix / llm
    sample_context: str | None = None
