"""Tests for annotation_store CRUD (划线 + 批注).

Covers: add/list round-trip, chapter filtering, partial update
(color-only / note-only), update of missing id, and delete.
"""

import pytest
from unittest.mock import patch

from src.db import annotation_store

NOVEL = "novel-ann"


class _NonClosing:
    """Proxy that prevents the store from closing the shared test conn."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass  # no-op: fixture manages lifecycle


@pytest.fixture
def conn_factory(memory_db):
    async def _factory():
        return _NonClosing(memory_db)

    return _factory


async def _add(conn_factory, **kwargs):
    defaults = dict(
        novel_id=NOVEL,
        chapter_num=1,
        start_offset=0,
        end_offset=4,
        anchor_text="话说天下",
        color="yellow",
        note="",
    )
    defaults.update(kwargs)
    with patch("src.db.annotation_store.get_connection", conn_factory):
        return await annotation_store.add_annotation(**defaults)


@pytest.mark.asyncio
async def test_add_and_list_roundtrip(memory_db, conn_factory):
    with patch("src.db.annotation_store.get_connection", conn_factory):
        ann = await annotation_store.add_annotation(
            novel_id=NOVEL,
            chapter_num=1,
            start_offset=0,
            end_offset=4,
            anchor_text="话说天下",
            color="green",
            note="开篇",
        )
        assert ann["id"] > 0
        assert ann["novel_id"] == NOVEL
        assert ann["chapter_num"] == 1
        assert ann["start_offset"] == 0
        assert ann["end_offset"] == 4
        assert ann["anchor_text"] == "话说天下"
        assert ann["color"] == "green"
        assert ann["note"] == "开篇"
        assert ann["created_at"]

        rows = await annotation_store.list_annotations(NOVEL)
        assert len(rows) == 1
        assert rows[0]["id"] == ann["id"]

        # Other novels see nothing
        assert await annotation_store.list_annotations("novel-other") == []


@pytest.mark.asyncio
async def test_list_filter_by_chapter(memory_db, conn_factory):
    await _add(conn_factory, chapter_num=1, start_offset=0, end_offset=2,
               anchor_text="话说")
    await _add(conn_factory, chapter_num=1, start_offset=4, end_offset=6,
               anchor_text="大势")
    await _add(conn_factory, chapter_num=2, start_offset=0, end_offset=2,
               anchor_text="次日")

    with patch("src.db.annotation_store.get_connection", conn_factory):
        all_rows = await annotation_store.list_annotations(NOVEL)
        assert len(all_rows) == 3

        ch1 = await annotation_store.list_annotations(NOVEL, chapter_num=1)
        assert len(ch1) == 2
        assert all(r["chapter_num"] == 1 for r in ch1)
        # Ordered by start_offset within the chapter
        assert ch1[0]["start_offset"] < ch1[1]["start_offset"]

        ch2 = await annotation_store.list_annotations(NOVEL, chapter_num=2)
        assert len(ch2) == 1
        assert ch2[0]["anchor_text"] == "次日"

        assert await annotation_store.list_annotations(NOVEL, chapter_num=99) == []


@pytest.mark.asyncio
async def test_update_partial_fields(memory_db, conn_factory):
    ann = await _add(conn_factory, color="yellow", note="旧批注")

    with patch("src.db.annotation_store.get_connection", conn_factory):
        # Update color only — note preserved
        updated = await annotation_store.update_annotation(ann["id"], color="blue")
        assert updated is not None
        assert updated["color"] == "blue"
        assert updated["note"] == "旧批注"
        assert updated["anchor_text"] == "话说天下"

        # Update note only — color preserved
        updated = await annotation_store.update_annotation(ann["id"], note="新批注")
        assert updated is not None
        assert updated["color"] == "blue"
        assert updated["note"] == "新批注"

        # Update both
        updated = await annotation_store.update_annotation(
            ann["id"], color="pink", note=""
        )
        assert updated is not None
        assert updated["color"] == "pink"
        assert updated["note"] == ""

        # No-op update returns current row unchanged
        updated = await annotation_store.update_annotation(ann["id"])
        assert updated is not None
        assert updated["color"] == "pink"

        # Missing id returns None
        assert await annotation_store.update_annotation(99999, color="blue") is None


@pytest.mark.asyncio
async def test_delete(memory_db, conn_factory):
    ann = await _add(conn_factory)

    with patch("src.db.annotation_store.get_connection", conn_factory):
        assert await annotation_store.delete_annotation(ann["id"]) is True
        assert await annotation_store.list_annotations(NOVEL) == []
        # Deleting again returns False
        assert await annotation_store.delete_annotation(ann["id"]) is False
