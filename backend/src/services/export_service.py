"""Export / import novel data as JSON for migration between machines."""

import json
import uuid

from src.db.sqlite_db import get_connection

CURRENT_FORMAT_VERSION = 2
SUPPORTED_FORMAT_VERSIONS = {1, 2}


async def export_novel(novel_id: str, *, skip_content: bool = False) -> dict:
    """Export a novel with all associated data.

    Format v2 includes: novel, chapters, chapter_facts, user_state,
    entity_dictionary, world_structures.

    Args:
        novel_id: The novel to export.
        skip_content: If True, omit chapters[].content to reduce size.
    """
    conn = await get_connection()
    try:
        # Novel metadata (v2: includes prescan_status)
        cur = await conn.execute(
            "SELECT id, title, author, file_hash, total_chapters, total_words, prescan_status, created_at, updated_at FROM novels WHERE id = ?",
            (novel_id,),
        )
        novel_row = await cur.fetchone()
        if not novel_row:
            raise ValueError(f"Novel {novel_id} not found")
        novel = dict(novel_row)

        # Chapters (v2: includes is_excluded)
        chapter_cols = "chapter_num, volume_num, volume_title, title, word_count, analysis_status, analyzed_at, is_excluded"
        if not skip_content:
            chapter_cols = "chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at, is_excluded"
        cur = await conn.execute(
            f"SELECT {chapter_cols} FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
            (novel_id,),
        )
        chapters = [dict(r) for r in await cur.fetchall()]

        # Chapter facts
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

        # v2: Entity dictionary
        cur = await conn.execute(
            "SELECT name, entity_type, frequency, confidence, aliases, source, sample_context FROM entity_dictionary WHERE novel_id = ? ORDER BY frequency DESC",
            (novel_id,),
        )
        entity_dictionary = [dict(r) for r in await cur.fetchall()]

        # v2: World structures
        cur = await conn.execute(
            "SELECT structure_json, source_chapters FROM world_structures WHERE novel_id = ?",
            (novel_id,),
        )
        ws_row = await cur.fetchone()
        world_structures = dict(ws_row) if ws_row else None

        return {
            "format_version": CURRENT_FORMAT_VERSION,
            "novel": novel,
            "chapters": chapters,
            "chapter_facts": facts,
            "user_state": user_state,
            "entity_dictionary": entity_dictionary,
            "world_structures": world_structures,
        }
    finally:
        await conn.close()


async def import_novel(data: dict, overwrite: bool = False) -> dict:
    """Import a novel from exported JSON data (supports v1 and v2).

    If overwrite=True and a novel with the same title exists, replace it.
    Otherwise create with a fresh ID.
    """
    version = data.get("format_version")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(f"Unsupported export format version: {version}")

    novel_meta = data["novel"]
    chapters = data.get("chapters", [])
    facts = data.get("chapter_facts", [])
    user_state = data.get("user_state")
    entity_dict = data.get("entity_dictionary", [])
    world_struct = data.get("world_structures")

    conn = await get_connection()
    try:
        # Check for existing novel by title
        cur = await conn.execute(
            "SELECT id FROM novels WHERE title = ?", (novel_meta["title"],)
        )
        existing = await cur.fetchone()

        if existing and overwrite:
            await conn.execute("DELETE FROM novels WHERE id = ?", (existing["id"],))

        novel_id = str(uuid.uuid4())

        # Insert novel (v2: includes prescan_status)
        prescan_status = novel_meta.get("prescan_status", "pending")
        await conn.execute(
            "INSERT INTO novels (id, title, author, file_hash, total_chapters, total_words, prescan_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                novel_id,
                novel_meta["title"],
                novel_meta.get("author"),
                novel_meta.get("file_hash"),
                novel_meta.get("total_chapters", len(chapters)),
                novel_meta.get("total_words", 0),
                prescan_status,
                novel_meta.get("created_at"),
                novel_meta.get("updated_at"),
            ),
        )

        # Insert chapters (v2: includes is_excluded)
        if chapters:
            await conn.executemany(
                "INSERT INTO chapters (novel_id, chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at, is_excluded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        ch["chapter_num"],
                        ch.get("volume_num"),
                        ch.get("volume_title"),
                        ch["title"],
                        ch.get("content", ""),
                        ch.get("word_count", 0),
                        ch.get("analysis_status", "pending"),
                        ch.get("analyzed_at"),
                        ch.get("is_excluded", 0),
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

        # v2: Import entity dictionary
        if entity_dict:
            await conn.executemany(
                "INSERT INTO entity_dictionary (novel_id, name, entity_type, frequency, confidence, aliases, source, sample_context) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        e["name"],
                        e.get("entity_type"),
                        e.get("frequency", 0),
                        e.get("confidence", "medium"),
                        e.get("aliases", "[]"),
                        e.get("source", "import"),
                        e.get("sample_context"),
                    )
                    for e in entity_dict
                ],
            )

        # v2: Import world structures
        if world_struct:
            await conn.execute(
                "INSERT INTO world_structures (novel_id, structure_json, source_chapters) VALUES (?, ?, ?)",
                (
                    novel_id,
                    world_struct["structure_json"],
                    world_struct.get("source_chapters", "[]"),
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
            "entity_dict_imported": len(entity_dict),
            "has_world_structures": world_struct is not None,
            "existing_overwritten": existing is not None and overwrite,
        }
    finally:
        await conn.close()


def preview_import(data: dict) -> dict:
    """Return a preview of what would be imported, without touching the DB."""
    version = data.get("format_version")
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise ValueError(f"Unsupported export format version: {version}")

    novel = data.get("novel", {})
    chapters = data.get("chapters", [])
    facts = data.get("chapter_facts", [])
    entity_dict = data.get("entity_dictionary", [])
    world_struct = data.get("world_structures")

    total_words = sum(ch.get("word_count", 0) for ch in chapters)
    analyzed_count = sum(1 for ch in chapters if ch.get("analysis_status") == "completed")
    data_size = len(json.dumps(data, ensure_ascii=False))

    return {
        "format_version": version,
        "title": novel.get("title", "Unknown"),
        "author": novel.get("author"),
        "total_chapters": len(chapters),
        "total_words": total_words,
        "analyzed_chapters": analyzed_count,
        "facts_count": len(facts),
        "has_user_state": data.get("user_state") is not None,
        "entity_dict_count": len(entity_dict),
        "has_world_structures": world_struct is not None,
        "data_size_bytes": data_size,
    }
