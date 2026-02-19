"""Shared test fixtures for backend tests."""

import aiosqlite
import pytest
import pytest_asyncio

from unittest.mock import patch

# Schema copied from src/db/sqlite_db.py (inline to avoid importing config)
_TEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS novels (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    author          TEXT,
    file_hash       TEXT,
    total_chapters  INTEGER DEFAULT 0,
    total_words     INTEGER DEFAULT 0,
    prescan_status  TEXT DEFAULT 'pending',
    is_sample       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chapters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_num     INTEGER NOT NULL,
    volume_num      INTEGER,
    volume_title    TEXT,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    word_count      INTEGER DEFAULT 0,
    analysis_status TEXT DEFAULT 'pending',
    analyzed_at     TEXT,
    is_excluded     INTEGER DEFAULT 0,
    UNIQUE(novel_id, chapter_num)
);

CREATE TABLE IF NOT EXISTS chapter_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_id      INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    fact_json       TEXT NOT NULL,
    llm_model       TEXT,
    extracted_at    TEXT DEFAULT (datetime('now')),
    extraction_ms   INTEGER,
    UNIQUE(novel_id, chapter_id)
);

CREATE TABLE IF NOT EXISTS user_state (
    novel_id        TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
    last_chapter    INTEGER,
    scroll_position REAL,
    chapter_range   TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entity_dictionary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    entity_type     TEXT,
    frequency       INTEGER DEFAULT 0,
    confidence      TEXT DEFAULT 'medium',
    aliases         TEXT DEFAULT '[]',
    source          TEXT NOT NULL,
    sample_context  TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(novel_id, name)
);

CREATE TABLE IF NOT EXISTS world_structures (
    novel_id        TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
    structure_json   TEXT NOT NULL,
    source_chapters  TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);
"""


@pytest_asyncio.fixture
async def memory_db():
    """Create an in-memory SQLite database with full schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_TEST_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def mock_get_connection(memory_db):
    """Patch get_connection to return a shared in-memory DB.

    We wrap the real connection so close() is a no-op during tests
    (the fixture manages the lifecycle).
    """

    class _NonClosingConnection:
        """Proxy that prevents export_service from closing the shared conn."""

        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass  # no-op

    async def _factory():
        return _NonClosingConnection(memory_db)

    with patch("src.services.export_service.get_connection", _factory), \
         patch("src.services.sample_data_service.get_connection", _factory):
        yield memory_db
