"""Regression tests for Vietnamese alias merging."""

from __future__ import annotations

import json

import pytest

from src.services.alias_resolver import build_alias_map, invalidate_alias_cache


@pytest.fixture
def patch_alias_resolver_db(memory_db, monkeypatch):
    """Patch alias_resolver.get_connection to use the shared in-memory DB."""

    class _NonClosingConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            return None

    async def _factory():
        return _NonClosingConnection(memory_db)

    import src.services.alias_resolver as alias_resolver

    monkeypatch.setattr(alias_resolver, "get_connection", _factory)
    yield memory_db


@pytest.mark.asyncio
async def test_vietnamese_multi_word_aliases_merge_to_canonical(patch_alias_resolver_db):
    db = patch_alias_resolver_db
    novel_id = "vi-alias-fixture"
    invalidate_alias_cache(novel_id)

    await db.execute(
        """
        INSERT INTO novels
            (id, title, author, file_hash, total_chapters, total_words, source_language, prescan_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (novel_id, "Sông Núi Lam Sơn", "fixture", novel_id, 1, 1000, "vi", "completed"),
    )
    await db.execute(
        """
        INSERT INTO chapters
            (novel_id, chapter_num, title, content, word_count, analysis_status, is_excluded)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        """,
        (novel_id, 1, "Chương 1", "Trần Quốc Tuấn còn được gọi là Hưng Đạo Vương.", 52, "completed"),
    )

    rows = [
        ("Trần Quốc Tuấn", "person", 20, ["Quốc Tuấn", "Hưng Đạo Vương"]),
        ("Nguyễn Trãi", "person", 18, ["Ức Trai"]),
        ("Lê Lợi", "person", 17, ["Bình Định Vương"]),
    ]
    for name, entity_type, frequency, aliases in rows:
        await db.execute(
            """
            INSERT INTO entity_dictionary
                (novel_id, name, entity_type, frequency, confidence, aliases, source, sample_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                novel_id,
                name,
                entity_type,
                frequency,
                "high",
                json.dumps(aliases, ensure_ascii=False),
                "fixture",
                "",
            ),
        )

    await db.commit()

    alias_map = await build_alias_map(novel_id)

    assert alias_map["Quốc Tuấn"] == "Trần Quốc Tuấn"
    assert alias_map["Hưng Đạo Vương"] == "Trần Quốc Tuấn"
    assert alias_map["Ức Trai"] == "Nguyễn Trãi"
    assert alias_map["Bình Định Vương"] == "Lê Lợi"


@pytest.mark.asyncio
async def test_vietnamese_generic_aliases_do_not_bridge_people(patch_alias_resolver_db):
    db = patch_alias_resolver_db
    novel_id = "vi-alias-conflict-fixture"
    invalidate_alias_cache(novel_id)

    await db.execute(
        """
        INSERT INTO novels
            (id, title, author, file_hash, total_chapters, total_words, source_language, prescan_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (novel_id, "Sông Núi Lam Sơn", "fixture", novel_id, 1, 1000, "vi", "completed"),
    )

    rows = [
        ("Trần Quốc Tuấn", "person", 20, ["vị tướng"]),
        ("Lê Lợi", "person", 18, ["vị tướng"]),
    ]
    for name, entity_type, frequency, aliases in rows:
        await db.execute(
            """
            INSERT INTO entity_dictionary
                (novel_id, name, entity_type, frequency, confidence, aliases, source, sample_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                novel_id,
                name,
                entity_type,
                frequency,
                "medium",
                json.dumps(aliases, ensure_ascii=False),
                "fixture",
                "",
            ),
        )

    await db.commit()

    alias_map = await build_alias_map(novel_id)

    assert "vị tướng" not in alias_map
    assert alias_map == {}
