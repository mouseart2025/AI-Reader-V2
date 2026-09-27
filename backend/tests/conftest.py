"""Shared test fixtures for backend tests."""

import asyncio
import os
import tempfile
from unittest.mock import patch

import aiosqlite
import pytest
import pytest_asyncio

# ── Point every test at a throwaway data dir ──
# This must run BEFORE the first import of src.infra.config, which resolves
# AI_READER_DATA_DIR at module load. Without it, any test that reaches the real
# get_connection() reads the DEVELOPER's own ~/.ai-reader-v2/data.db — that file
# exists here with the full schema, but does not exist on a fresh CI runner,
# where those tests fail with "unable to open database file". main has been red
# that way since 2026-09-23 (8 failures across test_entity_visibility,
# test_map_geo_artifacts, test_map_geo_coords_cache and test_map_geo_failed).
# setdefault, not assignment, so an explicitly exported value still wins.
os.environ.setdefault(
    "AI_READER_DATA_DIR", tempfile.mkdtemp(prefix="ai-reader-test-data-")
)

# Imported only AFTER the env var above — the order here is load-bearing.
from src.db.sqlite_db import _SCHEMA_SQL as _BASE_SCHEMA

# Migrations that are applied via ALTER TABLE in init_db() but not in base schema.
# We add them here so test DBs have the full schema from the start.
_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    scroll_position REAL DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(novel_id, chapter_num, scroll_position)
);
ALTER TABLE chapters ADD COLUMN is_excluded INTEGER DEFAULT 0;
ALTER TABLE chapters ADD COLUMN analysis_error TEXT;
ALTER TABLE chapters ADD COLUMN error_type TEXT;
ALTER TABLE map_user_overrides ADD COLUMN lat REAL;
ALTER TABLE map_user_overrides ADD COLUMN lng REAL;
ALTER TABLE map_user_overrides ADD COLUMN constraint_type TEXT DEFAULT 'position';
ALTER TABLE map_user_overrides ADD COLUMN locked_parent TEXT;
ALTER TABLE chapter_facts ADD COLUMN input_tokens INTEGER;
ALTER TABLE chapter_facts ADD COLUMN output_tokens INTEGER;
ALTER TABLE chapter_facts ADD COLUMN cost_usd REAL;
ALTER TABLE chapter_facts ADD COLUMN cost_cny REAL;
ALTER TABLE chapter_facts ADD COLUMN scenes_json TEXT;
ALTER TABLE chapter_facts ADD COLUMN is_truncated INTEGER DEFAULT 0;
ALTER TABLE chapter_facts ADD COLUMN segment_count INTEGER DEFAULT 1;
ALTER TABLE chapter_facts ADD COLUMN output_truncated INTEGER DEFAULT 0;
ALTER TABLE analysis_tasks ADD COLUMN timing_summary TEXT;
ALTER TABLE map_layouts ADD COLUMN satisfaction_json TEXT;
ALTER TABLE map_geo_artifacts ADD COLUMN geo_coords_json TEXT;
"""

_TEST_SCHEMA = _BASE_SCHEMA


@pytest.fixture(scope="session", autouse=True)
def _app_database() -> None:
    """Create the app DB (its dir + the full schema) once, before any test runs.

    A few tests drive code paths that open the REAL database instead of the
    in-memory fixture — they patch get_connection in export_service and
    sample_data_service, but not in novel_store. Those paths only ever passed
    because the developer's own DB happened to already exist.
    """
    from src.db.sqlite_db import init_db

    asyncio.run(init_db())


@pytest_asyncio.fixture
async def memory_db():
    """Create an in-memory SQLite database with full schema."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_TEST_SCHEMA)
    # Apply migrations — split on semicolons, execute each statement individually
    # so failures on individual ALTER TABLE (column exists) don't block others.
    for stmt in _MIGRATION_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                await conn.execute(stmt)
            except Exception:
                pass  # Column already exists or table already created
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

    import src.services.export_service as _export_mod

    async def _noop_precompute(_novel_id):
        return None

    with patch("src.services.export_service.get_connection", _factory), \
         patch.object(_export_mod, "_build_precomputed", _noop_precompute), \
         patch("src.services.sample_data_service.get_connection", _factory):
        yield memory_db
