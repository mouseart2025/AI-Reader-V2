"""Full data backup / restore — ZIP archive with all novels and settings.

Export: SQLite → per-novel JSON + manifest → ZIP
Import: ZIP → validate → per-novel import (with conflict detection)

API Keys are NEVER included in backups.
"""

import io
import json
import zipfile
from datetime import datetime

from src.db.sqlite_db import get_connection
from src.services.export_service import export_novel, import_novel


BACKUP_FORMAT_VERSION = 1


def _get_app_version() -> str:
    """Get app version from package metadata, fallback to unknown."""
    try:
        from importlib.metadata import version
        return version("ai-reader-v2-backend")
    except Exception:
        try:
            import importlib.resources
            import tomllib
            # Fallback: read pyproject.toml
            import pathlib
            pyproject = pathlib.Path(__file__).parent.parent.parent / "pyproject.toml"
            if pyproject.exists():
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
                return data.get("project", {}).get("version", "unknown")
        except Exception:
            pass
    return "unknown"


async def export_all() -> io.BytesIO:
    """Export all novels and settings as a ZIP archive.

    Structure:
        manifest.json          — metadata + novel list
        novels/<novel_id>.json — per-novel export (reuses export_service format)
    """
    conn = await get_connection()
    try:
        cur = await conn.execute("SELECT id, title FROM novels ORDER BY title")
        novels = [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        exported_novels = []

        for novel in novels:
            try:
                data = await export_novel(novel["id"])
                zf.writestr(
                    f"novels/{novel['id']}.json",
                    json.dumps(data, ensure_ascii=False, indent=None),
                )
                exported_novels.append({
                    "id": novel["id"],
                    "title": novel["title"],
                    "total_chapters": data["novel"].get("total_chapters", 0),
                })
            except Exception:
                # Skip novels that fail to export
                pass

        manifest = {
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "exported_at": datetime.now().isoformat(),
            "app_version": _get_app_version(),
            "novel_count": len(exported_novels),
            "novels": exported_novels,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    buf.seek(0)
    return buf


async def preview_backup_import(data: bytes) -> dict:
    """Preview what a backup ZIP contains without importing.

    Returns manifest + per-novel conflict status.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile:
        raise ValueError("无效的 ZIP 文件")

    manifest_raw = zf.read("manifest.json")
    manifest = json.loads(manifest_raw)

    if manifest.get("backup_format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError(
            f"不支持的备份版本: {manifest.get('backup_format_version')}"
        )

    # Check each novel for conflicts
    conn = await get_connection()
    try:
        cur = await conn.execute("SELECT id, title FROM novels")
        existing = {row["title"]: row["id"] for row in await cur.fetchall()}
    finally:
        await conn.close()

    novels_preview = []
    for entry in manifest.get("novels", []):
        title = entry.get("title", "")
        novels_preview.append({
            "id": entry.get("id"),
            "title": title,
            "total_chapters": entry.get("total_chapters", 0),
            "conflict": title in existing,
            "existing_id": existing.get(title),
        })

    return {
        "backup_format_version": manifest.get("backup_format_version"),
        "exported_at": manifest.get("exported_at"),
        "novel_count": len(novels_preview),
        "novels": novels_preview,
        "conflict_count": sum(1 for n in novels_preview if n["conflict"]),
        "zip_size_bytes": len(data),
    }


async def import_all(
    data: bytes, conflict_mode: str = "skip"
) -> dict:
    """Import all novels from a backup ZIP.

    Args:
        data: Raw ZIP bytes
        conflict_mode: "skip" (default) or "overwrite"

    Returns summary of import results.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile:
        raise ValueError("无效的 ZIP 文件")

    manifest_raw = zf.read("manifest.json")
    manifest = json.loads(manifest_raw)

    if manifest.get("backup_format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError(
            f"不支持的备份版本: {manifest.get('backup_format_version')}"
        )

    # Get existing novels for conflict detection
    conn = await get_connection()
    try:
        cur = await conn.execute("SELECT title FROM novels")
        existing_titles = {row["title"] for row in await cur.fetchall()}
    finally:
        await conn.close()

    imported = 0
    skipped = 0
    overwritten = 0
    errors: list[str] = []

    novel_files = [n for n in zf.namelist() if n.startswith("novels/") and n.endswith(".json")]

    for path in novel_files:
        try:
            novel_data = json.loads(zf.read(path))
            title = novel_data.get("novel", {}).get("title", "")

            if title in existing_titles:
                if conflict_mode == "skip":
                    skipped += 1
                    continue
                else:
                    await import_novel(novel_data, overwrite=True)
                    overwritten += 1
            else:
                await import_novel(novel_data, overwrite=False)

            imported += 1
        except Exception as e:
            errors.append(f"{path}: {str(e)}")

    return {
        "total": len(novel_files),
        "imported": imported,
        "skipped": skipped,
        "overwritten": overwritten,
        "errors": errors,
    }
