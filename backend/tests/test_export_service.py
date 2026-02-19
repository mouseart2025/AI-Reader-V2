"""Tests for export_service v2 format (export, import, preview)."""

import json

import pytest
import pytest_asyncio

from src.services.export_service import (
    CURRENT_FORMAT_VERSION,
    export_novel,
    import_novel,
    preview_import,
)

NOVEL_ID = "test-novel-001"


@pytest_asyncio.fixture
async def seeded_db(mock_get_connection):
    """Insert a sample novel with chapters, facts, entity dict, and world structure."""
    db = mock_get_connection

    # Novel
    await db.execute(
        "INSERT INTO novels (id, title, author, total_chapters, total_words, prescan_status) VALUES (?, ?, ?, ?, ?, ?)",
        (NOVEL_ID, "测试小说", "测试作者", 2, 5000, "completed"),
    )

    # Chapters
    await db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content, word_count, analysis_status, is_excluded) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (NOVEL_ID, 1, "第一回", "章节一内容...", 2500, "completed", 0),
    )
    await db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content, word_count, analysis_status, is_excluded) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (NOVEL_ID, 2, "第二回", "章节二内容...", 2500, "completed", 1),
    )

    # Chapter facts
    ch1_id = (await (await db.execute("SELECT id FROM chapters WHERE novel_id=? AND chapter_num=1", (NOVEL_ID,))).fetchone())[0]
    ch2_id = (await (await db.execute("SELECT id FROM chapters WHERE novel_id=? AND chapter_num=2", (NOVEL_ID,))).fetchone())[0]
    fact1 = json.dumps({"characters": [{"name": "孙悟空"}]})
    fact2 = json.dumps({"characters": [{"name": "唐僧"}]})
    await db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json, llm_model) VALUES (?, ?, ?, ?)",
        (NOVEL_ID, ch1_id, fact1, "qwen3:14b"),
    )
    await db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json, llm_model) VALUES (?, ?, ?, ?)",
        (NOVEL_ID, ch2_id, fact2, "qwen3:14b"),
    )

    # Entity dictionary
    await db.execute(
        "INSERT INTO entity_dictionary (novel_id, name, entity_type, frequency, confidence, aliases, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (NOVEL_ID, "孙悟空", "person", 120, "high", '["齐天大圣","猴王"]', "prescan"),
    )
    await db.execute(
        "INSERT INTO entity_dictionary (novel_id, name, entity_type, frequency, confidence, aliases, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (NOVEL_ID, "花果山", "location", 45, "high", '["水帘洞"]', "prescan"),
    )

    # World structures
    ws = json.dumps({"layers": [{"id": "main", "regions": []}]})
    await db.execute(
        "INSERT INTO world_structures (novel_id, structure_json, source_chapters) VALUES (?, ?, ?)",
        (NOVEL_ID, ws, "[1,2]"),
    )

    await db.commit()
    return db


# ─── Export Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_v2_format_version(seeded_db):
    result = await export_novel(NOVEL_ID)
    assert result["format_version"] == CURRENT_FORMAT_VERSION
    assert result["format_version"] == 2


@pytest.mark.asyncio
async def test_export_v2_novel_has_prescan_status(seeded_db):
    result = await export_novel(NOVEL_ID)
    assert result["novel"]["prescan_status"] == "completed"


@pytest.mark.asyncio
async def test_export_v2_chapters_have_is_excluded(seeded_db):
    result = await export_novel(NOVEL_ID)
    chapters = result["chapters"]
    assert len(chapters) == 2
    assert chapters[0]["is_excluded"] == 0
    assert chapters[1]["is_excluded"] == 1


@pytest.mark.asyncio
async def test_export_v2_has_entity_dictionary(seeded_db):
    result = await export_novel(NOVEL_ID)
    ed = result["entity_dictionary"]
    assert len(ed) == 2
    # Sorted by frequency DESC
    assert ed[0]["name"] == "孙悟空"
    assert ed[0]["entity_type"] == "person"
    assert ed[0]["frequency"] == 120
    assert ed[0]["aliases"] == '["齐天大圣","猴王"]'
    assert ed[1]["name"] == "花果山"


@pytest.mark.asyncio
async def test_export_v2_has_world_structures(seeded_db):
    result = await export_novel(NOVEL_ID)
    ws = result["world_structures"]
    assert ws is not None
    assert "structure_json" in ws
    assert ws["source_chapters"] == "[1,2]"


@pytest.mark.asyncio
async def test_export_v2_chapter_facts(seeded_db):
    result = await export_novel(NOVEL_ID)
    assert len(result["chapter_facts"]) == 2


@pytest.mark.asyncio
async def test_export_skip_content(seeded_db):
    result = await export_novel(NOVEL_ID, skip_content=True)
    for ch in result["chapters"]:
        assert "content" not in ch
    # Other fields still present
    assert result["chapters"][0]["title"] == "第一回"


@pytest.mark.asyncio
async def test_export_with_content(seeded_db):
    result = await export_novel(NOVEL_ID, skip_content=False)
    assert result["chapters"][0]["content"] == "章节一内容..."


# ─── Import Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_v2_full(seeded_db):
    """Export then re-import should round-trip all v2 data."""
    exported = await export_novel(NOVEL_ID)

    # Delete the original novel
    await seeded_db.execute("DELETE FROM novels WHERE id = ?", (NOVEL_ID,))
    await seeded_db.commit()

    result = await import_novel(exported)
    assert result["chapters_imported"] == 2
    assert result["facts_imported"] == 2
    assert result["entity_dict_imported"] == 2
    assert result["has_world_structures"] is True

    new_id = result["id"]
    assert new_id != NOVEL_ID  # New UUID

    # Verify prescan_status persisted
    cur = await seeded_db.execute("SELECT prescan_status FROM novels WHERE id = ?", (new_id,))
    row = await cur.fetchone()
    assert row["prescan_status"] == "completed"

    # Verify is_excluded persisted
    cur = await seeded_db.execute(
        "SELECT is_excluded FROM chapters WHERE novel_id = ? ORDER BY chapter_num", (new_id,)
    )
    rows = await cur.fetchall()
    assert rows[0]["is_excluded"] == 0
    assert rows[1]["is_excluded"] == 1

    # Verify entity dictionary
    cur = await seeded_db.execute(
        "SELECT name, entity_type, frequency FROM entity_dictionary WHERE novel_id = ? ORDER BY frequency DESC", (new_id,)
    )
    entities = await cur.fetchall()
    assert len(entities) == 2
    assert entities[0]["name"] == "孙悟空"

    # Verify world structures
    cur = await seeded_db.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id = ?", (new_id,)
    )
    ws = await cur.fetchone()
    assert ws is not None


@pytest.mark.asyncio
async def test_import_v1_backward_compatible(mock_get_connection):
    """v1 format files import without error (no entity_dict or world_struct)."""
    v1_data = {
        "format_version": 1,
        "novel": {"title": "V1小说", "author": "作者", "total_chapters": 1, "total_words": 100},
        "chapters": [
            {"chapter_num": 1, "title": "第一回", "content": "内容", "word_count": 100, "analysis_status": "pending"}
        ],
        "chapter_facts": [],
        "user_state": None,
    }
    result = await import_novel(v1_data)
    assert result["chapters_imported"] == 1
    assert result["entity_dict_imported"] == 0
    assert result["has_world_structures"] is False


@pytest.mark.asyncio
async def test_import_unsupported_version(mock_get_connection):
    with pytest.raises(ValueError, match="Unsupported"):
        await import_novel({"format_version": 99})


# ─── Preview Tests ──────────────────────────────────────────────


def test_preview_v2():
    data = {
        "format_version": 2,
        "novel": {"title": "预览测试"},
        "chapters": [
            {"chapter_num": 1, "title": "Ch1", "word_count": 1000, "analysis_status": "completed"},
            {"chapter_num": 2, "title": "Ch2", "word_count": 2000, "analysis_status": "pending"},
        ],
        "chapter_facts": [{"chapter_num": 1, "fact_json": "{}"}],
        "entity_dictionary": [
            {"name": "人物A", "entity_type": "person"},
            {"name": "地点B", "entity_type": "location"},
        ],
        "world_structures": {"structure_json": "{}", "source_chapters": "[]"},
        "user_state": None,
    }
    result = preview_import(data)
    assert result["format_version"] == 2
    assert result["total_chapters"] == 2
    assert result["total_words"] == 3000
    assert result["analyzed_chapters"] == 1
    assert result["facts_count"] == 1
    assert result["entity_dict_count"] == 2
    assert result["has_world_structures"] is True


def test_preview_v1_compatible():
    data = {
        "format_version": 1,
        "novel": {"title": "V1预览"},
        "chapters": [],
        "chapter_facts": [],
        "user_state": None,
    }
    result = preview_import(data)
    assert result["format_version"] == 1
    assert result["entity_dict_count"] == 0
    assert result["has_world_structures"] is False


def test_preview_unsupported_version():
    with pytest.raises(ValueError, match="Unsupported"):
        preview_import({"format_version": 99})
