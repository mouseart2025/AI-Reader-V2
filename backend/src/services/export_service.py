"""Export / import novel data as JSON for migration between machines."""

import json
import logging
import uuid

from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)

CURRENT_FORMAT_VERSION = 5
SUPPORTED_FORMAT_VERSIONS = {1, 2, 3, 4, 5}


async def _build_precomputed(novel_id: str) -> dict | None:
    """Build precomputed visualization data for desktop .air import.

    Calls visualization_service, encyclopedia_service, etc. to generate
    pre-aggregated data so the Tauri desktop app can directly write .json.gz
    files without reimplementing Python aggregation logic in Rust.
    """
    try:
        from src.services.visualization_service import (
            get_analyzed_range,
            get_graph_data,
            get_map_data,
            get_timeline_data,
            get_factions_data,
        )
        from src.services.encyclopedia_service import (
            get_category_stats,
            get_encyclopedia_entries,
        )
        from src.db.world_structure_store import load_with_overrides

        ch_start, ch_end = await get_analyzed_range(novel_id)
        if ch_start == 0 and ch_end == 0:
            logger.warning("Novel %s has no analyzed chapters, skipping precomputed", novel_id)
            return None

        graph = await get_graph_data(novel_id, ch_start, ch_end)
        map_data = await get_map_data(novel_id, ch_start, ch_end)
        timeline = await get_timeline_data(novel_id, ch_start, ch_end)
        factions = await get_factions_data(novel_id, ch_start, ch_end)

        encyclopedia = await get_encyclopedia_entries(novel_id)
        encyclopedia_stats = await get_category_stats(novel_id)

        ws = await load_with_overrides(novel_id)
        world_structure = ws.model_dump() if ws else None

        return {
            "graph": graph,
            "map": map_data,
            "timeline": timeline,
            "encyclopedia": encyclopedia,
            "encyclopedia_stats": encyclopedia_stats,
            "factions": factions,
            "world_structure": world_structure,
        }
    except Exception as e:
        logger.error("Failed to build precomputed data for %s: %s", novel_id, e)
        raise RuntimeError(f"生成预计算数据失败: {e}") from e


async def export_novel(novel_id: str, *, skip_content: bool = False) -> dict:
    """Export a novel with all associated data.

    Format v5 adds: scenes_json, cost/quality columns, map_layouts,
    layer_layouts, conversations + messages.
    """
    conn = await get_connection()
    try:
        # Novel metadata
        cur = await conn.execute(
            "SELECT id, title, author, file_hash, total_chapters, total_words, prescan_status, synopsis, created_at, updated_at FROM novels WHERE id = ?",
            (novel_id,),
        )
        novel_row = await cur.fetchone()
        if not novel_row:
            raise ValueError(f"Novel {novel_id} not found")
        novel = dict(novel_row)

        # Chapters (all columns including error tracking)
        chapter_cols = "chapter_num, volume_num, volume_title, title, word_count, analysis_status, analyzed_at, is_excluded, analysis_error, error_type"
        if not skip_content:
            chapter_cols = "chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at, is_excluded, analysis_error, error_type"
        cur = await conn.execute(
            f"SELECT {chapter_cols} FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
            (novel_id,),
        )
        chapters = [dict(r) for r in await cur.fetchall()]

        # Chapter facts (all columns: scenes, cost, quality)
        cur = await conn.execute(
            """SELECT cf.chapter_id, c.chapter_num, cf.fact_json, cf.scenes_json,
                      cf.llm_model, cf.extracted_at, cf.extraction_ms,
                      cf.input_tokens, cf.output_tokens, cf.cost_usd, cf.cost_cny,
                      cf.is_truncated, cf.segment_count
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

        # Entity dictionary (filter out noise 'unknown' type)
        cur = await conn.execute(
            "SELECT name, entity_type, frequency, confidence, aliases, source, sample_context FROM entity_dictionary WHERE novel_id = ? AND entity_type != 'unknown' ORDER BY frequency DESC",
            (novel_id,),
        )
        entity_dictionary = [dict(r) for r in await cur.fetchall()]

        # World structures
        cur = await conn.execute(
            "SELECT structure_json, source_chapters FROM world_structures WHERE novel_id = ?",
            (novel_id,),
        )
        ws_row = await cur.fetchone()
        world_structures = dict(ws_row) if ws_row else None

        # Bookmarks
        cur = await conn.execute(
            "SELECT chapter_num, scroll_position, note, created_at FROM bookmarks WHERE novel_id = ? ORDER BY created_at",
            (novel_id,),
        )
        bookmarks = [dict(r) for r in await cur.fetchall()]

        # Map user overrides
        cur = await conn.execute(
            "SELECT location_name, x, y, lat, lng, constraint_type, locked_parent, updated_at FROM map_user_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        map_user_overrides = [dict(r) for r in await cur.fetchall()]

        # World structure overrides
        cur = await conn.execute(
            "SELECT override_type, override_key, override_json, created_at FROM world_structure_overrides WHERE novel_id = ?",
            (novel_id,),
        )
        ws_overrides = [dict(r) for r in await cur.fetchall()]

        # v5: Map layouts (computed layout cache + quality baseline)
        cur = await conn.execute(
            "SELECT chapter_hash, layout_json, layout_mode, terrain_path, satisfaction_json, created_at FROM map_layouts WHERE novel_id = ?",
            (novel_id,),
        )
        map_layouts = [dict(r) for r in await cur.fetchall()]

        # v5: Layer layouts (multi-layer visualization positions)
        cur = await conn.execute(
            "SELECT layer_id, chapter_hash, layout_json, layout_mode, terrain_path, created_at FROM layer_layouts WHERE novel_id = ?",
            (novel_id,),
        )
        layer_layouts = [dict(r) for r in await cur.fetchall()]

        # v5: Conversations + messages (Q&A chat history)
        cur = await conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE novel_id = ? ORDER BY created_at",
            (novel_id,),
        )
        conversations = [dict(r) for r in await cur.fetchall()]

        messages = []
        if conversations:
            conv_ids = [c["id"] for c in conversations]
            placeholders = ",".join("?" * len(conv_ids))
            cur = await conn.execute(
                f"SELECT conversation_id, role, content, sources_json, created_at FROM messages WHERE conversation_id IN ({placeholders}) ORDER BY created_at",
                conv_ids,
            )
            messages = [dict(r) for r in await cur.fetchall()]

        # v4: Build precomputed visualization data for desktop import
        precomputed = await _build_precomputed(novel_id)

        result = {
            "format_version": CURRENT_FORMAT_VERSION,
            "novel": novel,
            "chapters": chapters,
            "chapter_facts": facts,
            "user_state": user_state,
            "entity_dictionary": entity_dictionary,
            "world_structures": world_structures,
            "bookmarks": bookmarks,
            "map_user_overrides": map_user_overrides,
            "world_structure_overrides": ws_overrides,
            "map_layouts": map_layouts,
            "layer_layouts": layer_layouts,
            "conversations": conversations,
            "messages": messages,
        }
        if precomputed:
            result["precomputed"] = precomputed
        return result
    finally:
        await conn.close()


async def import_novel(data: dict, overwrite: bool = False) -> dict:
    """Import a novel from exported JSON data (supports v1-v5).

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
    bookmarks = data.get("bookmarks", [])
    map_overrides = data.get("map_user_overrides", [])
    ws_overrides = data.get("world_structure_overrides", [])
    map_layouts = data.get("map_layouts", [])
    layer_layouts = data.get("layer_layouts", [])
    conversations = data.get("conversations", [])
    messages = data.get("messages", [])

    conn = await get_connection()
    try:
        # Check for existing novel by title or file_hash
        file_hash = novel_meta.get("file_hash")
        if file_hash:
            cur = await conn.execute(
                "SELECT id FROM novels WHERE title = ? OR file_hash = ?",
                (novel_meta["title"], file_hash),
            )
        else:
            cur = await conn.execute(
                "SELECT id FROM novels WHERE title = ?", (novel_meta["title"],)
            )
        existing = await cur.fetchone()

        if existing and overwrite:
            await conn.execute("DELETE FROM novels WHERE id = ?", (existing["id"],))

        novel_id = str(uuid.uuid4())

        # Insert novel
        prescan_status = novel_meta.get("prescan_status", "pending")
        await conn.execute(
            "INSERT INTO novels (id, title, author, file_hash, total_chapters, total_words, prescan_status, synopsis, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                novel_id,
                novel_meta["title"],
                novel_meta.get("author"),
                novel_meta.get("file_hash"),
                novel_meta.get("total_chapters", len(chapters)),
                novel_meta.get("total_words", 0),
                prescan_status,
                novel_meta.get("synopsis"),
                novel_meta.get("created_at"),
                novel_meta.get("updated_at"),
            ),
        )

        # Insert chapters (all columns including error tracking)
        if chapters:
            await conn.executemany(
                "INSERT INTO chapters (novel_id, chapter_num, volume_num, volume_title, title, content, word_count, analysis_status, analyzed_at, is_excluded, analysis_error, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        ch.get("analysis_error"),
                        ch.get("error_type"),
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
                        """INSERT INTO chapter_facts
                           (novel_id, chapter_id, fact_json, scenes_json, llm_model,
                            extracted_at, extraction_ms, input_tokens, output_tokens,
                            cost_usd, cost_cny, is_truncated, segment_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            novel_id,
                            ch_map[chapter_num],
                            fact["fact_json"],
                            fact.get("scenes_json"),
                            fact.get("llm_model"),
                            fact.get("extracted_at"),
                            fact.get("extraction_ms"),
                            fact.get("input_tokens"),
                            fact.get("output_tokens"),
                            fact.get("cost_usd"),
                            fact.get("cost_cny"),
                            fact.get("is_truncated", 0),
                            fact.get("segment_count", 1),
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

        # Import entity dictionary
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

        # Import world structures
        if world_struct:
            await conn.execute(
                "INSERT INTO world_structures (novel_id, structure_json, source_chapters) VALUES (?, ?, ?)",
                (
                    novel_id,
                    world_struct["structure_json"],
                    world_struct.get("source_chapters", "[]"),
                ),
            )

        # Import bookmarks
        if bookmarks:
            await conn.executemany(
                "INSERT INTO bookmarks (novel_id, chapter_num, scroll_position, note, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        b["chapter_num"],
                        b.get("scroll_position", 0),
                        b.get("note", ""),
                        b.get("created_at"),
                    )
                    for b in bookmarks
                ],
            )

        # Import map user overrides
        if map_overrides:
            await conn.executemany(
                "INSERT INTO map_user_overrides (novel_id, location_name, x, y, lat, lng, constraint_type, locked_parent, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        o["location_name"],
                        o.get("x"),
                        o.get("y"),
                        o.get("lat"),
                        o.get("lng"),
                        o.get("constraint_type", "position"),
                        o.get("locked_parent"),
                        o.get("updated_at"),
                    )
                    for o in map_overrides
                ],
            )

        # Import world structure overrides
        if ws_overrides:
            await conn.executemany(
                "INSERT INTO world_structure_overrides (novel_id, override_type, override_key, override_json, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        o["override_type"],
                        o["override_key"],
                        o["override_json"],
                        o.get("created_at"),
                    )
                    for o in ws_overrides
                ],
            )

        # v5: Import map layouts
        if map_layouts:
            await conn.executemany(
                "INSERT INTO map_layouts (novel_id, chapter_hash, layout_json, layout_mode, terrain_path, satisfaction_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        ml["chapter_hash"],
                        ml["layout_json"],
                        ml.get("layout_mode", "hierarchy"),
                        ml.get("terrain_path"),
                        ml.get("satisfaction_json"),
                        ml.get("created_at"),
                    )
                    for ml in map_layouts
                ],
            )

        # v5: Import layer layouts
        if layer_layouts:
            await conn.executemany(
                "INSERT INTO layer_layouts (novel_id, layer_id, chapter_hash, layout_json, layout_mode, terrain_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        novel_id,
                        ll["layer_id"],
                        ll["chapter_hash"],
                        ll["layout_json"],
                        ll.get("layout_mode", "hierarchy"),
                        ll.get("terrain_path"),
                        ll.get("created_at"),
                    )
                    for ll in layer_layouts
                ],
            )

        # v5: Import conversations + messages
        conv_id_map: dict[str, str] = {}
        if conversations:
            for conv in conversations:
                new_conv_id = str(uuid.uuid4())
                conv_id_map[conv["id"]] = new_conv_id
                await conn.execute(
                    "INSERT INTO conversations (id, novel_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        new_conv_id,
                        novel_id,
                        conv.get("title"),
                        conv.get("created_at"),
                        conv.get("updated_at"),
                    ),
                )

        if messages and conv_id_map:
            await conn.executemany(
                "INSERT INTO messages (conversation_id, role, content, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        conv_id_map[m["conversation_id"]],
                        m["role"],
                        m["content"],
                        m.get("sources_json"),
                        m.get("created_at"),
                    )
                    for m in messages
                    if m.get("conversation_id") in conv_id_map
                ],
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
            "bookmarks_imported": len(bookmarks),
            "map_overrides_imported": len(map_overrides),
            "ws_overrides_imported": len(ws_overrides),
            "map_layouts_imported": len(map_layouts),
            "layer_layouts_imported": len(layer_layouts),
            "conversations_imported": len(conversations),
            "messages_imported": len(messages),
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
    bookmarks = data.get("bookmarks", [])
    map_overrides = data.get("map_user_overrides", [])
    ws_overrides = data.get("world_structure_overrides", [])
    conversations = data.get("conversations", [])

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
        "bookmarks_count": len(bookmarks),
        "map_overrides_count": len(map_overrides),
        "ws_overrides_count": len(ws_overrides),
        "conversations_count": len(conversations),
        "data_size_bytes": data_size,
    }
