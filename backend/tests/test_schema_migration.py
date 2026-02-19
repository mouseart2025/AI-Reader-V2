"""Tests for schema migration: is_sample column in novels table."""

import aiosqlite
import pytest
import pytest_asyncio


@pytest.mark.asyncio
async def test_is_sample_column_exists(memory_db):
    """Verify is_sample column exists with default value 0."""
    await memory_db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES (?, ?, ?)",
        ("n1", "测试", 1),
    )
    await memory_db.commit()
    cursor = await memory_db.execute("SELECT is_sample FROM novels WHERE id = 'n1'")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


@pytest.mark.asyncio
async def test_is_sample_can_be_set(memory_db):
    """Verify is_sample can be updated to 1."""
    await memory_db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES (?, ?, ?)",
        ("n2", "样本", 1),
    )
    await memory_db.execute("UPDATE novels SET is_sample = 1 WHERE id = 'n2'")
    await memory_db.commit()
    cursor = await memory_db.execute("SELECT is_sample FROM novels WHERE id = 'n2'")
    row = await cursor.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_alter_table_migration_idempotent():
    """Verify ALTER TABLE migration is safe on a DB that already has is_sample."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys=ON")
    # Create table WITH is_sample (as new DBs will)
    await conn.execute("""
        CREATE TABLE novels (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            is_sample INTEGER DEFAULT 0
        )
    """)
    await conn.commit()
    # Migration should not raise
    try:
        await conn.execute(
            "ALTER TABLE novels ADD COLUMN is_sample INTEGER DEFAULT 0"
        )
    except Exception:
        pass  # Expected: column already exists
    # Table still works
    await conn.execute(
        "INSERT INTO novels (id, title, is_sample) VALUES ('x', 'test', 1)"
    )
    await conn.commit()
    cursor = await conn.execute("SELECT is_sample FROM novels WHERE id = 'x'")
    row = await cursor.fetchone()
    assert row[0] == 1
    await conn.close()
