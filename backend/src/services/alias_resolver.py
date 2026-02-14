"""Build alias → canonical name mapping for entity deduplication.

Uses entity_dictionary (from pre-scan) as primary source, falling back to
ChapterFact.characters[].new_aliases when no dictionary is available.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)

# ── Module-level cache ────────────────────────────

_alias_cache: dict[str, dict[str, str]] = {}  # novel_id -> alias_map


def invalidate_alias_cache(novel_id: str) -> None:
    """Clear cached alias map for a novel (call after prescan or analysis completes)."""
    _alias_cache.pop(novel_id, None)


# ── Union-Find ────────────────────────────────────


class _UnionFind:
    """Simple Union-Find to merge alias groups."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        """Return root -> list of members."""
        result: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            result[self.find(x)].append(x)
        return result


# ── Core function ─────────────────────────────────


async def build_alias_map(novel_id: str) -> dict[str, str]:
    """Build alias -> canonical_name mapping.

    Source priority:
    1. entity_dictionary (pre-scan LLM generated alias groups)
    2. ChapterFact.characters[].new_aliases (per-chapter extraction fallback)

    Canonical name rule: the name with highest frequency in the group.
    Returns {alias: canonical, ...}. The canonical name does NOT map to itself.
    """
    if novel_id in _alias_cache:
        return _alias_cache[novel_id]

    alias_map = await _build_from_dictionary(novel_id)
    if not alias_map:
        alias_map = await _build_from_chapter_facts(novel_id)

    _alias_cache[novel_id] = alias_map
    if alias_map:
        logger.info("Built alias map for novel %s: %d aliases", novel_id, len(alias_map))
    return alias_map


async def _build_from_dictionary(novel_id: str) -> dict[str, str]:
    """Build alias map from entity_dictionary table."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT name, frequency, aliases
            FROM entity_dictionary
            WHERE novel_id = ?
            ORDER BY frequency DESC
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    if not rows:
        return {}

    uf = _UnionFind()
    # name -> frequency for canonical selection
    freq: dict[str, int] = {}

    for row in rows:
        name = row["name"]
        frequency = row["frequency"] or 0
        aliases_raw = row["aliases"]
        aliases: list[str] = json.loads(aliases_raw) if aliases_raw else []

        freq[name] = max(freq.get(name, 0), frequency)
        uf.find(name)  # ensure registered

        for alias in aliases:
            if alias and alias != name:
                freq.setdefault(alias, 0)
                uf.union(name, alias)

    return _groups_to_map(uf, freq)


async def _build_from_chapter_facts(novel_id: str) -> dict[str, str]:
    """Fallback: build alias map from ChapterFact.characters[].new_aliases."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT cf.fact_json
            FROM chapter_facts cf
            WHERE cf.novel_id = ?
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    if not rows:
        return {}

    uf = _UnionFind()
    freq: dict[str, int] = defaultdict(int)

    for row in rows:
        data = json.loads(row["fact_json"])
        characters = data.get("characters", [])
        for char in characters:
            name = char.get("name", "")
            if not name:
                continue
            freq[name] += 1
            uf.find(name)

            for alias in char.get("new_aliases", []):
                if alias and alias != name:
                    freq.setdefault(alias, 0)
                    uf.union(name, alias)

    return _groups_to_map(uf, freq)


def _groups_to_map(uf: _UnionFind, freq: dict[str, int]) -> dict[str, str]:
    """Convert Union-Find groups into alias -> canonical mapping."""
    alias_map: dict[str, str] = {}

    for _root, members in uf.groups().items():
        if len(members) <= 1:
            continue
        # Pick canonical = highest frequency in group
        canonical = max(members, key=lambda m: freq.get(m, 0))
        for member in members:
            if member != canonical:
                alias_map[member] = canonical

    return alias_map
