"""multi-pass Epic 3 Story 3.2 测试: pass API 路由全流程。

路由层直接调用(参照 test_entity_overrides.py 模式),memory_db +
mock get_connection;LLM 用 MockLLM(参照 test_source_pass_service.py)。

覆盖 spec 验收:
- 全流程:启动 → 进度可查(GET 列表)→ 完成 → diff 可拉 → 删除后影子数据
  清除且主表不变;
- 前置校验 409:未完成一审 / 有活动任务 / 有活动 pass;400:非法 kind;
- pause/resume/cancel 状态机 409;delete 活动 pass 409;
- diff 首次生成回填 history(air_unlocked_at + diff_counts)。
"""

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import src.api.routes.analysis_passes as routes
from src.api.routes.analysis_passes import (
    AdjudicationRequest,
    StartPassRequest,
    cancel_pass,
    delete_pass,
    get_chapter_diff,
    list_passes,
    pause_pass,
    record_adjudication,
    resume_pass,
    start_pass,
)
from src.db import (
    analysis_pass_store,
    analysis_task_store,
    chapter_fact_store,
    chapter_store,
    entity_dictionary_store,
    novel_store,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services import embedding_service
from src.services.analysis_service import manager
from src.services.pass_diff_service import PassDiffService
from src.services.source_pass_service import SourcePassService

NOVEL = "novel-api"

CH1 = "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢。"
CH2 = "次日宋江辞别柴进,独自上路投青州去了。"


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class MockLLM:
    """与 test_source_pass_service 同型:按「## 第 N 章」识别章节。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.calls.append((system, prompt))
        m = re.search(r"## 第 (\d+) 章", prompt)
        ch = int(m.group(1)) if m else 0
        return {
            "characters": [{"name": "宋江"}, {"name": "武松"}],
            "relationships": [
                {"person_a": "宋江", "person_b": "武松",
                 "relation_type": "结拜兄弟"},
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
def api_env(memory_db, monkeypatch):
    """patch 全部相关 store 的 get_connection + 广播 + 别名 + Chroma;
    注入 MockLLM 的 SourcePassService 与独立 PassDiffService 实例。"""

    async def _factory():
        return _NonClosing(memory_db)

    for mod in (
        analysis_pass_store, analysis_task_store, chapter_store,
        chapter_fact_store, entity_dictionary_store, novel_store,
    ):
        monkeypatch.setattr(mod, "get_connection", _factory)
    monkeypatch.setattr(
        "src.extraction.context_summary_builder.get_all_chapter_facts",
        AsyncMock(side_effect=AssertionError("二审不应读 chapter_facts 主表")),
    )
    monkeypatch.setattr(
        "src.services.pass_diff_service.build_alias_map",
        AsyncMock(return_value={}),
    )

    broadcasts: list[dict] = []

    async def _capture(novel_id, data):
        broadcasts.append(data)

    monkeypatch.setattr(manager, "broadcast", _capture)
    monkeypatch.setattr(embedding_service, "index_chapter", MagicMock())
    monkeypatch.setattr(
        embedding_service, "index_entities_from_fact", MagicMock(),
    )
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)

    llm = MockLLM()
    pass_service = SourcePassService(llm=llm)
    diff_service = PassDiffService()
    monkeypatch.setattr(routes, "get_source_pass_service", lambda: pass_service)
    monkeypatch.setattr(routes, "get_pass_diff_service", lambda: diff_service)
    return memory_db, broadcasts, llm, pass_service, diff_service


async def _seed(memory_db, analysis_status: str = "completed") -> None:
    await memory_db.execute(
        "INSERT INTO novels (id, title, total_chapters) VALUES (?, '测试小说', 2)",
        (NOVEL,),
    )
    for i, content in ((1, CH1), (2, CH2)):
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
            " analysis_status) VALUES (?, ?, ?, ?, ?, ?)",
            (i, NOVEL, i, f"第{i}章", content, analysis_status),
        )
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
            " VALUES (?, ?, ?)",
            (NOVEL, i, json.dumps(
                {"chapter_id": i, "novel_id": NOVEL, "characters": [
                    {"name": "一审基线人物"},
                ]}, ensure_ascii=False,
            )),
        )
    await memory_db.commit()


def _start_capture():
    """捕获 service.start 内的后台循环协程,便于手动驱动。"""
    captured: dict = {}

    def fake_create_task(coro, **kwargs):
        captured["coro"] = coro
        return object()

    return captured, fake_create_task


async def _table_count(memory_db, table: str) -> int:
    cursor = await memory_db.execute(f"SELECT COUNT(*) AS n FROM {table}")
    return (await cursor.fetchone())["n"]


async def _wait_pass_status(pass_id: str, status: str) -> dict:
    for _ in range(200):
        await asyncio.sleep(0.01)
        row = await analysis_pass_store.get_pass(pass_id)
        if row["status"] == status:
            return row
    raise AssertionError(f"pass 未进入 {status}: {row['status']}")


# ── 全流程:启动 → 进度 → 完成 → diff → 删除 ──


@pytest.mark.asyncio
async def test_full_flow(api_env):
    memory_db, broadcasts, llm, _svc, _diff = api_env
    await _seed(memory_db)
    main_facts_before = await _table_count(memory_db, "chapter_facts")

    # 1. 启动
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        res = await start_pass(NOVEL, StartPassRequest(model_override="qwen2:7b"))
    pass_id = res["pass_id"]
    assert res["status"] == "running"

    # 2. 进度可查(循环尚未驱动,current_chapter=0,列表含 history 骨架)
    listing = await list_passes(NOVEL)
    assert [p["id"] for p in listing["passes"]] == [pass_id]
    row = listing["passes"][0]
    assert row["status"] == "running"
    assert row["history_json"]["model"] == "qwen2:7b"
    assert row["history_json"]["source_only_completed_at"] is None

    # 3. 完成
    await captured["coro"]
    row = (await list_passes(NOVEL))["passes"][0]
    assert row["status"] == "completed"
    assert row["current_chapter"] == 2
    assert row["history_json"]["source_only_completed_at"] is not None

    # 4. diff:把主表 fact 换成与二审一致的 → 空 diff;
    #    再给二审加一个人物 → only_in_pass=1,并回填 history
    pass_facts = await analysis_pass_store.get_pass_chapter_facts(pass_id)
    ch1_fact = next(f for f in pass_facts if f["chapter_num"] == 1)["fact"]
    await memory_db.execute(
        "UPDATE chapter_facts SET fact_json = ? WHERE novel_id = ? AND chapter_id = 1",
        (json.dumps(ch1_fact, ensure_ascii=False), NOVEL),
    )
    await memory_db.commit()

    diff1 = await get_chapter_diff(NOVEL, pass_id, chapter=1)
    assert diff1["cached"] is False
    assert diff1["counts"] == {"only_in_main": 0, "only_in_pass": 0,
                               "different": 0}
    entry = (await analysis_pass_store.get_pass(pass_id))[
        "history_json"]["chapters"]["1"]
    assert entry["air_unlocked_at"] is not None
    assert entry["diff_counts"] == {"air_only": 0, "pass_only": 0,
                                    "different": 0}

    # 二审该章加一个人物 → only_in_pass=1(缓存键含内容 hash,自动重算)
    ch1_fact["characters"].append({"name": "二审独有角色"})
    from src.models.chapter_fact import ChapterFact
    await analysis_pass_store.upsert_pass_chapter_fact(
        pass_id, 1, ChapterFact.model_validate(ch1_fact),
    )
    diff2 = await get_chapter_diff(NOVEL, pass_id, chapter=1)
    assert diff2["counts"]["only_in_pass"] == 1
    assert diff2["only_in_pass"][0]["item"]["name"] == "二审独有角色"
    entry = (await analysis_pass_store.get_pass(pass_id))[
        "history_json"]["chapters"]["1"]
    assert entry["diff_counts"] == {"air_only": 0, "pass_only": 1,
                                    "different": 0}

    # 5. 删除:影子数据级联清除,主表不变
    res = await delete_pass(NOVEL, pass_id)
    assert res["status"] == "ok"
    assert (await list_passes(NOVEL))["passes"] == []
    assert await _table_count(memory_db, "pass_chapter_facts") == 0
    assert await _table_count(memory_db, "analysis_passes") == 0
    assert await _table_count(memory_db, "chapter_facts") == main_facts_before


# ── pause / resume / cancel ──


@pytest.mark.asyncio
async def test_pause_resume_via_api(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)

    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        res = await start_pass(NOVEL, StartPassRequest())
    pass_id = res["pass_id"]
    # 循环未驱动:直接 pause(状态机: running → paused)
    assert (await pause_pass(NOVEL, pass_id))["status"] == "paused"
    # resume:走真实 create_task,循环跑完
    assert (await resume_pass(NOVEL, pass_id))["status"] == "running"
    captured["coro"].close()  # 首次 start 捕获的循环从未启动,避免 GC 警告
    row = await _wait_pass_status(pass_id, "completed")
    assert row["current_chapter"] == 2
    # 结束后不能再操作
    with pytest.raises(HTTPException) as exc:
        await pause_pass(NOVEL, pass_id)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await resume_pass(NOVEL, pass_id)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await cancel_pass(NOVEL, pass_id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_via_api(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)

    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        res = await start_pass(NOVEL, StartPassRequest())
    pass_id = res["pass_id"]
    assert (await cancel_pass(NOVEL, pass_id))["status"] == "cancelled"
    captured["coro"].close()  # 循环从未启动
    # cancelled 后可删除
    assert (await delete_pass(NOVEL, pass_id))["status"] == "ok"


@pytest.mark.asyncio
async def test_delete_active_pass_rejected(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        res = await start_pass(NOVEL, StartPassRequest())
    with pytest.raises(HTTPException) as exc:
        await delete_pass(NOVEL, res["pass_id"])
    assert exc.value.status_code == 409
    captured["coro"].close()  # 循环从未启动


# ── 前置校验 ──


@pytest.mark.asyncio
async def test_start_rejects_unfinished_main_analysis(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db, analysis_status="pending")
    with pytest.raises(HTTPException) as exc:
        await start_pass(NOVEL, StartPassRequest())
    assert exc.value.status_code == 409
    assert "一审" in exc.value.detail


@pytest.mark.asyncio
async def test_start_rejects_active_task(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    await analysis_task_store.create_task("task-1", NOVEL, 1, 2)
    with pytest.raises(HTTPException) as exc:
        await start_pass(NOVEL, StartPassRequest())
    assert exc.value.status_code == 409
    assert "一审" in exc.value.detail


@pytest.mark.asyncio
async def test_start_rejects_active_pass(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 2)
    with pytest.raises(HTTPException) as exc:
        await start_pass(NOVEL, StartPassRequest())
    assert exc.value.status_code == 409
    assert "二审" in exc.value.detail


@pytest.mark.asyncio
async def test_start_rejects_unknown_kind_and_novel(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    with pytest.raises(HTTPException) as exc:
        await start_pass(NOVEL, StartPassRequest(kind="review_pass"))
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await start_pass("no-such-novel", StartPassRequest())
    assert exc.value.status_code == 404
    assert exc.value.detail == "小说不存在"


@pytest.mark.asyncio
async def test_diff_404s(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 2)

    with pytest.raises(HTTPException) as exc:
        await get_chapter_diff(NOVEL, "no-such-pass", chapter=1)
    assert exc.value.status_code == 404
    # pass 属于别的小说 → 404(不泄漏)
    with pytest.raises(HTTPException) as exc:
        await get_chapter_diff("other-novel", "p1", chapter=1)
    assert exc.value.status_code == 404
    # 二审未覆盖该章 → 404
    with pytest.raises(HTTPException) as exc:
        await get_chapter_diff(NOVEL, "p1", chapter=1)
    assert exc.value.status_code == 404
    assert "尚未覆盖" in exc.value.detail
    # 章节不存在 → 404
    with pytest.raises(HTTPException) as exc:
        await get_chapter_diff(NOVEL, "p1", chapter=99)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_pass_not_found_404(api_env):
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    for action in (pause_pass, resume_pass, cancel_pass, delete_pass):
        with pytest.raises(HTTPException) as exc:
            await action(NOVEL, "no-such-pass")
        assert exc.value.status_code == 404
        assert exc.value.detail == "二审任务不存在"


# ── 人工裁决(Epic 4 Story 4.2):只写 history 埋点,不改正式结果 ──


@pytest.mark.asyncio
async def test_adjudication_flow(api_env):
    """裁决计数累加 + 明细落 log;不改 chapter_facts / pass_chapter_facts。"""
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 2)
    main_facts_before = await _table_count(memory_db, "chapter_facts")
    pass_facts_before = await _table_count(memory_db, "pass_chapter_facts")

    res = await record_adjudication(
        NOVEL, "p1",
        AdjudicationRequest(chapter=1, entry_id="different:characters:宋江",
                            verdict="accept_main"),
    )
    assert res["status"] == "ok"
    assert res["adjudication"] == {"confirmed": 1, "rejected": 0, "neither": 0}

    # 同章再次裁决:计数累加、明细追加;主表/影子表行数不变
    await record_adjudication(
        NOVEL, "p1",
        AdjudicationRequest(chapter=1, entry_id="only_in_pass:characters:武松",
                            verdict="accept_pass"),
    )
    res = await record_adjudication(
        NOVEL, "p1",
        AdjudicationRequest(chapter=2, entry_id="different:events:#1↔#1",
                            verdict="neither"),
    )
    assert res["adjudication"] == {"confirmed": 0, "rejected": 0, "neither": 1}

    chapters = (await analysis_pass_store.get_pass("p1"))[
        "history_json"]["chapters"]
    ch1 = chapters["1"]
    assert ch1["adjudication"] == {"confirmed": 1, "rejected": 1, "neither": 0}
    assert [e["verdict"] for e in ch1["adjudication_log"]] == [
        "accept_main", "accept_pass",
    ]
    assert all(e["at"] for e in ch1["adjudication_log"])
    assert chapters["2"]["adjudication"]["neither"] == 1
    # 裁决只写 history 埋点
    assert await _table_count(memory_db, "chapter_facts") == main_facts_before
    assert await _table_count(memory_db, "pass_chapter_facts") == pass_facts_before


@pytest.mark.asyncio
async def test_adjudication_validation(api_env):
    """非法 verdict / 范围外章节 400;不存在或跨小说的 pass 404。"""
    memory_db, _b, _llm, _svc, _d = api_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 2)

    with pytest.raises(HTTPException) as exc:
        await record_adjudication(
            NOVEL, "p1",
            AdjudicationRequest(chapter=1, entry_id="x", verdict="overwrite_main"),
        )
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await record_adjudication(
            NOVEL, "p1",
            AdjudicationRequest(chapter=3, entry_id="x", verdict="accept_main"),
        )
    assert exc.value.status_code == 400
    assert "范围" in exc.value.detail

    with pytest.raises(HTTPException) as exc:
        await record_adjudication(
            NOVEL, "no-such-pass",
            AdjudicationRequest(chapter=1, entry_id="x", verdict="accept_main"),
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await record_adjudication(
            "other-novel", "p1",
            AdjudicationRequest(chapter=1, entry_id="x", verdict="accept_main"),
        )
    assert exc.value.status_code == 404
