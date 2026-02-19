"""Tests for sample_data_service: auto-import on first launch."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from src.services.sample_data_service import auto_import_samples, _restore_chapter_content


# Minimal v2 JSON sample for testing
def _make_sample_json(title="测试小说", author="测试作者", num_chapters=2):
    chapters = [
        {
            "chapter_num": i + 1,
            "title": f"第{i + 1}章",
            "word_count": 100,
            "analysis_status": "completed",
            "analyzed_at": "2026-01-01",
            "is_excluded": 0,
        }
        for i in range(num_chapters)
    ]
    return {
        "format_version": 2,
        "novel": {
            "id": "orig-id",
            "title": title,
            "author": author,
            "file_hash": "abc123",
            "total_chapters": num_chapters,
            "total_words": num_chapters * 100,
            "prescan_status": "completed",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        },
        "chapters": chapters,
        "chapter_facts": [],
        "user_state": None,
        "entity_dictionary": [],
        "world_structures": None,
    }


@pytest.mark.asyncio
async def test_auto_import_when_db_empty(mock_get_connection, tmp_path):
    """First launch: empty DB → imports sample novels and marks is_sample=1."""
    db = mock_get_connection

    # Prepare fake sample files
    json_dir = tmp_path / "sample-data"
    json_dir.mkdir()
    txt_dir = tmp_path / "sample-novels"
    txt_dir.mkdir()

    sample_data = _make_sample_json("西游记样本", "吴承恩", 2)
    (json_dir / "xiyouji.json").write_text(json.dumps(sample_data), encoding="utf-8")
    (txt_dir / "xiyouji.txt").write_text(
        "第一章 开始\n这是第一章内容。\n\n第二章 继续\n这是第二章内容。\n",
        encoding="utf-8",
    )

    sample_data2 = _make_sample_json("三国演义样本", "罗贯中", 2)
    (json_dir / "sanguoyanyi.json").write_text(json.dumps(sample_data2), encoding="utf-8")
    (txt_dir / "sanguoyanyi.txt").write_text(
        "第一章 桃园\n桃园结义。\n\n第二章 董卓\n董卓进京。\n",
        encoding="utf-8",
    )

    with patch("src.services.sample_data_service._JSON_DIR", json_dir), \
         patch("src.services.sample_data_service._TXT_DIR", txt_dir):
        await auto_import_samples()

    # Verify novels were imported
    cursor = await db.execute("SELECT id, title, is_sample FROM novels ORDER BY title")
    rows = await cursor.fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["is_sample"] == 1


@pytest.mark.asyncio
async def test_auto_import_skips_when_db_nonempty(mock_get_connection, tmp_path):
    """Non-first launch: DB has novels → skips import."""
    db = mock_get_connection

    # Insert an existing novel
    await db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES (?, ?, ?)",
        ("existing-1", "已有小说", 5),
    )
    await db.commit()

    json_dir = tmp_path / "sample-data"
    json_dir.mkdir()
    txt_dir = tmp_path / "sample-novels"
    txt_dir.mkdir()

    sample_data = _make_sample_json("西游记样本")
    (json_dir / "xiyouji.json").write_text(json.dumps(sample_data), encoding="utf-8")

    with patch("src.services.sample_data_service._JSON_DIR", json_dir), \
         patch("src.services.sample_data_service._TXT_DIR", txt_dir):
        await auto_import_samples()

    # Only the original novel exists
    cursor = await db.execute("SELECT COUNT(*) FROM novels")
    row = await cursor.fetchone()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_auto_import_skips_missing_files(mock_get_connection, tmp_path):
    """Missing sample files → silently skips without error."""
    json_dir = tmp_path / "sample-data"
    json_dir.mkdir()
    txt_dir = tmp_path / "sample-novels"
    txt_dir.mkdir()

    with patch("src.services.sample_data_service._JSON_DIR", json_dir), \
         patch("src.services.sample_data_service._TXT_DIR", txt_dir):
        await auto_import_samples()  # Should not raise

    cursor = await mock_get_connection.execute("SELECT COUNT(*) FROM novels")
    row = await cursor.fetchone()
    assert row[0] == 0


@pytest.mark.asyncio
async def test_restore_chapter_content(mock_get_connection, tmp_path):
    """Chapter content is restored from TXT by chapter_num matching."""
    db = mock_get_connection

    # Insert a novel with empty-content chapters
    novel_id = "restore-test"
    await db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES (?, ?, ?)",
        (novel_id, "恢复测试", 2),
    )
    await db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content, word_count) VALUES (?, ?, ?, ?, ?)",
        (novel_id, 1, "第一章", "", 0),
    )
    await db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content, word_count) VALUES (?, ?, ?, ?, ?)",
        (novel_id, 2, "第二章", "", 0),
    )
    await db.commit()

    txt_path = tmp_path / "test.txt"
    txt_path.write_text(
        "第一章 开始\n这是第一章的正文内容，需要足够长才能被章节切分器识别。这里写一些额外的文字来确保检测。\n\n"
        "第二章 继续\n这是第二章的正文内容，同样需要足够长的内容来确保章节切分器能够正确分割。\n",
        encoding="utf-8",
    )

    await _restore_chapter_content(novel_id, txt_path, expected_chapters=2)

    # Verify content was restored
    cursor = await db.execute(
        "SELECT chapter_num, content FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
        (novel_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 2
    assert len(rows[0]["content"]) > 0
    assert len(rows[1]["content"]) > 0
