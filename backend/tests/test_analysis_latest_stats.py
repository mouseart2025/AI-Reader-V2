"""analysis/latest 端点 SQL 聚合 stats/quality 的等价性测试(issue #51)。

旧实现把全量 fact_json 拉到 Python 逐行 json.loads 求和;新实现用
chapter_fact_store.get_fact_stats() 在 SQL 内聚合。这里钉死两者数值一致,
以及端点响应 schema 不变。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.analysis import get_latest_task
from src.db import chapter_fact_store
from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    LocationFact,
    RelationshipFact,
)

NOVEL = "n-stats"
OTHER = "n-other"


def _nonclosing_factory(memory_db):
    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    return factory


async def _seed_novel(db, novel_id: str, chapters: int) -> None:
    await db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, "测试小说")
    )
    for i in range(1, chapters + 1):
        await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)",
            (novel_id, i, f"第{i}章", "原文"),
        )
    await db.commit()


async def _seed_facts(db) -> None:
    """3 章正常 fact + 1 章缺 key 的裸 JSON(另一小说 1 章干扰数据)。"""
    await _seed_novel(db, NOVEL, 4)
    await _seed_novel(db, OTHER, 1)

    f1 = ChapterFact(
        chapter_id=1,
        novel_id=NOVEL,
        characters=[CharacterFact(name="甲"), CharacterFact(name="乙")],
        locations=[LocationFact(name="青牛镇", type="城池")],
        relationships=[
            RelationshipFact(person_a="甲", person_b="乙", relation_type="师徒")
        ],
        events=[EventFact(summary="拜师", type="成长")],
    )
    await chapter_fact_store.insert_chapter_fact(
        novel_id=NOVEL,
        chapter_id=1,
        fact=f1,
        llm_model="m",
        extraction_ms=1,
        is_truncated=True,
        segment_count=3,
    )
    f2 = ChapterFact(
        chapter_id=2,
        novel_id=NOVEL,
        characters=[CharacterFact(name="丙")],
        events=[
            EventFact(summary="战斗", type="战斗"),
            EventFact(summary="疗伤", type="其他"),
        ],
    )
    await chapter_fact_store.insert_chapter_fact(
        novel_id=NOVEL,
        chapter_id=2,
        fact=f2,
        llm_model="m",
        extraction_ms=1,
        output_truncated=True,
    )
    f3 = ChapterFact(chapter_id=3, novel_id=NOVEL)
    await chapter_fact_store.insert_chapter_fact(
        novel_id=NOVEL,
        chapter_id=3,
        fact=f3,
        llm_model="m",
        extraction_ms=1,
        segment_count=2,
    )
    # 旧数据兼容:fact_json 可能缺 characters/relationships/events 等 key,
    # Python 侧 fact.get(..., []) 计 0,SQL 侧 json_array_length 为 NULL 也须计 0。
    await db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json, llm_model, "
        "extraction_ms, is_truncated, segment_count, output_truncated) "
        "VALUES (?, ?, ?, 'm', 1, NULL, NULL, NULL)",
        (NOVEL, 4, json.dumps({"locations": [{"name": "断岭"}]}, ensure_ascii=False)),
    )
    # 另一本小说的数据不得混入(多小说隔离)
    f_other = ChapterFact(
        chapter_id=1,
        novel_id=OTHER,
        characters=[CharacterFact(name="路人")] * 5,
    )
    await chapter_fact_store.insert_chapter_fact(
        novel_id=OTHER,
        chapter_id=1,
        fact=f_other,
        llm_model="m",
        extraction_ms=1,
    )
    await db.commit()


def _expected_via_python_loop(all_facts: list[dict]) -> dict:
    """修复前端点里的 Python 求和逻辑,作为等价基准。"""
    expected = {
        "entities": 0,
        "relations": 0,
        "events": 0,
        "truncated_chapters": 0,
        "segmented_chapters": 0,
        "total_segments": 0,
        "output_truncated_chapters": 0,
    }
    for ef in all_facts:
        fact = ef.get("fact", {})
        expected["entities"] += len(fact.get("characters", [])) + len(
            fact.get("locations", [])
        )
        expected["relations"] += len(fact.get("relationships", []))
        expected["events"] += len(fact.get("events", []))
        if ef.get("is_truncated"):
            expected["truncated_chapters"] += 1
        if ef.get("output_truncated"):
            expected["output_truncated_chapters"] += 1
        seg = ef.get("segment_count", 1)
        if seg > 1:
            expected["segmented_chapters"] += 1
        expected["total_segments"] += seg
    return expected


@pytest.mark.asyncio
async def test_get_fact_stats_matches_python_loop(memory_db):
    factory = _nonclosing_factory(memory_db)
    with patch("src.db.chapter_fact_store.get_connection", factory):
        await _seed_facts(memory_db)
        expected = _expected_via_python_loop(
            await chapter_fact_store.get_all_chapter_facts(NOVEL)
        )
        agg = await chapter_fact_store.get_fact_stats(NOVEL)

    assert agg == expected
    # sanity:确实聚合到了数据,不是全零通过
    assert agg["entities"] == 5  # 3 characters + 2 locations(含裸 JSON 的断岭)
    assert agg["total_segments"] == 3 + 1 + 2 + 1


@pytest.mark.asyncio
async def test_get_fact_stats_empty_novel_returns_zeros(memory_db):
    factory = _nonclosing_factory(memory_db)
    with patch("src.db.chapter_fact_store.get_connection", factory):
        agg = await chapter_fact_store.get_fact_stats("no-such-novel")
    assert agg == {
        "entities": 0,
        "relations": 0,
        "events": 0,
        "truncated_chapters": 0,
        "output_truncated_chapters": 0,
        "segmented_chapters": 0,
        "total_segments": 0,
    }


@pytest.mark.asyncio
async def test_latest_endpoint_response_schema_unchanged(memory_db):
    """端点响应 key 集合与 stats/quality 数值保持前端契约。"""
    factory = _nonclosing_factory(memory_db)
    service = MagicMock()
    service.get_live_timing = MagicMock(return_value=None)
    service.get_retry_progress = MagicMock(return_value=None)

    with (
        patch("src.db.chapter_fact_store.get_connection", factory),
        patch("src.api.routes.analysis.analysis_task_store") as mock_task_store,
        patch("src.api.routes.analysis.get_analysis_service", return_value=service),
    ):
        await _seed_facts(memory_db)
        mock_task_store.get_latest_task = AsyncMock(
            return_value={"id": "t1", "novel_id": NOVEL, "status": "completed"}
        )
        mock_task_store.get_failed_chapters = AsyncMock(return_value=[])

        resp = await get_latest_task(NOVEL)

    assert set(resp.keys()) == {
        "task",
        "stats",
        "quality",
        "timing",
        "failed_chapters",
        "retry_progress",
    }
    assert set(resp["stats"].keys()) == {"entities", "relations", "events"}
    assert set(resp["quality"].keys()) == {
        "truncated_chapters",
        "segmented_chapters",
        "total_segments",
        "output_truncated_chapters",
    }
    assert resp["stats"] == {"entities": 5, "relations": 1, "events": 3}
    assert resp["quality"] == {
        "truncated_chapters": 1,
        "segmented_chapters": 2,
        "total_segments": 7,
        "output_truncated_chapters": 1,
    }
