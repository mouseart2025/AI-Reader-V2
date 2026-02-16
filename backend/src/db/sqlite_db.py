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
    prescan_status  TEXT DEFAULT 'pending',
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

CREATE TABLE IF NOT EXISTS map_layouts (
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    chapter_hash    TEXT NOT NULL,
    layout_json     TEXT NOT NULL,
    layout_mode     TEXT NOT NULL DEFAULT 'hierarchy',
    terrain_path    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (novel_id, chapter_hash)
);

CREATE TABLE IF NOT EXISTS map_user_overrides (
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    location_name   TEXT NOT NULL,
    x               REAL NOT NULL,
    y               REAL NOT NULL,
    updated_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (novel_id, location_name)
);

CREATE TABLE IF NOT EXISTS world_structures (
    novel_id        TEXT PRIMARY KEY REFERENCES novels(id) ON DELETE CASCADE,
    structure_json   TEXT NOT NULL,
    source_chapters  TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS layer_layouts (
    novel_id        TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    layer_id        TEXT NOT NULL,
    chapter_hash    TEXT NOT NULL,
    layout_json     TEXT NOT NULL,
    layout_mode     TEXT NOT NULL DEFAULT 'hierarchy',
    terrain_path    TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (novel_id, layer_id, chapter_hash)
);

CREATE TABLE IF NOT EXISTS world_structure_overrides (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id      TEXT NOT NULL REFERENCES novels(id) ON DELETE CASCADE,
    override_type TEXT NOT NULL,
    override_key  TEXT NOT NULL,
    override_json TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(novel_id, override_type, override_key)
);

CREATE INDEX IF NOT EXISTS idx_ws_overrides_novel ON world_structure_overrides(novel_id);

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

CREATE INDEX IF NOT EXISTS idx_entity_dict_novel    ON entity_dictionary(novel_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_chapters_novel       ON chapters(novel_id, chapter_num);
CREATE INDEX IF NOT EXISTS idx_chapter_facts_novel   ON chapter_facts(novel_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv         ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_analysis_novel        ON analysis_tasks(novel_id, status);
CREATE INDEX IF NOT EXISTS idx_layer_layouts_novel   ON layer_layouts(novel_id);
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
        # Migration: add prescan_status to novels for existing databases
        try:
            await conn.execute(
                "ALTER TABLE novels ADD COLUMN prescan_status TEXT DEFAULT 'pending'"
            )
        except Exception:
            pass  # Column already exists
        # Migration: add is_excluded to chapters for chapter exclusion feature
        try:
            await conn.execute(
                "ALTER TABLE chapters ADD COLUMN is_excluded INTEGER DEFAULT 0"
            )
        except Exception:
            pass  # Column already exists
        # Migration: add lat/lng to map_user_overrides for geographic coordinate overrides
        try:
            await conn.execute(
                "ALTER TABLE map_user_overrides ADD COLUMN lat REAL"
            )
            await conn.execute(
                "ALTER TABLE map_user_overrides ADD COLUMN lng REAL"
            )
        except Exception:
            pass  # Columns already exist
        await conn.commit()
    finally:
        await conn.close()
