"""multi-pass Epic 2 Story 2.3 测试: SourcePassService 运行循环。

全部使用 mock LLM + memory DB,不打真实 API、不写真实数据库。覆盖验收:
- 二审独立完成,产物落影子表,全文打 source="source_pass";
- 全程对 chapter_facts / world_structures / entity_dictionary / Chroma 零写入;
- 一审毒丸 fact 的字串不出现在二审任何 LLM 调用里(机制级隔离);
- 可暂停/续跑(换新 service 实例模拟重启,从 current_chapter+1 续);
- 一章失败不阻塞;单活互斥(一审 ↔ 二审双向);
- WebSocket 广播全部带 pass_id 维度(pass_* 消息类型)。
"""

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db import (
    analysis_pass_store,
    analysis_task_store,
    chapter_store,
    entity_dictionary_store,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services import embedding_service
from src.services.analysis_service import AnalysisService, manager
from src.services.source_pass_service import SourcePassService, _build_pass_llm

NOVEL = "novel-sp"

CH1 = "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢。"
CH2 = "次日宋江辞别柴进,独自上路投青州去了。"
CH3 = "宋江行了几日,来到青州地面,远远望见一座城池。"


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class MockLLM:
    """Mock LLM:按 prompt 里的「## 第 N 章」识别章节;可指定某章抛错;
    on_chapter 钩子用于在章节处理中触发 pause/cancel。"""

    def __init__(self, fail_on_chapter: int | None = None, on_chapter=None):
        self.fail_on_chapter = fail_on_chapter
        self.on_chapter = on_chapter
        self.calls: list[tuple[str, str]] = []

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.calls.append((system, prompt))
        m = re.search(r"## 第 (\d+) 章", prompt)
        ch = int(m.group(1)) if m else 0
        if self.fail_on_chapter == ch:
            raise RuntimeError(f"mock llm boom ch{ch}")
        if self.on_chapter:
            await self.on_chapter(ch)
        return {
            "characters": [{"name": "宋江"}, {"name": "武松"}],
            "relationships": [
                {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟"},
            ],
            "locations": [{"name": "柴进庄", "type": "庄园"}],
            "events": [
                {
                    "summary": f"第{ch}章事件",
                    "type": "其他",
                    "importance": "low",
                    "participants": ["宋江"],
                    "location": "柴进庄",
                },
            ],
        }, LlmUsage(10, 5, 15)


@pytest.fixture
def sp_env(memory_db, monkeypatch):
    """播种 novel + 3 章 + 一审毒丸 fact + 词典 + 世界结构;
    patch 全部相关 store 的 get_connection、广播与 Chroma 索引。"""

    async def _factory():
        return _NonClosing(memory_db)

    for mod in (
        analysis_pass_store, analysis_task_store, chapter_store,
        entity_dictionary_store,
    ):
        monkeypatch.setattr(mod, "get_connection", _factory)
    # pass 路径机制上不应读主表 facts:直接埋雷证明没人读
    monkeypatch.setattr(
        "src.extraction.context_summary_builder.get_all_chapter_facts",
        AsyncMock(side_effect=AssertionError("二审不应读 chapter_facts 主表")),
    )

    # 广播捕获(pass_id 维度断言)
    broadcasts: list[dict] = []

    async def _capture(novel_id, data):
        broadcasts.append(data)

    monkeypatch.setattr(manager, "broadcast", _capture)

    # Chroma 零写入断言
    index_chapter = MagicMock(side_effect=AssertionError("二审不应写 Chroma"))
    index_entities = MagicMock(side_effect=AssertionError("二审不应写 Chroma"))
    monkeypatch.setattr(embedding_service, "index_chapter", index_chapter)
    monkeypatch.setattr(embedding_service, "index_entities_from_fact", index_entities)

    # 关闭质量开关,隔离被测行为(证据锚定/维度清洗不在本文件断言范围)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", True)  # 二审也不应补漏

    return memory_db, broadcasts


async def _seed(memory_db) -> None:
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试小说"),
    )
    for i, content in ((1, CH1), (2, CH2), (3, CH3)):
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content)"
            " VALUES (?, ?, ?, ?, ?)",
            (i, NOVEL, i, f"第{i}章", content),
        )
    # 一审毒丸 fact(主表):二审任何 LLM 调用都不应见到这串字
    poison = json.dumps({
        "chapter_id": 1, "novel_id": NOVEL,
        "characters": [{"name": "一审毒丸人物癸"}],
    }, ensure_ascii=False)
    await memory_db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) VALUES (?, 1, ?)",
        (NOVEL, poison),
    )
    await memory_db.execute(
        "INSERT INTO entity_dictionary (novel_id, name, entity_type, frequency, source)"
        " VALUES (?, '宋江', 'person', 10, 'frequency')",
        (NOVEL,),
    )
    await memory_db.execute(
        "INSERT INTO world_structures (novel_id, structure_json) VALUES (?, '{}')",
        (NOVEL,),
    )
    await memory_db.commit()


async def _table_count(memory_db, table: str) -> int:
    cursor = await memory_db.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return (await cursor.fetchone())["n"]


def _start_capture():
    """构造 asyncio.create_task 的替换函数,捕获后台循环协程以便手动驱动。

    用法: ``with patch("asyncio.create_task", fake): pass_id = await svc.start(...)``
    随后 ``await captured["coro"]`` 同步驱动循环跑完(或跑到 pause/cancel)。
    只在 start 调用窗口内 patch,resume 仍用真实 create_task。
    """
    captured: dict = {}

    def fake_create_task(coro, **kwargs):
        captured["coro"] = coro
        return object()

    return captured, fake_create_task


# ── 完整跑通 + 影子存储 + 零写入 + 隔离 ──


@pytest.mark.asyncio
async def test_pass_run_completes_shadow_only(sp_env):
    memory_db, broadcasts = sp_env
    await _seed(memory_db)
    before = {
        t: await _table_count(memory_db, t)
        for t in ("chapter_facts", "world_structures", "entity_dictionary")
    }

    llm = MockLLM()
    svc = SourcePassService(llm=llm)
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        pass_id = await svc.start(NOVEL, model_override="qwen2:7b")
    await captured["coro"]  # 手动驱动后台循环

    # pass 完成,进度推进到末章
    row = await analysis_pass_store.get_pass(pass_id)
    assert row["status"] == "completed"
    assert row["current_chapter"] == 3
    assert row["completed_at"] is not None
    # D3 埋点: model_override 记录进 config 与 history
    assert row["config_json"]["model_override"] == "qwen2:7b"
    assert row["model_name"] == "qwen2:7b"
    assert row["history_json"]["model"] == "qwen2:7b"
    assert row["history_json"]["source_only_completed_at"] is not None

    # 影子表:3 章全 completed,provenance 打点
    facts = await analysis_pass_store.get_pass_chapter_facts(pass_id)
    assert [f["chapter_num"] for f in facts] == [1, 2, 3]
    for f in facts:
        for ch in f["fact"]["characters"]:
            assert ch["source"] == "source_pass"

    # 零写入:主表 / 世界结构 / 词典行数不变;Chroma 未被触碰
    for t, n in before.items():
        assert await _table_count(memory_db, t) == n, f"{t} 被写入"
    embedding_service.index_chapter.assert_not_called()
    embedding_service.index_entities_from_fact.assert_not_called()

    # 机制级隔离:一审毒丸字串不出现在任何二审 LLM 调用里
    all_prompt_text = "\n".join(s + "\n" + p for s, p in llm.calls)
    assert "一审毒丸人物癸" not in all_prompt_text
    # 二审上下文确实用了自身前序 facts:第 2/3 章的 system 含第 1 章二审产物
    assert "已知人物" in llm.calls[1][0]

    # history 埋点:每章可回答「何时完成独立抽取/差异多少/人裁多少」
    chapters = row["history_json"]["chapters"]
    assert set(chapters) == {"1", "2", "3"}
    for num, content in (("1", CH1), ("2", CH2), ("3", CH3)):
        entry = chapters[num]
        assert entry["completed_at"] is not None
        assert entry["char_start"] == 0
        assert entry["char_end"] == len(content)
        assert set(entry["findings"]) == {
            "characters", "relationships", "locations", "events",
        }
        assert entry["air_unlocked_at"] is None
        assert entry["adjudication"] == {
            "confirmed": 0, "rejected": 0, "neither": 0,
        }

    # 广播:全部带 pass_id 维度,pass_* 消息类型
    assert broadcasts
    assert all(b["pass_id"] == pass_id for b in broadcasts)
    assert all(b["type"].startswith("pass_") for b in broadcasts)
    assert any(b["type"] == "pass_status" and b["status"] == "completed"
               for b in broadcasts)


# ── pause / resume(模拟重启)──


@pytest.mark.asyncio
async def test_pause_resume_across_restart(sp_env):
    memory_db, _ = sp_env
    await _seed(memory_db)

    holder: dict = {}

    async def _pause_after_ch1(ch: int) -> None:
        if ch == 1:
            await holder["svc"].pause(holder["pass_id"])

    llm = MockLLM(on_chapter=_pause_after_ch1)
    svc = SourcePassService(llm=llm)
    holder["svc"] = svc
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        pass_id = await svc.start(NOVEL)
    holder["pass_id"] = pass_id
    await captured["coro"]  # 跑完第 1 章后在第 2 章前停下

    row = await analysis_pass_store.get_pass(pass_id)
    assert row["status"] == "paused"
    assert row["current_chapter"] == 1
    assert len(llm.calls) == 1  # 只处理了第 1 章
    assert len(await analysis_pass_store.get_pass_chapter_facts(pass_id)) == 1

    # 模拟重启:全新 service 实例(内存信号全丢),从 DB 状态续跑
    llm2 = MockLLM()
    svc2 = SourcePassService(llm=llm2)
    await svc2.resume(pass_id)
    for _ in range(200):
        await asyncio.sleep(0.01)
        row = await analysis_pass_store.get_pass(pass_id)
        if row["status"] == "completed":
            break
    assert row["status"] == "completed"
    # 从 current_chapter+1 续跑:第 1 章不重抽,只补 2、3 章
    assert len(llm2.calls) == 2
    assert len(await analysis_pass_store.get_pass_chapter_facts(pass_id)) == 3


# ── cancel ──


@pytest.mark.asyncio
async def test_cancel_stops_loop(sp_env):
    memory_db, _ = sp_env
    await _seed(memory_db)

    holder: dict = {}

    async def _cancel_after_ch1(ch: int) -> None:
        if ch == 1:
            await holder["svc"].cancel(holder["pass_id"])

    svc = SourcePassService(llm=MockLLM(on_chapter=_cancel_after_ch1))
    holder["svc"] = svc
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        pass_id = await svc.start(NOVEL)
    holder["pass_id"] = pass_id
    await captured["coro"]

    row = await analysis_pass_store.get_pass(pass_id)
    assert row["status"] == "cancelled"
    assert len(await analysis_pass_store.get_pass_chapter_facts(pass_id)) == 1


# ── 一章失败不阻塞 ──


@pytest.mark.asyncio
async def test_chapter_failure_does_not_block(sp_env):
    memory_db, _ = sp_env
    await _seed(memory_db)

    llm = MockLLM(fail_on_chapter=2)
    svc = SourcePassService(llm=llm)
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        pass_id = await svc.start(NOVEL)
    await captured["coro"]

    row = await analysis_pass_store.get_pass(pass_id)
    assert row["status"] == "completed"  # 有成功章节即 completed

    cursor = await memory_db.execute(
        "SELECT c.chapter_num, p.status FROM pass_chapter_facts p"
        " JOIN chapters c ON c.id = p.chapter_id WHERE p.pass_id = ?"
        " ORDER BY c.chapter_num",
        (pass_id,),
    )
    statuses = [r["status"] for r in await cursor.fetchall()]
    assert statuses == ["completed", "failed", "completed"]  # 第 2 章失败不阻塞第 3 章

    # 失败章节的 history 记了错误;主表仍零写入
    entry = row["history_json"]["chapters"]["2"]
    assert "mock llm boom" in entry["error"]
    assert entry["completed_at"] is None
    assert await _table_count(memory_db, "chapter_facts") == 1
    # 失败章节:首次 + 重试共 2 次调用;3 章合计 4 次
    assert len(llm.calls) == 4


# ── 单活互斥(一审 ↔ 二审双向)──


@pytest.mark.asyncio
async def test_running_task_blocks_pass_start(sp_env):
    memory_db, _ = sp_env
    await _seed(memory_db)
    await analysis_task_store.create_task("task-1", NOVEL, 1, 3)

    svc = SourcePassService(llm=MockLLM())
    with pytest.raises(ValueError, match="active task"):
        await svc.start(NOVEL)


@pytest.mark.asyncio
async def test_active_pass_blocks_second_pass(sp_env):
    memory_db, _ = sp_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)

    svc = SourcePassService(llm=MockLLM())
    with pytest.raises(ValueError, match="active pass"):
        await svc.start(NOVEL)


@pytest.mark.asyncio
async def test_active_pass_blocks_main_analysis(sp_env):
    memory_db, _ = sp_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 3)

    svc = AnalysisService()
    with pytest.raises(ValueError, match="active pass"):
        await svc.start(NOVEL, 1, 3)


# ── D3: model_override client ──


def test_build_pass_llm_override():
    """model_override: 浅拷贝换 model,原 client 不受影响;默认沿用当前配置。"""
    from src.infra.llm_client import get_llm_client

    base = _build_pass_llm(None)
    assert base is get_llm_client()
    override = _build_pass_llm("other-model-7b")
    assert override is not base
    assert override.model == "other-model-7b"
    assert base.model != "other-model-7b"
