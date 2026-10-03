"""backup.py 路由级测试:全量备份导出 / 预览 / 导入主流程与错误路径。

真实 app + httpx ASGITransport;DB 走 conftest 的 api_client fixture
(memory_db),ZIP 全部在内存构建,不触碰真实文件系统与 LLM。
"""

import io
import json
import zipfile

import pytest

from src.services.backup_service import BACKUP_FORMAT_VERSION

NOVEL = "novel-backup"


async def _seed_novel(db, novel_id=NOVEL, title="备份测试小说"):
    await db.execute(
        "INSERT INTO novels (id, title, total_chapters, total_words)"
        " VALUES (?, ?, 1, 100)",
        (novel_id, title),
    )
    await db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
        " analysis_status) VALUES (?, ?, 1, '第1章', '正文内容', 'completed')",
        (1, novel_id),
    )
    await db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
        " VALUES (?, 1, ?)",
        (novel_id, json.dumps({"characters": [{"name": "宋江"}]})),
    )
    await db.commit()


def _novel_export_payload(
    title="备份测试小说", novel_id=NOVEL, file_hash=None,
):
    return {
        "format_version": 6,
        "novel": {
            "id": novel_id, "title": title,
            "total_chapters": 1, "total_words": 100, "file_hash": file_hash,
        },
        "chapters": [
            {
                "chapter_num": 1, "title": "第1章", "content": "正文内容",
                "word_count": 100, "analysis_status": "completed",
            },
        ],
        "chapter_facts": [
            {"chapter_num": 1, "fact_json": json.dumps({"characters": []})},
        ],
    }


def _make_backup_zip(payloads, version=BACKUP_FORMAT_VERSION):
    """按 backup_service 的 ZIP 布局在内存构建备份包。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "backup_format_version": version,
            "exported_at": "2026-01-01T00:00:00",
            "app_version": "test",
            "novel_count": len(payloads),
            "novels": [
                {
                    "id": p["novel"]["id"],
                    "title": p["novel"]["title"],
                    "total_chapters": p["novel"].get("total_chapters", 0),
                }
                for p in payloads
            ],
        }, ensure_ascii=False))
        for p in payloads:
            zf.writestr(
                f"novels/{p['novel']['id']}.json",
                json.dumps(p, ensure_ascii=False),
            )
    return buf.getvalue()


def _upload(data, name="backup.zip"):
    return {"file": (name, data, "application/zip")}


# ── GET /api/backup/export ──


@pytest.mark.asyncio
async def test_export_backup_happy(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get("/api/backup/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "ai-reader-v2-backup-" in resp.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["backup_format_version"] == BACKUP_FORMAT_VERSION
    assert manifest["novel_count"] == 1
    assert manifest["novels"][0]["title"] == "备份测试小说"

    novel_data = json.loads(zf.read(f"novels/{NOVEL}.json"))
    assert novel_data["format_version"] == 6
    assert novel_data["novel"]["id"] == NOVEL
    assert len(novel_data["chapters"]) == 1


@pytest.mark.asyncio
async def test_export_backup_empty_db(api_client):
    client, _db = api_client
    resp = await client.get("/api/backup/export")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["novel_count"] == 0
    assert manifest["novels"] == []


# ── POST /api/backup/import/preview ──


@pytest.mark.asyncio
async def test_preview_backup_happy_no_conflict(api_client):
    client, _db = api_client
    data = _make_backup_zip([_novel_export_payload()])

    resp = await client.post("/api/backup/import/preview", files=_upload(data))
    assert resp.status_code == 200
    body = resp.json()
    assert body["backup_format_version"] == BACKUP_FORMAT_VERSION
    assert body["novel_count"] == 1
    assert body["conflict_count"] == 0
    assert body["novels"][0]["conflict"] is False
    assert body["zip_size_bytes"] == len(data)


@pytest.mark.asyncio
async def test_preview_backup_title_conflict(api_client):
    """库中已有同名小说 → 预览标记 conflict 并给出 existing_id。"""
    client, db = api_client
    await _seed_novel(db, novel_id="existing-1")
    data = _make_backup_zip([_novel_export_payload()])

    resp = await client.post("/api/backup/import/preview", files=_upload(data))
    assert resp.status_code == 200
    body = resp.json()
    assert body["conflict_count"] == 1
    entry = body["novels"][0]
    assert entry["conflict"] is True
    assert entry["existing_id"] == "existing-1"


@pytest.mark.asyncio
async def test_preview_backup_invalid_zip(api_client):
    client, _db = api_client
    resp = await client.post(
        "/api/backup/import/preview", files=_upload(b"not a zip at all"),
    )
    assert resp.status_code == 400
    assert "ZIP" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_preview_backup_unsupported_version(api_client):
    client, _db = api_client
    data = _make_backup_zip([_novel_export_payload()], version=99)
    resp = await client.post("/api/backup/import/preview", files=_upload(data))
    assert resp.status_code == 400
    assert "版本" in resp.json()["detail"]


# ── POST /api/backup/import/confirm ──


@pytest.mark.asyncio
async def test_confirm_import_happy(api_client):
    client, db = api_client
    data = _make_backup_zip([_novel_export_payload()])

    resp = await client.post("/api/backup/import/confirm", files=_upload(data))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "total": 1, "imported": 1, "skipped": 0, "overwritten": 0, "errors": [],
    }

    cur = await db.execute("SELECT title, total_chapters FROM novels")
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "备份测试小说"


@pytest.mark.asyncio
async def test_confirm_import_conflict_skip_then_overwrite(api_client):
    """同名冲突:skip 模式跳过;overwrite 模式删除旧小说后重建。"""
    client, db = api_client
    await _seed_novel(db, novel_id="existing-1")
    data = _make_backup_zip([_novel_export_payload()])

    resp = await client.post(
        "/api/backup/import/confirm?conflict_mode=skip", files=_upload(data),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped"] == 1
    assert body["imported"] == 0
    cur = await db.execute("SELECT id FROM novels")
    assert [r["id"] for r in await cur.fetchall()] == ["existing-1"]

    resp = await client.post(
        "/api/backup/import/confirm?conflict_mode=overwrite",
        files=_upload(data),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overwritten"] == 1
    assert body["imported"] == 1
    # 旧 id 被删除,新小说使用全新 id
    cur = await db.execute("SELECT id, title FROM novels")
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] != "existing-1"
    assert rows[0]["title"] == "备份测试小说"


@pytest.mark.asyncio
async def test_confirm_import_invalid_mode(api_client):
    client, _db = api_client
    data = _make_backup_zip([_novel_export_payload()])
    resp = await client.post(
        "/api/backup/import/confirm?conflict_mode=merge", files=_upload(data),
    )
    assert resp.status_code == 400
    assert "conflict_mode" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_import_invalid_zip(api_client):
    client, _db = api_client
    resp = await client.post(
        "/api/backup/import/confirm", files=_upload(b"\x00\x01\x02"),
    )
    assert resp.status_code == 400
    assert "ZIP" in resp.json()["detail"]


# ── 导出 → 导入 round-trip ──


@pytest.mark.asyncio
async def test_export_import_roundtrip(api_client):
    """真实导出包再经 preview/confirm 导回新库(改名避免冲突)。"""
    client, db = api_client
    await _seed_novel(db)

    exported = (await client.get("/api/backup/export")).content
    preview = await client.post(
        "/api/backup/import/preview", files=_upload(exported),
    )
    assert preview.status_code == 200
    assert preview.json()["conflict_count"] == 1  # 与自身冲突

    # 清库后导回
    await db.execute("DELETE FROM novels")
    await db.commit()
    resp = await client.post(
        "/api/backup/import/confirm", files=_upload(exported),
    )
    assert resp.status_code == 200
    assert resp.json()["imported"] == 1
    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM chapter_facts",
    )
    assert (await cur.fetchone())["n"] == 1
