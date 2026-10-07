"""追加章节 (issue #69) 测试:编号续接 / 去重 / 分析续接 / 错误路径。

覆盖:
- 路由层 (api_client + memory DB): 404 / 400 / 409,文本与文件两种追加方式,
  章节编号从 max(chapter_num) 续接,去重跳过,全部重复时的明确信息,
  追加后已有章节与其 facts 零改动;
- 服务层 (mock LLM + memory DB): 追加后直接跑分析循环,已完成章节被跳过,
  只有新章调用 LLM,且 prompt 中的 context summary 正确续接前序 facts。

全部使用 mock LLM + memory DB,不打真实 API、不写真实数据库。
"""

import json
from unittest.mock import AsyncMock

import pytest

import src.db.sqlite_db as sqlite_db
from src.db import (
    analysis_task_store,
    chapter_fact_store,
    entity_dictionary_store,
    novel_store,
    world_structure_store,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services import embedding_service
from src.services.analysis_service import AnalysisService, manager

NOVEL = "novel-append"

# 章节标题按切分器实际产出形态(seed 模拟真实导入:切分器会剥掉「第X章」前缀)
CH1_TITLE = "结拜"
CH1 = "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢，意气相投。"
CH2_TITLE = "上山"
CH2 = "武松别了宋江，独自上了景阳冈。冈上寒风阵阵。"

# 追加文本:第三章新内容 + 与第二章完全重复的章节 + 第四章新内容
APPEND_TEXT = (
    "第三章 打虎\n"
    "武松在景阳冈上遇到一只吊睛白额大虫，三拳两脚将其打死。\n"
    f"第二章 {CH2_TITLE}\n{CH2}\n"
    "第四章 下山\n"
    "武松打虎之后名声大噪，知县赏了他一个都头的官职。\n"
)

ALL_DUP_TEXT = f"第一章 {CH1_TITLE}\n{CH1}\n第二章 {CH2_TITLE}\n{CH2}\n"


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


async def _seed_novel(db, *, with_facts: bool = False) -> None:
    """Seed 一部两章的小说;with_facts 时两章均 completed 且带 facts。"""
    await db.execute(
        "INSERT INTO novels (id, title, total_chapters, total_words, synopsis)"
        " VALUES (?, ?, 2, ?, ?)",
        (NOVEL, "追加测试", len(CH1) + len(CH2), "已有概要"),
    )
    status = "completed" if with_facts else "pending"
    await db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
        " word_count, analysis_status) VALUES (1, ?, 1, ?, ?, ?, ?)",
        (NOVEL, CH1_TITLE, CH1, len(CH1), status),
    )
    await db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
        " word_count, analysis_status) VALUES (2, ?, 2, ?, ?, ?, ?)",
        (NOVEL, CH2_TITLE, CH2, len(CH2), status),
    )
    if with_facts:
        for cid in (1, 2):
            fact = {
                "chapter_id": cid,
                "novel_id": NOVEL,
                "characters": [{"name": "宋江"}],
            }
            await db.execute(
                "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json,"
                " llm_model) VALUES (?, ?, ?, 'mock')",
                (NOVEL, cid, json.dumps(fact, ensure_ascii=False)),
            )
    await db.commit()


# ── 路由层测试 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_404_novel_not_found(api_client):
    client, _ = api_client
    resp = await client.post(
        "/api/novels/nope/chapters/append",
        data={"text": APPEND_TEXT},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_append_400_empty_input(api_client):
    client, db = api_client
    await _seed_novel(db)

    # 完全不带内容
    resp = await client.post(f"/api/novels/{NOVEL}/chapters/append")
    assert resp.status_code == 400

    # 空白文本
    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        data={"text": "   \n  "},
    )
    assert resp.status_code == 400

    # 空文件
    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        files={"file": ("a.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400

    # 不支持的扩展名
    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        files={"file": ("a.pdf", b"x", "application/pdf")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_append_409_analysis_running(api_client):
    client, db = api_client
    await _seed_novel(db)
    await db.execute(
        "INSERT INTO analysis_tasks (id, novel_id, status, chapter_start,"
        " chapter_end) VALUES ('t1', ?, 'running', 1, 2)",
        (NOVEL,),
    )
    await db.commit()
    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        data={"text": APPEND_TEXT},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_append_text_continues_numbering_and_dedupes(api_client):
    """追加文本:编号从 max(chapter_num) 续接,重复章节被跳过,
    已有章节数据零改动,新章为 pending,novel 统计字段更新。"""
    client, db = api_client
    await _seed_novel(db, with_facts=True)

    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        data={"text": APPEND_TEXT},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 2
    assert body["skipped"] == 1  # 与第二章内容完全重复的章节被跳过
    assert body["total_chapters"] == 4
    assert "跳过 1 章重复" in body["message"]
    assert [c["chapter_num"] for c in body["chapters"]] == [3, 4]

    cur = await db.execute(
        "SELECT chapter_num, title, analysis_status FROM chapters"
        " WHERE novel_id = ? ORDER BY chapter_num",
        (NOVEL,),
    )
    rows = await cur.fetchall()
    assert [r["chapter_num"] for r in rows] == [1, 2, 3, 4]
    assert rows[2]["title"] == "打虎"  # 切分器剥掉「第三章」前缀
    # 已有章节保持 completed,新章为 pending
    assert [r["analysis_status"] for r in rows] == [
        "completed",
        "completed",
        "pending",
        "pending",
    ]

    # 已有 facts 零改动
    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM chapter_facts WHERE novel_id = ?",
        (NOVEL,),
    )
    assert (await cur.fetchone())["n"] == 2

    # novels 统计字段更新
    novel = await novel_store.get_novel(NOVEL)
    assert novel["total_chapters"] == 4
    assert novel["total_words"] > len(CH1) + len(CH2)


@pytest.mark.asyncio
async def test_append_file_upload(api_client):
    """文件方式追加与文本方式等价。"""
    client, db = api_client
    await _seed_novel(db)

    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        files={"file": ("more.txt", APPEND_TEXT.encode("utf-8"), "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 2
    assert body["skipped"] == 1
    assert body["total_chapters"] == 4


@pytest.mark.asyncio
async def test_append_all_duplicates_returns_clear_message(api_client):
    """全部重复:added=0,返回明确信息,章节数不变。"""
    client, db = api_client
    await _seed_novel(db)

    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        data={"text": ALL_DUP_TEXT},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 0
    assert body["skipped"] == 2
    assert body["total_chapters"] == 2
    assert "重复" in body["message"]

    cur = await db.execute(
        "SELECT COUNT(*) AS n FROM chapters WHERE novel_id = ?",
        (NOVEL,),
    )
    assert (await cur.fetchone())["n"] == 2


@pytest.mark.asyncio
async def test_append_dedupes_within_batch(api_client):
    """追加文本内部重复:相同标题的连续章节被切分器合并为同一章,
    最终只新增一章(不产生重复)。"""
    client, db = api_client
    await _seed_novel(db)

    dup_batch = (
        "第三章 打虎\n武松在景阳冈上打死大虫。\n第三章 打虎\n武松在景阳冈上打死大虫。\n"
    )
    resp = await client.post(
        f"/api/novels/{NOVEL}/chapters/append",
        data={"text": dup_batch},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 1
    assert body["total_chapters"] == 3


# ── 服务层:追加后分析只跑新章,context 续接前序 ──────────────


class MockLLM:
    """固定返回 1 人物,并记录每次调用的 system(context 注入处)与 prompt。"""

    def __init__(self):
        self.systems: list[str] = []
        self.prompts: list[str] = []

    async def generate(
        self,
        system,
        prompt,
        format=None,
        temperature=0.1,
        max_tokens=4096,
        timeout=120,
        num_ctx=None,
    ):
        self.systems.append(system)
        self.prompts.append(prompt)
        return {
            "characters": [{"name": "武松"}],
            "relationships": [],
            "locations": [],
            "events": [],
        }, LlmUsage(1000, 200, 1200)


@pytest.fixture
def append_env(memory_db, monkeypatch):
    """patch 分析链路各 store 的 get_connection 到 memory_db;广播静默。"""

    async def _factory():
        return _NonClosing(memory_db)

    for mod in (
        chapter_fact_store,
        analysis_task_store,
        entity_dictionary_store,
        novel_store,
    ):
        monkeypatch.setattr(mod, "get_connection", _factory)
    # novel_service.append_chapters / cost_service / synopsis 步的函数内
    # import 走源模块;analysis_service 在 import 时绑定了自己的引用
    monkeypatch.setattr(sqlite_db, "get_connection", _factory)
    monkeypatch.setattr(
        "src.services.analysis_service.get_connection",
        _factory,
    )
    monkeypatch.setattr(
        "src.services.analysis_service.invalidate_layout_cache",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        world_structure_store,
        "load",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        world_structure_store,
        "save",
        AsyncMock(return_value=None),
    )
    # 世界结构 LLM 增量更新与本测试无关,隔离真实 LLM 调用
    from src.services.world_structure_agent import WorldStructureAgent

    monkeypatch.setattr(
        WorldStructureAgent,
        "_run_llm_update",
        AsyncMock(return_value=None),
    )
    # embedding 索引是章内并行环节之一,隔离掉 ChromaDB 依赖
    monkeypatch.setattr(embedding_service, "index_chapter", lambda *a, **k: None)
    monkeypatch.setattr(
        embedding_service,
        "index_entities_from_fact",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        embedding_service,
        "build_fact_summary",
        lambda *a, **k: "",
    )

    async def _noop_broadcast(novel_id, data):
        return None

    monkeypatch.setattr(manager, "broadcast", _noop_broadcast)

    # 关闭质量开关:单遍抽取,每章恰好 1 次 LLM 调用
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")

    return memory_db


@pytest.mark.asyncio
async def test_analysis_after_append_only_runs_new_chapters(
    append_env,
    monkeypatch,
):
    """追加后直接跑分析循环(1..total,force=False):已完成章节跳过,
    只有新章调用 LLM;新章 prompt 的 context summary 含前序人物(续接正确);
    前序 facts 零改动。"""
    memory_db = append_env
    await _seed_novel(memory_db, with_facts=True)
    facts_before = [
        r["fact_json"]
        for r in await memory_db.execute_fetchall(
            "SELECT fact_json FROM chapter_facts WHERE novel_id = ?"
            " ORDER BY chapter_id",
            (NOVEL,),
        )
    ]

    # 走真实追加路径新增第三章
    from src.services import novel_service

    result = await novel_service.append_chapters(
        NOVEL,
        "第三章 打虎\n武松在景阳冈上遇到大虫，三拳两脚将其打死。",
    )
    assert result["added"] == 1
    assert result["chapters"][0]["chapter_num"] == 3

    # 种子任务行(循环内会更新进度/状态)
    await memory_db.execute(
        "INSERT INTO analysis_tasks (id, novel_id, status, chapter_start,"
        " chapter_end) VALUES ('task-append', ?, 'running', 1, 3)",
        (NOVEL,),
    )
    await memory_db.commit()

    from src.extraction.chapter_fact_extractor import ChapterFactExtractor

    llm = MockLLM()
    svc = AnalysisService()
    svc.extractor = ChapterFactExtractor(llm=llm)
    svc.scene_extractor = AsyncMock()
    svc.scene_extractor.extract = AsyncMock(return_value=[])
    svc._schedule_post_analysis = lambda novel_id: None

    async def _no_review(novel_id, chapter_num, fact, chapter_text, protected):
        return fact

    monkeypatch.setattr(svc, "_review_hallucinations", _no_review)

    svc._task_signals["task-append"] = "running"
    # 真实 ContextSummaryBuilder:context 从 memory DB 的前序 facts 构建
    await svc._run_loop_inner("task-append", NOVEL, 1, 3, force=False)

    # 只有第三章调用了 LLM(前两章 completed 被跳过)
    assert len(llm.systems) == 1
    # context summary 续接前序:前章人物「宋江」经 system 注入新章上下文
    assert "宋江" in llm.systems[0]

    # 新章已完成并落 facts;前序 facts 逐字节未变
    cur = await memory_db.execute(
        "SELECT analysis_status FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
        (NOVEL,),
    )
    assert [r["analysis_status"] for r in await cur.fetchall()] == [
        "completed",
        "completed",
        "completed",
    ]
    facts_after = [
        r["fact_json"]
        for r in await memory_db.execute_fetchall(
            "SELECT fact_json FROM chapter_facts WHERE novel_id = ?"
            " ORDER BY chapter_id",
            (NOVEL,),
        )
    ]
    assert len(facts_after) == 3
    assert facts_after[:2] == facts_before
