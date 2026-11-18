"""Regression tests for issue #27 — ``.air`` export crashed on a set.

pydantic's ``model_dump()`` keeps ``set`` intact, and ``WorldStructure`` has a
``set[str]`` field (``virtual_locations``). Every ``.air`` / ``format=json``
export therefore raised ``TypeError: Object of type set is not JSON
serializable`` from v0.77.0 until v0.85.2.

These tests exercise the *real* ``_build_precomputed`` path; the shared
``mock_get_connection`` fixture stubs it out, which is exactly why CI stayed
green while the feature was broken in production.
"""

import gzip
import json

import pytest

from src.models.world_structure import WorldStructure
from src.services import backup_service, export_service

NOVEL_ID = "issue27-novel"


async def _seed_novel(db):
    await db.execute(
        "INSERT INTO novels (id, title, author, total_chapters, total_words, prescan_status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (NOVEL_ID, "封神演义", "许仲琳", 1, 1200, "completed"),
    )
    await db.commit()


def _patch_precompute_deps(monkeypatch):
    """Keep the real builder, stub only its heavy dependencies."""
    from src.db import world_structure_store
    from src.services import encyclopedia_service
    from src.services import visualization_service as viz

    async def _range(_novel_id):
        return (1, 1)

    async def _empty(*_args, **_kwargs):
        return []

    async def _empty_stats(_novel_id):
        return {}

    async def _ws(novel_id):
        ws = WorldStructure.create_default(novel_id)
        # The exact values a real 红楼梦 export carried.
        ws.virtual_locations = {"天下", "主世界"}
        return ws

    monkeypatch.setattr(viz, "get_analyzed_range", _range)
    for name in (
        "get_graph_data",
        "get_map_data",
        "get_timeline_data",
        "get_factions_data",
    ):
        monkeypatch.setattr(viz, name, _empty)
    monkeypatch.setattr(encyclopedia_service, "get_encyclopedia_entries", _empty)
    monkeypatch.setattr(encyclopedia_service, "get_category_stats", _empty_stats)
    monkeypatch.setattr(world_structure_store, "load_with_overrides", _ws)


@pytest.mark.asyncio
async def test_export_payload_is_json_serializable(real_precompute_connection, monkeypatch):
    """The whole export payload must survive json.dumps — .air depends on it."""
    await _seed_novel(real_precompute_connection)
    _patch_precompute_deps(monkeypatch)

    result = await export_service.export_novel(NOVEL_ID)

    # Before the fix this line raised TypeError.
    blob = json.dumps(result, ensure_ascii=False)
    assert len(blob) > 0

    ws_out = result["precomputed"]["world_structure"]
    assert isinstance(ws_out["virtual_locations"], list), (
        "virtual_locations must be JSON-native; python-mode model_dump keeps a set"
    )
    assert set(ws_out["virtual_locations"]) == {"天下", "主世界"}


@pytest.mark.asyncio
async def test_air_bytes_are_valid_gzip_json(real_precompute_connection, monkeypatch):
    """Mirror the .air branch: gzip(json.dumps(payload)) must round-trip."""
    await _seed_novel(real_precompute_connection)
    _patch_precompute_deps(monkeypatch)

    result = await export_service.export_novel(NOVEL_ID)
    compressed = gzip.compress(
        json.dumps(result, ensure_ascii=False).encode("utf-8"), compresslevel=6
    )

    assert compressed[:2] == b"\x1f\x8b"
    restored = json.loads(gzip.decompress(compressed))
    assert restored["format_version"] == export_service.CURRENT_FORMAT_VERSION
    assert restored["novel"]["title"] == "封神演义"


@pytest.mark.asyncio
async def test_backup_raises_when_every_novel_fails(mock_get_connection, monkeypatch):
    """All-fail must not return an empty ZIP with HTTP 200."""
    await _seed_novel(mock_get_connection)

    async def _boom(_novel_id):
        raise TypeError("Object of type set is not JSON serializable")

    monkeypatch.setattr(backup_service, "export_novel", _boom)

    with pytest.raises(RuntimeError, match="全部导出失败"):
        await backup_service.export_all()


@pytest.mark.asyncio
async def test_backup_surfaces_partial_failures_in_manifest(mock_get_connection, monkeypatch):
    """One good + one bad novel: the ZIP is valid, but the manifest must say so."""
    db = mock_get_connection
    await _seed_novel(db)
    await db.execute(
        "INSERT INTO novels (id, title, author, total_chapters, total_words, prescan_status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("issue27-bad", "坏书", "x", 1, 10, "completed"),
    )
    await db.commit()

    async def _selective(novel_id):
        if novel_id == "issue27-bad":
            raise TypeError("Object of type set is not JSON serializable")
        return {"novel": {"total_chapters": 1}}

    monkeypatch.setattr(backup_service, "export_novel", _selective)

    import io
    import zipfile

    buf = await backup_service.export_all()
    zf = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    manifest = json.loads(zf.read("manifest.json"))

    assert manifest["novel_count"] == 1
    assert manifest["failed_count"] == 1
    assert manifest["failed"][0]["id"] == "issue27-bad"
    assert "set is not JSON serializable" in manifest["failed"][0]["error"]
