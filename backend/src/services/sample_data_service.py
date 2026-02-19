"""Auto-import sample novels on first launch (empty database).

Called from lifespan in main.py after init_db(). If the novels table is empty,
imports pre-analyzed JSON data packages and restores chapter content from TXT files.
"""

import json
import logging
import uuid
from pathlib import Path

from src.db.sqlite_db import get_connection
from src.services.export_service import import_novel
from src.utils.chapter_splitter import split_chapters_ex

logger = logging.getLogger(__name__)

# Sample data definitions: (json_filename, txt_filename)
_SAMPLES = [
    ("xiyouji.json", "xiyouji.txt"),
    ("sanguoyanyi.json", "sanguoyanyi.txt"),
]

# Path resolution from this file
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
_JSON_DIR = _BACKEND_DIR.parent / "frontend" / "public" / "sample-data"
_TXT_DIR = _BACKEND_DIR / "sample-novels"


async def auto_import_samples() -> None:
    """Import sample novels if the database is empty.

    Silently skips if sample files are missing (e.g., production environment).
    """
    # Check if DB has any novels
    conn = await get_connection()
    try:
        cursor = await conn.execute("SELECT COUNT(*) FROM novels")
        row = await cursor.fetchone()
        count = row[0] if row else 0
    finally:
        await conn.close()

    if count > 0:
        logger.debug("数据库非空 (%d 本小说)，跳过样本导入", count)
        return

    logger.info("首次启动，开始导入样本小说...")

    for json_file, txt_file in _SAMPLES:
        json_path = _JSON_DIR / json_file
        txt_path = _TXT_DIR / txt_file

        if not json_path.exists():
            logger.warning("样本文件不存在: %s，跳过", json_path)
            continue

        try:
            # Load JSON data package
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Import via existing service
            result = await import_novel(data)
            novel_id = result["id"]
            title = result["title"]
            logger.info("导入样本: %s (id=%s)", title, novel_id)

            # Mark as sample + create completed analysis_tasks record
            total_chapters = data.get("novel", {}).get("total_chapters", len(data.get("chapters", [])))
            conn = await get_connection()
            try:
                await conn.execute(
                    "UPDATE novels SET is_sample = 1 WHERE id = ?", (novel_id,)
                )
                await conn.execute(
                    """INSERT INTO analysis_tasks (id, novel_id, status, chapter_start, chapter_end, current_chapter)
                       VALUES (?, ?, 'completed', 1, ?, ?)""",
                    (str(uuid.uuid4()), novel_id, total_chapters, total_chapters),
                )
                await conn.commit()
            finally:
                await conn.close()

            # Restore chapter content from TXT
            if txt_path.exists():
                await _restore_chapter_content(novel_id, txt_path, len(data.get("chapters", [])))
            else:
                logger.warning("TXT 文件不存在: %s，章节内容未恢复", txt_path)

        except Exception:
            logger.exception("导入样本 %s 失败", json_file)


async def _restore_chapter_content(novel_id: str, txt_path: Path, expected_chapters: int) -> None:
    """Read TXT, split chapters, and UPDATE chapter content by chapter_num."""
    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read()

    result = split_chapters_ex(text)
    chapters = result.chapters[:expected_chapters]

    conn = await get_connection()
    try:
        for ch in chapters:
            await conn.execute(
                "UPDATE chapters SET content = ? WHERE novel_id = ? AND chapter_num = ?",
                (ch.content, novel_id, ch.chapter_num),
            )
        await conn.commit()
        logger.info("恢复章节内容: %d 章 (%s)", len(chapters), txt_path.name)
    finally:
        await conn.close()
