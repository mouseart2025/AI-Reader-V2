"""窗口化并发抽取 (issue #61) 集成测试。

覆盖:
- k=1 (默认串行) 与 k=3 (窗口化并发) 对同一本多章小说产出的
  ChapterFact 落库内容逐章一致 (k=1 行为等价性钉死);
- k=3 下抽取确实并发 (max inflight > 1),k=1 下严格串行;
- 进度广播使用"已完成计数"制且单调不减;
- ANALYSIS_CONCURRENCY 开关: 本地强制 1,云端上限 3;
- 暂停语义 = 合并器排空即停 (在飞章节完成合并,未开始章节不抽取),
  恢复后从 current_chapter+1 重扫补齐,最终产物完整。

全部使用 mock LLM + memory DB,不打真实 API、不写真实数据库。
"""

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.db.sqlite_db as sqlite_db
from src.db import (
    analysis_task_store,
    chapter_fact_store,
    entity_dictionary_store,
    novel_store,
    world_structure_override_store,
    world_structure_store,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services import embedding_service, visualization_service
from src.services.analysis_service import AnalysisService, manager

N_CHAPTERS = 6


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class MockLLM:
    """按章节号返回确定性事实 (人物N + 事件N);记录并发水位。

    gate: 非 gate_skip 章节的 generate 阻塞在 gate.wait() 上,
    用于暂停测试精确控制"在飞"章节。
    """

    def __init__(self, gate: asyncio.Event | None = None, gate_skip=frozenset()):
        self.gate = gate
        self.gate_skip = gate_skip
        self.inflight = 0
        self.max_inflight = 0

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        m = re.search(r"第(\d+)章", prompt)
        n = int(m.group(1)) if m else 0
        if self.gate is not None and n not in self.gate_skip:
            await self.gate.wait()
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(0.01)  # 让出事件循环,制造并发窗口
            return {
                "characters": [{"name": f"人物{n}"}],
                "relationships": [],
                "locations": [],
                "events": [
                    {
                        "summary": f"事件{n}",
                        "type": "其他",
                        "importance": "low",
                        "participants": [f"人物{n}"],
                    },
                ],
            }, LlmUsage(100, 50, 150)
        finally:
            self.inflight -= 1


@pytest.fixture
def win_env(memory_db, monkeypatch):
    """patch 全部 DB 访问到 memory_db;广播捕获到列表;关闭质量开关。"""
    async def _factory():
        return _NonClosing(memory_db)

    for mod in (
        chapter_fact_store,
        analysis_task_store,
        entity_dictionary_store,
        novel_store,
        world_structure_store,
        world_structure_override_store,
        visualization_service,
    ):
        monkeypatch.setattr(mod, "get_connection", _factory)
    # cost_service / world_structure_agent._rebuild_parent_votes 等函数内
    # 延迟导入 get_connection,patch 源头模块即可;analysis_service 顶部
    # 直接绑定的 get_connection (概要生成用) 也一并 patch
    monkeypatch.setattr(sqlite_db, "get_connection", _factory)
    import src.services.analysis_service as analysis_service_mod
    monkeypatch.setattr(analysis_service_mod, "get_connection", _factory)

    broadcasts: list[tuple[str, dict]] = []

    async def _capture(novel_id, data):
        broadcasts.append((novel_id, data))

    monkeypatch.setattr(manager, "broadcast", _capture)

    # 关闭质量开关,隔离被测行为(单遍抽取,无 recall/幻觉判定 LLM 调用)
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)

    # embedding 索引 (ChromaDB) 不出测试进程
    monkeypatch.setattr(embedding_service, "index_chapter", MagicMock())
    monkeypatch.setattr(
        embedding_service, "index_entities_from_fact", MagicMock(),
    )

    return {"db": memory_db, "broadcasts": broadcasts}


async def _seed(db, novel_id: str, n_chapters: int = N_CHAPTERS) -> None:
    await db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, "并发测试"),
    )
    for i in range(1, n_chapters + 1):
        await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content,"
            " analysis_status) VALUES (?, ?, ?, ?, ?)",
            (novel_id, i, f"第{i}章", f"第{i}章 人物{i}出场,发生了事件{i}。", "pending"),
        )
    await db.commit()


def _make_service(monkeypatch, mock_llm) -> AnalysisService:
    """AnalysisService + mock extractor;幻觉判定层短路为原样返回;
    世界结构更新与后置管线打桩(不在本测试覆盖范围)。"""
    from src.extraction.chapter_fact_extractor import ChapterFactExtractor

    svc = AnalysisService()
    svc.extractor = ChapterFactExtractor(llm=mock_llm)
    svc.scene_extractor = AsyncMock()
    svc.scene_extractor.extract = AsyncMock(return_value=[])

    async def _no_review(novel_id, chapter_num, fact, chapter_text, protected):
        return fact

    monkeypatch.setattr(svc, "_review_hallucinations", _no_review)

    async def _noop_world(world_agent, chapter_num, chapter_text, fact):
        return None

    monkeypatch.setattr(
        AnalysisService, "_update_world_structure", staticmethod(_noop_world),
    )
    # 完成后自动触发的后台管线 (geo/entity resolution) 不打桩会变真实任务
    svc._schedule_post_analysis = lambda novel_id: None
    return svc


async def _run(svc: AnalysisService, task_id: str, novel_id: str,
               start: int, end: int) -> None:
    await analysis_task_store.create_task(task_id, novel_id, start, end)
    svc._task_signals[task_id] = "running"
    await svc._run_loop(task_id, novel_id, start, end)


async def _facts_by_chapter(db, novel_id: str) -> dict[int, dict]:
    cursor = await db.execute(
        "SELECT c.chapter_num, cf.fact_json FROM chapter_facts cf"
        " JOIN chapters c ON c.id = cf.chapter_id"
        " WHERE cf.novel_id = ? ORDER BY c.chapter_num",
        (novel_id,),
    )
    rows = await cursor.fetchall()
    result = {}
    for row in rows:
        fact = json.loads(row["fact_json"])
        fact.pop("novel_id", None)  # 两本小说 id 不同,归一化后比较
        result[row["chapter_num"]] = fact
    return result


async def _statuses(db, novel_id: str) -> dict[int, str]:
    cursor = await db.execute(
        "SELECT chapter_num, analysis_status FROM chapters"
        " WHERE novel_id = ?",
        (novel_id,),
    )
    return {r["chapter_num"]: r["analysis_status"] for r in await cursor.fetchall()}


def test_concurrency_switch(monkeypatch):
    """ANALYSIS_CONCURRENCY: 默认/本地强制 1,云端上限 3,非法值回退 1。"""
    monkeypatch.delenv("ANALYSIS_CONCURRENCY", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert config.get_analysis_concurrency() == 1

    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "3")
    assert config.get_analysis_concurrency() == 3

    # 上限不超过云端全局 LLM 信号量 (3)
    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "99")
    assert config.get_analysis_concurrency() == 3

    # 本地 Ollama (全局信号量=1) 强制串行
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert config.get_analysis_concurrency() == 1

    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "abc")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert config.get_analysis_concurrency() == 1


@pytest.mark.asyncio
async def test_windowed_k3_facts_match_k1(win_env, monkeypatch):
    """k=1 与 k=3 落库 ChapterFact 逐章一致;k=3 确实并发抽取。"""
    memory_db = win_env["db"]
    broadcasts = win_env["broadcasts"]
    await _seed(memory_db, "novel-seq")
    await _seed(memory_db, "novel-par")

    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-chat")

    # ── k=1: 默认串行路径 ──
    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "1")
    assert config.get_analysis_concurrency() == 1
    llm_seq = MockLLM()
    svc_seq = _make_service(monkeypatch, llm_seq)
    await _run(svc_seq, "task-seq", "novel-seq", 1, N_CHAPTERS)
    assert llm_seq.max_inflight == 1

    # ── k=3: 窗口化并发路径 ──
    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "3")
    assert config.get_analysis_concurrency() == 3
    llm_par = MockLLM()
    svc_par = _make_service(monkeypatch, llm_par)
    await _run(svc_par, "task-par", "novel-par", 1, N_CHAPTERS)
    assert llm_par.max_inflight > 1  # 并发确实发生

    facts_seq = await _facts_by_chapter(memory_db, "novel-seq")
    facts_par = await _facts_by_chapter(memory_db, "novel-par")
    assert set(facts_seq) == set(range(1, N_CHAPTERS + 1))
    assert facts_par == facts_seq

    # sanity: 确实落库了 mock 的事实内容,不是空 fact 相等
    assert facts_seq[3]["characters"][0]["name"] == "人物3"

    # 两模式章节状态均为 completed
    for novel in ("novel-seq", "novel-par"):
        statuses = await _statuses(memory_db, novel)
        assert all(s == "completed" for s in statuses.values())

    # 进度: 已完成计数制,单调不减且最终 done == total
    for novel in ("novel-seq", "novel-par"):
        dones = [
            data["done"] for nid, data in broadcasts
            if nid == novel and data["type"] == "progress"
        ]
        assert dones, f"{novel} 无进度广播"
        assert dones == sorted(dones)
        assert dones[-1] == N_CHAPTERS


@pytest.mark.asyncio
async def test_windowed_pause_drains_then_resume(win_env, monkeypatch):
    """暂停 = 合并器排空即停: 在飞章节完成合并,未开始章节不抽取;
    恢复后从 current_chapter+1 重扫,全部章节最终 completed。"""
    memory_db = win_env["db"]
    novel = "novel-pause"
    await _seed(memory_db, novel)

    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "3")

    # 第 1 章不阻塞,其余章节阻塞在 gate 上 → 精确的"在飞"状态
    gate = asyncio.Event()
    llm = MockLLM(gate=gate, gate_skip={1})
    svc = _make_service(monkeypatch, llm)

    await analysis_task_store.create_task("task-p", novel, 1, N_CHAPTERS)
    svc._task_signals["task-p"] = "running"
    run = asyncio.create_task(svc._run_loop("task-p", novel, 1, N_CHAPTERS))

    # 等第 1 章完成合并
    for _ in range(500):
        await asyncio.sleep(0.01)
        if (await _statuses(memory_db, novel)).get(1) == "completed":
            break
    else:
        run.cancel()
        pytest.fail("chapter 1 未在预期时间内完成合并")

    # 暂停: 此时第 2、3 章在飞 (worker 阻塞在 gate),
    # 第 4 章可能已被空闲 worker 取走,5、6 章尚未开始抽取
    await svc.pause("task-p")
    gate.set()  # 放行在飞抽取,合并器排空后循环退出
    await asyncio.wait_for(run, timeout=15)

    task = await analysis_task_store.get_task("task-p")
    assert task["status"] == "paused"

    statuses = await _statuses(memory_db, novel)
    completed = {n for n, s in statuses.items() if s == "completed"}
    # 排空语义: 在飞的 2、3 章必定完成;未开始的 5、6 章绝不处理
    assert {1, 2, 3} <= completed <= {1, 2, 3, 4}
    assert statuses[5] == "pending"
    assert statuses[6] == "pending"
    # current_chapter = 严格按章序合并的连续前缀末端
    assert task["current_chapter"] == max(completed)

    # 恢复: 从 current_chapter+1 重扫 (force=False 跳过已完成章)
    svc._task_signals["task-p"] = "running"
    await analysis_task_store.update_task_status("task-p", "running")
    await svc._run_loop("task-p", novel, task["current_chapter"] + 1, N_CHAPTERS)

    final = await _statuses(memory_db, novel)
    assert all(s == "completed" for s in final.values())
    assert len(final) == N_CHAPTERS

    facts = await _facts_by_chapter(memory_db, novel)
    assert set(facts) == set(range(1, N_CHAPTERS + 1))
    for n, fact in facts.items():
        assert fact["characters"][0]["name"] == f"人物{n}"
