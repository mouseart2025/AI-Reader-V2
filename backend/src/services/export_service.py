"""Export / import novel data as JSON for migration between machines."""

import json
import uuid

from src.db.sqlite_db import get_connection


async def export_novel(novel_id: str) -> dict:
    """Export a novel with all associated data (chapters, chapter_facts, user_state).

    Returns a dict suitable for JSON serialisation.
    """
    conn = await get_connection()
    try:
        # Novel metadata
        cur = await conn.execute(
            "SELECT id, title, author, file_hash, total_chapters, total_words, created_at, updated_at FROM novels WHERE id = ?",
            (novel_id,),
        )
        novel_row = await cur.fetchone()
        if not novel_row:
            raise ValueError(f"Novel {novel_id} not found")
        novel = dict(novel_row)

        # Chapters (including content)
        cur = await conn.execute(
            "SELECT chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
            (novel_id,),
        )
        chapters = [dict(r) for r in await cur.fetchall()]

        # Chapter facts (join with chapters to get chapter_num for portable mapping)
        cur = await conn.execute(
            """SELECT cf.chapter_id, c.chapter_num, cf.fact_json, cf.llm_model, cf.extracted_at, cf.extraction_ms
               FROM chapter_facts cf
               JOIN chapters c ON c.id = cf.chapter_id AND c.novel_id = cf.novel_id
               WHERE cf.novel_id = ?""",
            (novel_id,),
        )
        facts = [dict(r) for r in await cur.fetchall()]

        # User state
        cur = await conn.execute(
            "SELECT last_chapter, scroll_position, chapter_range, updated_at FROM user_state WHERE novel_id = ?",
            (novel_id,),
        )
        user_state_row = await cur.fetchone()
        user_state = dict(user_state_row) if user_state_row else None

        return {
            "format_version": 1,
            "novel": novel,
            "chapters": chapters,
            "chapter_facts": facts,
            "user_state": user_state,
        }
    finally:
        await conn.close()


async def import_novel(data: dict, overwrite: bool = False) -> dict:
    """Import a novel from exported JSON data.

    If overwrite=True and a novel with the same title exists, replace it.
    Otherwise create with a fresh ID.

    Returns the imported novel metadata dict.
    """
    if data.get("format_version") != 1:
        raise ValueError("Unsupported export format version")

    novel_meta = data["novel"]
    chapters = data.get("chapters", [])
    facts = data.get("chapter_facts", [])
    user_state = data.get("user_state")

    conn = await get_connection()
    try:
        # Check for existing novel by title
        cur = await conn.execute(
            "SELECT id FROM novels WHERE title = ?", (novel_meta["title"],)
        )
        existing = await cur.fetchone()

        if existing and overwrite:
            # Delete the old novel (CASCADE removes chapters, facts, etc.)
            await conn.execute("DELETE FROM novels WHERE id = ?", (existing["id"],))

        novel_id = str(uuid.uuid4())

        # Insert novel
        await conn.execute(
            "INSERT INTO novels (id, title, author, file_hash, total_chapters, total_words, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                novel_id,
                novel_meta["title"],
                novel_meta.get("author"),
                novel_meta.get("file_hash"),
                novel_meta.get("total_chapters", len(chapters)),
                novel_meta.get("total_words", 0),
                novel_meta.get("created_at"),
                novel_meta.get("updated_at"),
            ),
        )

        # Insert chapters
        if chapters:
            await conn.executemany(
                "INSERT INTO chapters (novel_id, chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        ch["chapter_num"],
                        ch.get("volume_num"),
                        ch.get("volume_title"),
                        ch["title"],
                        ch["content"],
                        ch.get("word_count", 0),
                        ch.get("analysis_status", "pending"),
                        ch.get("analyzed_at"),
                    )
                    for ch in chapters
                ],
            )

        # Re-insert chapter facts, mapping chapter_num → new chapter_id
        if facts:
            cur = await conn.execute(
                "SELECT id, chapter_num FROM chapters WHERE novel_id = ?",
                (novel_id,),
            )
            ch_map = {row["chapter_num"]: row["id"] for row in await cur.fetchall()}

            for fact in facts:
                chapter_num = fact.get("chapter_num")
                if chapter_num is not None and chapter_num in ch_map:
                    await conn.execute(
                        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json, llm_model, extracted_at, extraction_ms) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            novel_id,
                            ch_map[chapter_num],
                            fact["fact_json"],
                            fact.get("llm_model"),
                            fact.get("extracted_at"),
                            fact.get("extraction_ms"),
                        ),
                    )

        # Restore user state
        if user_state:
            await conn.execute(
                "INSERT INTO user_state (novel_id, last_chapter, scroll_position, chapter_range, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    novel_id,
                    user_state.get("last_chapter"),
                    user_state.get("scroll_position"),
                    user_state.get("chapter_range"),
                    user_state.get("updated_at"),
                ),
            )

        await conn.commit()

        return {
            "id": novel_id,
            "title": novel_meta["title"],
            "author": novel_meta.get("author"),
            "total_chapters": novel_meta.get("total_chapters", len(chapters)),
            "total_words": novel_meta.get("total_words", 0),
            "chapters_imported": len(chapters),
            "facts_imported": sum(1 for f in facts if f.get("chapter_num") is not None),
            "has_user_state": user_state is not None,
            "existing_overwritten": existing is not None and overwrite,
        }
    finally:
        await conn.close()


def preview_import(data: dict) -> dict:
    """Return a preview of what would be imported, without touching the DB."""
    if data.get("format_version") != 1:
        raise ValueError("Unsupported export format version")

    novel = data.get("novel", {})
    chapters = data.get("chapters", [])
    facts = data.get("chapter_facts", [])

    total_words = sum(ch.get("word_count", 0) for ch in chapters)
    analyzed_count = sum(1 for ch in chapters if ch.get("analysis_status") == "completed")
    data_size = len(json.dumps(data, ensure_ascii=False))

    return {
        "title": novel.get("title", "Unknown"),
        "author": novel.get("author"),
        "total_chapters": len(chapters),
        "total_words": total_words,
        "analyzed_chapters": analyzed_count,
        "facts_count": len(facts),
        "has_user_state": data.get("user_state") is not None,
        "data_size_bytes": data_size,
    }
