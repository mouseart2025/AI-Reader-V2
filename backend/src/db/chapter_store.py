"""Data access layer for chapter reading and user state."""

from src.db.sqlite_db import get_connection


async def list_chapters(novel_id: str) -> list[dict]:
    """List all chapters for a novel with analysis status and volume info."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, novel_id, chapter_num, volume_num, volume_title,
                   title, word_count, analysis_status, analyzed_at, is_excluded
            FROM chapters
            WHERE novel_id = ?
            ORDER BY chapter_num
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def get_chapter_content(novel_id: str, chapter_num: int) -> dict | None:
    """Get a single chapter's full content."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT id, novel_id, chapter_num, volume_num, volume_title,
                   title, content, word_count, analysis_status, analyzed_at, is_excluded
            FROM chapters
            WHERE novel_id = ? AND chapter_num = ?
            """,
            (novel_id, chapter_num),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def get_chapter_entities(novel_id: str, chapter_num: int) -> list[dict]:
    """Get entity names from a chapter's ChapterFact for highlighting."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT cf.fact_json
            FROM chapter_facts cf
            JOIN chapters c ON cf.chapter_id = c.id AND cf.novel_id = c.novel_id
            WHERE c.novel_id = ? AND c.chapter_num = ?
            """,
            (novel_id, chapter_num),
        )
        row = await cursor.fetchone()
        if not row:
            return []

        import json

        fact = json.loads(row["fact_json"])
        entities: list[dict] = []

        for ch in fact.get("characters", []):
            if ch.get("name"):
                entities.append({"name": ch["name"], "type": "person"})

        for loc in fact.get("locations", []):
            if loc.get("name"):
                entities.append({"name": loc["name"], "type": "location"})

        for ie in fact.get("item_events", []):
            if ie.get("item_name"):
                entities.append({"name": ie["item_name"], "type": "item"})

        for oe in fact.get("org_events", []):
            if oe.get("org_name"):
                entities.append({"name": oe["org_name"], "type": "org"})

        for nc in fact.get("new_concepts", []):
            if nc.get("name"):
                entities.append({"name": nc["name"], "type": "concept"})

        # Also extract person names from event participants and
        # location names from event locations (LLM sometimes puts
        # entities only in events instead of the dedicated arrays)
        for ev in fact.get("events", []):
            for p in ev.get("participants", []):
                if p:
                    entities.append({"name": p, "type": "person"})
            loc = ev.get("location")
            # Skip compound locations like "山村/七玄门" or "山村→城镇"
            if loc and "/" not in loc and "→" not in loc and "（" not in loc:
                entities.append({"name": loc, "type": "location"})

        # Also extract person names from relationship facts
        for rel in fact.get("relationships", []):
            if rel.get("person_a"):
                entities.append({"name": rel["person_a"], "type": "person"})
            if rel.get("person_b"):
                entities.append({"name": rel["person_b"], "type": "person"})

        # Deduplicate by (name, type)
        seen: set[tuple[str, str]] = set()
        unique: list[dict] = []
        for e in entities:
            key = (e["name"], e["type"])
            if key not in seen:
                seen.add(key)
                unique.append(e)

        return unique
    finally:
        await conn.close()


async def get_user_state(novel_id: str) -> dict | None:
    """Get the user's reading state for a novel."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT novel_id, last_chapter, scroll_position, chapter_range, updated_at
            FROM user_state
            WHERE novel_id = ?
            """,
            (novel_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def search_chapters(
    novel_id: str, query: str, limit: int = 50
) -> list[dict]:
    """Full-text search across all chapters of a novel."""
    conn = await get_connection()
    try:
        # Use LIKE for simple substring matching
        pattern = f"%{query}%"
        cursor = await conn.execute(
            """
            SELECT chapter_num, title, content
            FROM chapters
            WHERE novel_id = ? AND content LIKE ?
            ORDER BY chapter_num
            LIMIT ?
            """,
            (novel_id, pattern, limit),
        )
        rows = await cursor.fetchall()
        results: list[dict] = []
        for row in rows:
            content: str = row["content"]
            idx = content.lower().find(query.lower())
            if idx < 0:
                continue
            # Extract context snippet (100 chars around match)
            start = max(0, idx - 50)
            end = min(len(content), idx + len(query) + 50)
            snippet = content[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(content):
                snippet = snippet + "..."
            results.append({
                "chapter_num": row["chapter_num"],
                "title": row["title"],
                "snippet": snippet,
            })
        return results
    finally:
        await conn.close()


async def save_user_state(
    novel_id: str,
    last_chapter: int,
    scroll_position: float = 0.0,
    chapter_range: str | None = None,
) -> None:
    """Save or update the user's reading state."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            INSERT INTO user_state (novel_id, last_chapter, scroll_position, chapter_range, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(novel_id) DO UPDATE SET
                last_chapter = excluded.last_chapter,
                scroll_position = excluded.scroll_position,
                chapter_range = excluded.chapter_range,
                updated_at = datetime('now')
            """,
            (novel_id, last_chapter, scroll_position, chapter_range),
        )
        await conn.commit()
    finally:
        await conn.close()


async def set_chapters_excluded(
    novel_id: str,
    chapter_nums: list[int],
    excluded: bool,
) -> None:
    """Batch set is_excluded for specified chapters.

    When restoring (excluded=False), also resets analysis_status to 'pending'
    so the chapters can be re-analyzed.
    """
    if not chapter_nums:
        return
    conn = await get_connection()
    try:
        placeholders = ",".join("?" for _ in chapter_nums)
        if excluded:
            await conn.execute(
                f"""
                UPDATE chapters SET is_excluded = 1
                WHERE novel_id = ? AND chapter_num IN ({placeholders})
                """,
                [novel_id, *chapter_nums],
            )
        else:
            await conn.execute(
                f"""
                UPDATE chapters SET is_excluded = 0, analysis_status = 'pending', analyzed_at = NULL
                WHERE novel_id = ? AND chapter_num IN ({placeholders})
                """,
                [novel_id, *chapter_nums],
            )
        await conn.commit()
    finally:
        await conn.close()


async def get_bookmarks(novel_id: str) -> list[dict]:
    """List all bookmarks for a novel."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                scroll_position REAL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(novel_id, chapter_num, scroll_position)
            )
            """
        )
        cursor = await conn.execute(
            """
            SELECT id, novel_id, chapter_num, scroll_position, note, created_at
            FROM bookmarks
            WHERE novel_id = ?
            ORDER BY created_at DESC
            """,
            (novel_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


async def add_bookmark(
    novel_id: str,
    chapter_num: int,
    scroll_position: float = 0.0,
    note: str = "",
) -> dict:
    """Add a bookmark. Returns the created bookmark."""
    conn = await get_connection()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                scroll_position REAL DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(novel_id, chapter_num, scroll_position)
            )
            """
        )
        cursor = await conn.execute(
            """
            INSERT INTO bookmarks (novel_id, chapter_num, scroll_position, note)
            VALUES (?, ?, ?, ?)
            """,
            (novel_id, chapter_num, round(scroll_position, 4), note),
        )
        await conn.commit()
        bookmark_id = cursor.lastrowid
        cursor2 = await conn.execute(
            "SELECT id, novel_id, chapter_num, scroll_position, note, created_at FROM bookmarks WHERE id = ?",
            (bookmark_id,),
        )
        row = await cursor2.fetchone()
        return dict(row) if row else {}
    finally:
        await conn.close()


async def delete_bookmark(bookmark_id: int) -> bool:
    """Delete a bookmark by ID."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "DELETE FROM bookmarks WHERE id = ?",
            (bookmark_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def delete_chapter_facts(
    novel_id: str,
    chapter_nums: list[int],
) -> int:
    """Delete chapter_facts for specified chapters. Returns number deleted."""
    if not chapter_nums:
        return 0
    conn = await get_connection()
    try:
        placeholders = ",".join("?" for _ in chapter_nums)
        cursor = await conn.execute(
            f"""
            DELETE FROM chapter_facts
            WHERE novel_id = ? AND chapter_id IN (
                SELECT id FROM chapters
                WHERE novel_id = ? AND chapter_num IN ({placeholders})
            )
            """,
            [novel_id, novel_id, *chapter_nums],
        )
        await conn.commit()
        return cursor.rowcount
    finally:
        await conn.close()
