"""Auto-import sample novels on startup.

Called from lifespan in main.py after init_db(). Scans for .air files in
resource directories and imports any that aren't already in the database
(checked by file_hash + is_sample flag).

Supports two scan paths:
  1. Tauri resource dir: $AI_READER_RESOURCE_DIR/novels/ (desktop build)
  2. Fallback: frontend/public/sample-data/ (Web/dev mode)

Also supports legacy JSON + TXT format for backward compatibility.
"""

import gzip
import json
import logging
import os
import uuid
from pathlib import Path

from src.db.sqlite_db import get_connection
from src.services.export_service import import_novel
from src.utils.chapter_splitter import split_chapters_ex

logger = logging.getLogger(__name__)

# Path resolution from this file
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
_PROJECT_DIR = _BACKEND_DIR.parent

# Scan directories (in priority order)
_RESOURCE_NOVELS_DIR = _PROJECT_DIR / "frontend" / "src-tauri" / "resources" / "novels"
_SAMPLE_DATA_DIR = _PROJECT_DIR / "frontend" / "public" / "sample-data"
_TXT_DIR = _BACKEND_DIR / "sample-novels"

# Legacy sample definitions: (json_filename, txt_filename)
_LEGACY_SAMPLES = [
    ("xiyouji.json", "xiyouji.txt"),
    ("sanguoyanyi.json", "sanguoyanyi.txt"),
]


def _get_scan_dirs() -> list[Path]:
    """Return list of directories to scan for .air files, in priority order."""
    dirs: list[Path] = []

    # 1. Environment variable override (Tauri sets this at runtime)
    env_dir = os.environ.get("AI_READER_RESOURCE_DIR")
    if env_dir:
        p = Path(env_dir) / "novels"
        if p.is_dir():
            dirs.append(p)

    # 2. Tauri resources dir (dev/local builds)
    if _RESOURCE_NOVELS_DIR.is_dir():
        dirs.append(_RESOURCE_NOVELS_DIR)

    # 3. Fallback: frontend/public/sample-data
    if _SAMPLE_DATA_DIR.is_dir():
        dirs.append(_SAMPLE_DATA_DIR)

    return dirs


async def _is_sample_imported(file_hash: str | None, title: str) -> bool:
    """Check if a sample novel with this file_hash or title already exists."""
    conn = await get_connection()
    try:
        if file_hash:
            cur = await conn.execute(
                "SELECT id FROM novels WHERE (file_hash = ? OR title = ?) AND is_sample = 1",
                (file_hash, title),
            )
        else:
            cur = await conn.execute(
                "SELECT id FROM novels WHERE title = ? AND is_sample = 1",
                (title,),
            )
        return await cur.fetchone() is not None
    finally:
        await conn.close()


async def _import_air_file(air_path: Path) -> None:
    """Import a single .air file as a sample novel."""
    raw = air_path.read_bytes()

    # Decode: .air is gzip-compressed JSON
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw)

    # Extract metadata to check for duplicates
    novel_meta = data.get("novel", {})
    file_hash = novel_meta.get("file_hash")
    title = novel_meta.get("title", air_path.stem)

    if await _is_sample_imported(file_hash, title):
        logger.debug("样本已存在，跳过: %s", title)
        return

    # Import via export_service
    result = await import_novel(data)
    novel_id = result["id"]
    logger.info("导入样本 .air: %s (id=%s)", title, novel_id)

    # Mark as sample + create completed analysis_tasks record
    total_chapters = result.get("total_chapters", novel_meta.get("total_chapters", 0))
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


async def _import_legacy_sample(json_file: str, txt_file: str) -> None:
    """Import a legacy JSON + TXT sample (backward compatibility)."""
    json_path = _SAMPLE_DATA_DIR / json_file
    txt_path = _TXT_DIR / txt_file

    if not json_path.exists():
        logger.debug("样本 JSON 不存在: %s，跳过", json_path)
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    novel_meta = data.get("novel", {})
    file_hash = novel_meta.get("file_hash")
    title = novel_meta.get("title", json_file)

    if await _is_sample_imported(file_hash, title):
        logger.debug("样本已存在，跳过: %s", title)
        return

    result = await import_novel(data)
    novel_id = result["id"]
    title = result["title"]
    logger.info("导入样本 (legacy): %s (id=%s)", title, novel_id)

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


async def auto_import_samples() -> None:
    """Import sample novels. Checks per-file, imports any missing samples.

    Scans for .air files first (preferred), then falls back to legacy JSON+TXT.
    Already-imported samples are detected by file_hash + is_sample flag.
    """
    air_files_found = False

    # Phase 1: Scan for .air files
    for scan_dir in _get_scan_dirs():
        for air_path in sorted(scan_dir.glob("*.air")):
            air_files_found = True
            try:
                await _import_air_file(air_path)
            except Exception:
                logger.exception("导入样本 %s 失败", air_path.name)

    # Phase 2: Legacy JSON + TXT fallback (only if no .air files were found)
    if not air_files_found:
        for json_file, txt_file in _LEGACY_SAMPLES:
            try:
                await _import_legacy_sample(json_file, txt_file)
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
