"""export_import.py 路由级测试:单本小说导出 (json/air) 与导入
preview/confirm 主流程、非法文件与哈希/标题冲突检测。

真实 app + httpx ASGITransport;DB 走 conftest 的 api_client fixture
(memory_db),导出导入的 gzip/JSON 均在内存处理,不触碰真实文件系统。
"""

import gzip
import json

import pytest

NOVEL = "novel-export"


async def _seed_novel(db, novel_id=NOVEL, title="导出测试小说", file_hash=None):
    await db.execute(
        "INSERT INTO novels (id, title, file_hash, total_chapters, total_words)"
        " VALUES (?, ?, ?, 1, 100)",
        (novel_id, title, file_hash),
    )
    await db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
        " analysis_status) VALUES (?, ?, 1, '第1章', '正文内容', 'completed')",
        (1, novel_id),
    )
    await db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json, llm_model)"
        " VALUES (?, 1, ?, 'qwen3:8b')",
        (novel_id, json.dumps({"characters": [{"name": "武松"}]})),
    )
    await db.commit()


def _export_payload(title="导出测试小说", file_hash=None, version=6):
    return {
        "format_version": version,
        "novel": {
            "id": "src-novel", "title": title, "file_hash": file_hash,
            "total_chapters": 1, "total_words": 100,
        },
        "chapters": [
            {
                "chapter_num": 1, "title": "第1章", "content": "正文内容",
                "word_count": 100, "analysis_status": "completed",
            },
        ],
        "chapter_facts": [
            {
                "chapter_num": 1,
                "fact_json": json.dumps({"characters": []}),
                "llm_model": "qwen3:8b",
            },
        ],
    }


def _json_upload(payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return {"file": ("export.json", data, "application/json")}


# ── GET /api/novels/{id}/export ──


@pytest.mark.asyncio
async def test_export_novel_json_happy(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/export")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.json()
    assert body["format_version"] == 6
    assert body["novel"]["title"] == "导出测试小说"
    assert len(body["chapters"]) == 1
    assert len(body["chapter_facts"]) == 1


@pytest.mark.asyncio
async def test_export_novel_air_format(api_client):
    client, db = api_client
    await _seed_novel(db)

    resp = await client.get(f"/api/novels/{NOVEL}/export?format=air")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-air+gzip"
    assert resp.content[:2] == b"\x1f\x8b"  # gzip magic

    body = json.loads(gzip.decompress(resp.content))
    assert body["format_version"] == 6
    assert body["novel"]["id"] == NOVEL


@pytest.mark.asyncio
async def test_export_novel_404(api_client):
    client, _db = api_client
    resp = await client.get("/api/novels/no-such-novel/export")
    assert resp.status_code == 404


# ── POST /api/novels/import/preview ──


@pytest.mark.asyncio
async def test_preview_import_happy(api_client):
    client, _db = api_client
    resp = await client.post(
        "/api/novels/import/preview", files=_json_upload(_export_payload()),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "导出测试小说"
    assert body["format_version"] == 6
    assert body["total_chapters"] == 1
    assert body["existing_novel_id"] is None
    assert body["llm_models"] == ["qwen3:8b"]


@pytest.mark.asyncio
async def test_preview_import_gzip_air_file(api_client):
    """上传 .air (gzip) 文件同样能解码预览。"""
    client, _db = api_client
    raw = json.dumps(_export_payload(), ensure_ascii=False).encode("utf-8")
    files = {"file": ("export.air", gzip.compress(raw), "application/gzip")}
    resp = await client.post("/api/novels/import/preview", files=files)
    assert resp.status_code == 200
    assert resp.json()["title"] == "导出测试小说"


@pytest.mark.asyncio
async def test_preview_import_title_conflict(api_client):
    client, db = api_client
    await _seed_novel(db, novel_id="existing-1")

    resp = await client.post(
        "/api/novels/import/preview", files=_json_upload(_export_payload()),
    )
    assert resp.status_code == 200
    assert resp.json()["existing_novel_id"] == "existing-1"


@pytest.mark.asyncio
async def test_preview_import_file_hash_conflict(api_client):
    """标题不同但 file_hash 相同 → 仍判定为同一本小说(哈希冲突)。"""
    client, db = api_client
    await _seed_novel(
        db, novel_id="existing-1", title="改了名的同一本书", file_hash="hash-1",
    )

    payload = _export_payload(title="导出测试小说", file_hash="hash-1")
    resp = await client.post(
        "/api/novels/import/preview", files=_json_upload(payload),
    )
    assert resp.status_code == 200
    assert resp.json()["existing_novel_id"] == "existing-1"


@pytest.mark.asyncio
async def test_preview_import_invalid_json(api_client):
    client, _db = api_client
    files = {"file": ("bad.json", b"{not valid json", "application/json")}
    resp = await client.post("/api/novels/import/preview", files=files)
    assert resp.status_code == 400
    assert "JSON" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_preview_import_corrupt_gzip(api_client):
    """gzip magic 开头但内容损坏 → 400 解压失败。"""
    client, _db = api_client
    files = {"file": ("bad.air", b"\x1f\x8bgarbage-bytes", "application/gzip")}
    resp = await client.post("/api/novels/import/preview", files=files)
    assert resp.status_code == 400
    assert "解压" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_preview_import_unsupported_version(api_client):
    client, _db = api_client
    resp = await client.post(
        "/api/novels/import/preview",
        files=_json_upload(_export_payload(version=99)),
    )
    assert resp.status_code == 400
    assert "format version" in resp.json()["detail"]


# ── POST /api/novels/import/confirm ──


@pytest.mark.asyncio
async def test_confirm_import_happy(api_client):
    client, db = api_client
    resp = await client.post(
        "/api/novels/import/confirm", files=_json_upload(_export_payload()),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "导出测试小说"
    assert body["chapters_imported"] == 1
    assert body["facts_imported"] == 1
    assert body["existing_overwritten"] is False

    cur = await db.execute("SELECT id, title FROM novels")
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_confirm_import_overwrite_replaces_existing(api_client):
    """同名小说 + overwrite=true → 旧记录被删除,导入生成新 id。"""
    client, db = api_client
    await _seed_novel(db, novel_id="existing-1")

    resp = await client.post(
        "/api/novels/import/confirm?overwrite=true",
        files=_json_upload(_export_payload()),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["existing_overwritten"] is True
    assert body["id"] != "existing-1"

    cur = await db.execute("SELECT id, title FROM novels")
    rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_confirm_import_unsupported_version(api_client):
    client, _db = api_client
    resp = await client.post(
        "/api/novels/import/confirm",
        files=_json_upload(_export_payload(version=99)),
    )
    assert resp.status_code == 400
    assert "format version" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_export_then_import_roundtrip(api_client):
    """真实导出 JSON 再 confirm 导入(不 overwrite,生成第二本副本)。"""
    client, db = api_client
    await _seed_novel(db)

    exported = (await client.get(f"/api/novels/{NOVEL}/export")).content
    files = {"file": ("export.json", exported, "application/json")}
    resp = await client.post("/api/novels/import/confirm", files=files)
    assert resp.status_code == 200
    assert resp.json()["chapters_imported"] == 1

    cur = await db.execute("SELECT COUNT(*) AS n FROM novels")
    assert (await cur.fetchone())["n"] == 2
