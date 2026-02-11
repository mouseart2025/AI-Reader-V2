import aiosqlite

from src.infra.config import DB_PATH, ensure_data_dir

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS novels (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    author          TEXT,
    file_hash       TEXT,
    total_chapters  INTEGER DEFAULT 0,
    total_words     INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    title           TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    sources_json    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_state (
    novel_id        TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
    last_chapter    INTEGER,
    scroll_position REAL,
    chapter_range   TEXT,
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_tasks (
    id              TEXT PRIMARY KEY,
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    status          TEXT DEFAULT 'pending',
    chapter_start   INTEGER NOT NULL,
    chapter_end     INTEGER NOT NULL,
    current_chapter INTEGER,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chapters_novel      ON chapters(novel_id, chapter_num);
CREATE INDEX IF NOT EXISTS idx_chapter_facts_novel  ON chapter_facts(novel_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv        ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_analysis_novel       ON analysis_tasks(novel_id, status);
"""


async def get_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(DB_PATH))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    return conn


async def init_db() -> None:
    ensure_data_dir()
    conn = await get_connection()
    try:
        await conn.executescript(_SCHEMA_SQL)
        await conn.commit()
    finally:
        await conn.close()
