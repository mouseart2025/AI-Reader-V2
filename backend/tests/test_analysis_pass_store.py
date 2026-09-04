"""multi-pass Epic 1 测试: analysis_passes / pass_chapter_facts 影子表 + store。

覆盖 Story 1.1 / 1.2 验收:
- 建表幂等(CREATE TABLE IF NOT EXISTS,无 ALTER);
- save→load 往返 JSON 一致(中文不转义);
- 同一 pass 重复写同章 = UPSERT 不重复行;
- get_active_pass 单活语义(running/paused);
- history_json 埋点能回答「每章何时完成独立抽取、何时解锁对照、差异多少、
  人裁了多少」;空 pass 不产生脏数据;
- delete_pass 级联清除影子数据;recover_stale_passes: running → paused。
"""

import json

import pytest

from src.db import analysis_pass_store
from src.db.sqlite_db import _SCHEMA_SQL
from src.models.chapter_fact import ChapterFact, CharacterFact

NOVEL = "novel-pass"


class _NonClosing:
    """包装共享 memory_db,store 函数 finally 里的 close() 变为 no-op。"""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.fixture
def pass_db(memory_db):
    """共享 memory_db + patch store 的 get_connection;播种 novel + 3 章。"""

    async def _factory():
        return _NonClosing(memory_db)

    return memory_db, _factory


async def _seed(pass_db) -> None:
    memory_db, _ = pass_db
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试小说"),
    )
    for i in (1, 2, 3):
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content)"
            " VALUES (?, ?, ?, ?, ?)",
            (i, NOVEL, i, f"第{i}章", f"第{i}章原文"),
        )
    await memory_db.commit()


def _fact(chapter_num: int, name: str) -> ChapterFact:
    return ChapterFact(
        chapter_id=chapter_num,
        novel_id=NOVEL,
        characters=[CharacterFact(name=name)],
    )


# ── Story 1.1: 建表幂等 + CRUD + UPSERT ──


@pytest.mark.asyncio
async def test_schema_idempotent(pass_db):
    """_SCHEMA_SQL 重复执行不报错(CREATE TABLE IF NOT EXISTS 幂等)。"""
    memory_db, _ = pass_db
    # fixture 已执行过一次,再执行两次验证幂等
    await memory_db.executescript(_SCHEMA_SQL)
    await memory_db.executescript(_SCHEMA_SQL)
    cursor = await memory_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name IN ('analysis_passes', 'pass_chapter_facts') ORDER BY name",
    )
    tables = [r[0] for r in await cursor.fetchall()]
    assert tables == ["analysis_passes", "pass_chapter_facts"]


@pytest.mark.asyncio
async def test_pass_crud_roundtrip(pass_db, monkeypatch):
    """create→get/list 往返;config_json/history_json 解析;中文不转义。"""
    memory_db, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass(
        "p1", NOVEL, 1, 3,
        model_name="qwen3:8b", config={"备注": "独立二审"}, provider="ollama",
    )
    row = await analysis_pass_store.get_pass("p1")
    assert row["novel_id"] == NOVEL
    assert row["kind"] == "source_pass"
    assert row["status"] == "running"
    assert row["model_name"] == "qwen3:8b"
    assert row["current_chapter"] == 0  # chapter_start - 1:尚无已处理章节
    assert row["config_json"] == {"备注": "独立二审"}  # 中文往返一致
    history = row["history_json"]
    assert history["kind"] == "source_pass"
    assert history["model"] == "qwen3:8b"
    assert history["provider"] == "ollama"
    assert history["source_only_completed_at"] is None
    assert history["chapters"] == {}

    # 原始存储层中文未被转义成 \uXXXX
    cursor = await memory_db.execute(
        "SELECT config_json, history_json FROM analysis_passes WHERE id = 'p1'",
    )
    raw = await cursor.fetchone()
    assert "独立二审" in raw["config_json"]

    listed = await analysis_pass_store.list_passes(NOVEL)
    assert [p["id"] for p in listed] == ["p1"]
    assert await analysis_pass_store.list_passes("other-novel") == []


@pytest.mark.asyncio
async def test_pass_fact_upsert_no_duplicate_rows(pass_db, monkeypatch):
    """同一 pass 重复写同章 = UPSERT 不重复行,内容为最后一次写入。"""
    memory_db, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)
    await analysis_pass_store.upsert_pass_chapter_fact("p1", 1, _fact(1, "宋江"))
    await analysis_pass_store.upsert_pass_chapter_fact("p1", 1, _fact(1, "宋江 revised"))
    await analysis_pass_store.upsert_pass_chapter_fact("p1", 2, _fact(2, "武松"))

    cursor = await memory_db.execute(
        "SELECT COUNT(*) AS n FROM pass_chapter_facts WHERE pass_id = 'p1'",
    )
    assert (await cursor.fetchone())["n"] == 2  # 不产生重复行

    facts = await analysis_pass_store.get_pass_chapter_facts("p1")
    assert [f["chapter_num"] for f in facts] == [1, 2]
    assert facts[0]["fact"]["characters"][0]["name"] == "宋江 revised"
    # 与 chapter_facts 同构:fact JSON 内含 chapter_id/novel_id
    assert facts[0]["fact"]["chapter_id"] == 1
    assert facts[0]["fact"]["novel_id"] == NOVEL


@pytest.mark.asyncio
async def test_get_active_pass_single_active_semantics(pass_db, monkeypatch):
    """get_active_pass: running/paused 算活动,completed/cancelled 不算。"""
    _, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)
    assert (await analysis_pass_store.get_active_pass(NOVEL))["id"] == "p1"

    await analysis_pass_store.update_pass_status("p1", "paused")
    assert (await analysis_pass_store.get_active_pass(NOVEL))["id"] == "p1"

    await analysis_pass_store.update_pass_status("p1", "completed")
    assert await analysis_pass_store.get_active_pass(NOVEL) is None

    row = await analysis_pass_store.get_pass("p1")
    assert row["status"] == "completed"
    assert row["completed_at"] is not None  # 终态写完成时间


@pytest.mark.asyncio
async def test_progress_and_failed_chapter_rows(pass_db, monkeypatch):
    """进度更新 + failed 章节行(状态写 pass_chapter_facts.status)。"""
    _, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)
    await analysis_pass_store.update_pass_progress("p1", 2)
    assert (await analysis_pass_store.get_pass("p1"))["current_chapter"] == 2

    await analysis_pass_store.upsert_pass_chapter_fact(
        "p1", 2, _fact(2, ""), status="failed", error="LLM 超时",
    )
    # failed 章节不进 context provider、不进 completed 集合
    assert await analysis_pass_store.get_pass_chapter_facts("p1") == []
    assert await analysis_pass_store.get_completed_chapter_nums("p1") == set()

    await analysis_pass_store.upsert_pass_chapter_fact("p1", 1, _fact(1, "宋江"))
    assert await analysis_pass_store.get_completed_chapter_nums("p1") == {1}


# ── Story 1.2: history_json 埋点 ──


@pytest.mark.asyncio
async def test_history_answers_review_questions(pass_db, monkeypatch):
    """history_json 完整回答「每章何时完成独立抽取、何时解锁对照、差异多少、
    人裁了多少」;未发生的事件为 None/0,不产生脏数据。"""
    _, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass(
        "p1", NOVEL, 1, 3, model_name="qwen3:8b", provider="ollama",
    )
    await analysis_pass_store.update_chapter_history("p1", 1, {
        "chapter_id": 1,
        "char_start": 0,
        "char_end": 1234,
        "completed_at": "2026-09-04T00:00:01+00:00",
        "findings": {"characters": 5, "relationships": 3, "locations": 2, "events": 4},
    })
    await analysis_pass_store.update_pass_history(
        "p1", source_only_completed_at="2026-09-04T00:01:00+00:00",
    )

    history = (await analysis_pass_store.get_pass("p1"))["history_json"]
    # pass 级:kind / model / provider / source-only 完成时间戳
    assert history["kind"] == "source_pass"
    assert history["model"] == "qwen3:8b"
    assert history["provider"] == "ollama"
    assert history["source_only_completed_at"] == "2026-09-04T00:01:00+00:00"
    # 章节级:source range + 完成时间戳 + findings
    ch1 = history["chapters"]["1"]
    assert ch1["chapter_id"] == 1
    assert (ch1["char_start"], ch1["char_end"]) == (0, 1234)
    assert ch1["completed_at"] == "2026-09-04T00:00:01+00:00"
    assert ch1["findings"]["characters"] == 5
    # 何时解锁对照 / 差异多少 / 人裁了多少 — Epic 3/4 回填,骨架就位
    assert ch1["air_unlocked_at"] is None
    assert ch1["diff_counts"] == {
        "air_only": None, "pass_only": None, "different": None,
    }
    assert ch1["adjudication"] == {"confirmed": 0, "rejected": 0, "neither": 0}
    assert ch1["adjudication_log"] == []
    # 未处理的章节不出现(空 pass 不产生脏数据)
    assert set(history["chapters"]) == {"1"}

    # 章节埋点合并:后续写入(Epic 3 unlock / Epic 4 裁决)不覆盖已有字段
    await analysis_pass_store.update_chapter_history(
        "p1", 1, {"air_unlocked_at": "2026-09-04T00:02:00+00:00"},
    )
    ch1 = (await analysis_pass_store.get_pass("p1"))["history_json"]["chapters"]["1"]
    assert ch1["air_unlocked_at"] == "2026-09-04T00:02:00+00:00"
    assert ch1["findings"]["characters"] == 5  # 已有字段保留


@pytest.mark.asyncio
async def test_empty_pass_no_dirty_data(pass_db, monkeypatch):
    """空 pass:history.chapters 为空、影子 facts 为空、list 可查。"""
    _, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)
    row = await analysis_pass_store.get_pass("p1")
    assert row["history_json"]["chapters"] == {}
    assert await analysis_pass_store.get_pass_chapter_facts("p1") == []


# ── 删除级联 + stale 恢复 ──


@pytest.mark.asyncio
async def test_delete_pass_cascades_shadow_data(pass_db, monkeypatch):
    """delete_pass 级联删除 pass_chapter_facts,主表章节不受影响。"""
    memory_db, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)
    await analysis_pass_store.upsert_pass_chapter_fact("p1", 1, _fact(1, "宋江"))

    assert await analysis_pass_store.delete_pass("p1") is True
    assert await analysis_pass_store.get_pass("p1") is None
    cursor = await memory_db.execute(
        "SELECT COUNT(*) AS n FROM pass_chapter_facts WHERE pass_id = 'p1'",
    )
    assert (await cursor.fetchone())["n"] == 0
    cursor = await memory_db.execute("SELECT COUNT(*) AS n FROM chapters")
    assert (await cursor.fetchone())["n"] == 3  # 主表不受影响
    assert await analysis_pass_store.delete_pass("p1") is False


@pytest.mark.asyncio
async def test_recover_stale_passes(pass_db, monkeypatch):
    """recover_stale_passes: running → paused;paused/completed 不动。"""
    _, factory = pass_db
    await _seed(pass_db)
    monkeypatch.setattr(analysis_pass_store, "get_connection", factory)

    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)
    await analysis_pass_store.create_pass("p2", NOVEL, 1, 3)
    await analysis_pass_store.update_pass_status("p2", "completed")

    assert await analysis_pass_store.recover_stale_passes() == 1
    assert (await analysis_pass_store.get_pass("p1"))["status"] == "paused"
    assert (await analysis_pass_store.get_pass("p2"))["status"] == "completed"
    assert await analysis_pass_store.recover_stale_passes() == 0  # 幂等
