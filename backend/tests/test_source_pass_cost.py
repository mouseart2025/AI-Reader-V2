"""multi-pass Epic 5 Story 5.2 测试: 二审成本分账 (usage 按 pass 维度区分)。

覆盖验收:
- cost_service scope 分账: source_pass 写独立月度 key (cost_pass_*),
  一审默认 key (cost_*) 不受影响;
- SourcePassService 每章 usage 落 history 埋点 (token 始终记,费用仅云端);
- 云端模式: 二审费用记独立月度分账,一审月度合计不混入;
- 本地模式: token 仍记录,费用为 0,月度分账不产生;
- list_passes API 输出 cost_summary;settings /budget 输出二审分账字段。

全部使用 mock LLM + memory DB,不打真实 API、不写真实数据库。
"""

import json
import re
from unittest.mock import AsyncMock, patch

import pytest

import src.db.sqlite_db as sqlite_db
from src.api.routes.analysis_passes import list_passes
from src.api.routes.settings import get_budget
from src.db import (
    analysis_pass_store,
    analysis_task_store,
    chapter_store,
    entity_dictionary_store,
    novel_store,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services import embedding_service
from src.services.analysis_service import manager
from src.services.cost_service import (
    SCOPE_SOURCE_PASS,
    add_monthly_usage,
    get_monthly_usage,
)
from src.services.source_pass_service import SourcePassService

NOVEL = "novel-cost"

CH1 = "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢。"
CH2 = "次日宋江辞别柴进,独自上路投青州去了。"

# 与 test_realtime_cost 同量级的 usage,保证费用经 4/6 位舍入后仍非零
USAGE = LlmUsage(50_000, 4_000, 54_000)


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class MockLLM:
    """与 test_source_pass_service 同型:按「## 第 N 章」识别章节。"""

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        m = re.search(r"## 第 (\d+) 章", prompt)
        ch = int(m.group(1)) if m else 0
        return {
            "characters": [{"name": "宋江"}],
            "relationships": [],
            "locations": [],
            "events": [
                {
                    "summary": f"第{ch}章事件",
                    "type": "其他",
                    "importance": "low",
                    "participants": ["宋江"],
                    "location": "柴进庄",
                },
            ],
        }, LlmUsage(USAGE.prompt_tokens, USAGE.completion_tokens,
                    USAGE.total_tokens)


@pytest.fixture
def cost_env(memory_db, monkeypatch):
    """patch 全部相关 store 与 cost_service 的 get_connection 到 memory_db;
    广播静默;Chroma 零写入。"""
    async def _factory():
        return _NonClosing(memory_db)

    for mod in (
        analysis_pass_store, analysis_task_store, chapter_store,
        entity_dictionary_store, novel_store,
    ):
        monkeypatch.setattr(mod, "get_connection", _factory)
    # cost_service 在函数内 `from src.db.sqlite_db import get_connection`,
    # patch 源头模块即可
    monkeypatch.setattr(sqlite_db, "get_connection", _factory)
    monkeypatch.setattr(
        "src.extraction.context_summary_builder.get_all_chapter_facts",
        AsyncMock(side_effect=AssertionError("二审不应读 chapter_facts 主表")),
    )

    async def _noop_broadcast(novel_id, data):
        return None

    monkeypatch.setattr(manager, "broadcast", _noop_broadcast)
    monkeypatch.setattr(embedding_service, "index_chapter", AsyncMock())
    monkeypatch.setattr(embedding_service, "index_entities_from_fact", AsyncMock())

    # 关闭质量开关,隔离被测行为
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)

    return memory_db


async def _seed(memory_db) -> None:
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "成本测试小说"),
    )
    for i, content in ((1, CH1), (2, CH2)):
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content)"
            " VALUES (?, ?, ?, ?, ?)",
            (i, NOVEL, i, f"第{i}章", content),
        )
    await memory_db.commit()


def _start_capture():
    """捕获 asyncio.create_task 的后台循环协程以便手动驱动(同
    test_source_pass_service._start_capture)。"""
    captured: dict = {}

    def fake_create_task(coro, **kwargs):
        captured["coro"] = coro
        return object()

    return captured, fake_create_task


# deepseek-chat 定价: (0.27, 1.10) USD / 1M tokens
_CH_COST_USD = round(
    (USAGE.prompt_tokens / 1_000_000) * 0.27
    + (USAGE.completion_tokens / 1_000_000) * 1.10,
    6,
)


# ── cost_service scope 分账 ──


@pytest.mark.asyncio
async def test_monthly_usage_scope_separation(cost_env):
    """source_pass scope 写独立月度 key;一审默认 key 不受影响。"""
    await add_monthly_usage(0.01, 0.07, 1000, 200)  # 一审(默认)
    await add_monthly_usage(0.02, 0.14, 2000, 400, scope=SCOPE_SOURCE_PASS)

    main = await get_monthly_usage()
    pas = await get_monthly_usage(scope=SCOPE_SOURCE_PASS)

    assert main == {
        "usd": 0.01, "cny": 0.07, "input_tokens": 1000, "output_tokens": 200,
    }
    assert pas == {
        "usd": 0.02, "cny": 0.14, "input_tokens": 2000, "output_tokens": 400,
    }

    # 两个 key 物理隔离
    cursor = await cost_env.execute(
        "SELECT key FROM app_settings WHERE key LIKE 'cost%' ORDER BY key",
    )
    keys = [r["key"] for r in await cursor.fetchall()]
    assert len(keys) == 2
    assert any(k.startswith("cost_") and not k.startswith("cost_pass_") for k in keys)
    assert any(k.startswith("cost_pass_") for k in keys)


@pytest.mark.asyncio
async def test_monthly_usage_empty_scope_defaults_zero(cost_env):
    """未发生二审时,二审分账为零,一审合计不受影响。"""
    await add_monthly_usage(0.05, 0.36, 5000, 500)
    pas = await get_monthly_usage(scope=SCOPE_SOURCE_PASS)
    assert pas == {"usd": 0.0, "cny": 0.0, "input_tokens": 0, "output_tokens": 0}


# ── SourcePassService 成本记账 ──


@pytest.mark.asyncio
async def test_pass_run_records_usage_cloud(cost_env, monkeypatch):
    """云端模式: 每章 usage 落 history;二审费用记独立月度 key,一审合计不混入。"""
    memory_db = cost_env
    await _seed(memory_db)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")

    svc = SourcePassService(llm=MockLLM())
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        pass_id = await svc.start(NOVEL, model_override="deepseek-chat")
    await captured["coro"]

    row = await analysis_pass_store.get_pass(pass_id)
    assert row["status"] == "completed"

    # 每章 history 埋点带 usage(token + 费用 + 实际模型)
    chapters = row["history_json"]["chapters"]
    assert set(chapters) == {"1", "2"}
    for num in ("1", "2"):
        u = chapters[num]["usage"]
        assert u["input_tokens"] == USAGE.prompt_tokens
        assert u["output_tokens"] == USAGE.completion_tokens
        assert u["cost_usd"] == _CH_COST_USD
        assert u["cost_cny"] == round(_CH_COST_USD * 7.2, 4)
        assert u["model"] == "deepseek-chat"

    # 月度分账: 二审进 cost_pass_*,一审 cost_* 保持零
    pas = await get_monthly_usage(scope=SCOPE_SOURCE_PASS)
    assert pas["input_tokens"] == 2 * USAGE.prompt_tokens
    assert pas["output_tokens"] == 2 * USAGE.completion_tokens
    assert pas["usd"] > 0
    main = await get_monthly_usage()
    assert main == {"usd": 0.0, "cny": 0.0, "input_tokens": 0, "output_tokens": 0}


@pytest.mark.asyncio
async def test_pass_run_records_usage_local(cost_env, monkeypatch):
    """本地模式: token 仍记录,费用为 0,且不产生任何月度分账记录。"""
    memory_db = cost_env
    await _seed(memory_db)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")

    svc = SourcePassService(llm=MockLLM())
    captured, fake_create_task = _start_capture()
    with patch("asyncio.create_task", fake_create_task):
        pass_id = await svc.start(NOVEL)
    await captured["coro"]

    row = await analysis_pass_store.get_pass(pass_id)
    assert row["status"] == "completed"
    u = row["history_json"]["chapters"]["1"]["usage"]
    assert u["input_tokens"] == USAGE.prompt_tokens
    assert u["cost_usd"] == 0.0
    assert u["cost_cny"] == 0.0

    cursor = await memory_db.execute(
        "SELECT key FROM app_settings WHERE key LIKE 'cost%'",
    )
    assert await cursor.fetchall() == []


# ── API 输出区分 ──


@pytest.mark.asyncio
async def test_list_passes_includes_cost_summary(cost_env):
    """list_passes 输出 cost_summary(由 history 埋点聚合)。"""
    memory_db = cost_env
    await _seed(memory_db)
    await analysis_pass_store.create_pass(
        "p-cost", NOVEL, 1, 2, model_name="deepseek-chat",
    )
    await analysis_pass_store.update_chapter_history("p-cost", 1, {
        "usage": {
            "input_tokens": 50_000, "output_tokens": 4_000,
            "cost_usd": 0.0179, "cost_cny": 0.1289, "model": "deepseek-chat",
        },
    })
    await analysis_pass_store.update_chapter_history("p-cost", 2, {
        "usage": {
            "input_tokens": 50_000, "output_tokens": 4_000,
            "cost_usd": 0.0179, "cost_cny": 0.1289, "model": "deepseek-chat",
        },
    })

    result = await list_passes(NOVEL)
    p = result["passes"][0]
    assert p["cost_summary"] == {
        "billed_chapters": 2,
        "input_tokens": 100_000,
        "output_tokens": 8_000,
        "cost_usd": round(0.0179 * 2, 4),
        "cost_cny": round(0.1289 * 2, 2),
    }


@pytest.mark.asyncio
async def test_budget_endpoint_separates_pass_usage(cost_env):
    """settings /budget: 一审合计与二审分账分别输出,互不混入。"""
    await add_monthly_usage(0.10, 0.72, 10_000, 1_000)
    await add_monthly_usage(0.03, 0.22, 3_000, 300, scope=SCOPE_SOURCE_PASS)

    result = await get_budget()
    assert result["monthly_used_cny"] == 0.72
    assert result["monthly_input_tokens"] == 10_000
    assert result["monthly_pass_used_cny"] == 0.22
    assert result["monthly_pass_input_tokens"] == 3_000
