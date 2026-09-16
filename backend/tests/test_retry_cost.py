"""重试路径成本记账测试:重试烧的 token 必须按首试同口径计入章节记录。

覆盖:
- 手动重试 (_retry_failed_bg) 云端模式: cost_usd/cost_cny 非零,
  数值与首试公式一致 (provider 返回 usage × 模型定价), 且计入月度账本;
- 本地模式: token 仍记录,费用为 0,不产生月度分账。

全部使用 mock LLM + memory DB,不打真实 API、不写真实数据库。
"""

from unittest.mock import AsyncMock

import pytest

import src.db.sqlite_db as sqlite_db
from src.db import (
    analysis_task_store,
    chapter_fact_store,
    world_structure_store,
)
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services.analysis_service import AnalysisService, manager
from src.services.cost_service import get_monthly_usage

NOVEL = "novel-retry-cost"

CH1 = "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢。"

# 与 test_realtime_cost 同量级的 usage,保证费用经 4/6 位舍入后仍非零
USAGE = LlmUsage(50_000, 4_000, 54_000)

# deepseek-chat 定价: (0.27, 1.10) USD / 1M tokens
_CH_COST_USD = round(
    (USAGE.prompt_tokens / 1_000_000) * 0.27
    + (USAGE.completion_tokens / 1_000_000) * 1.10,
    6,
)


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class MockLLM:
    """固定返回 1 人物 + 1 事件,usage 为定值。"""

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        return {
            "characters": [{"name": "宋江"}],
            "relationships": [],
            "locations": [],
            "events": [
                {
                    "summary": "宋江武松结拜",
                    "type": "社交",
                    "importance": "low",
                    "participants": ["宋江", "武松"],
                    "location": "柴进庄",
                },
            ],
        }, LlmUsage(USAGE.prompt_tokens, USAGE.completion_tokens,
                    USAGE.total_tokens)


@pytest.fixture
def retry_env(memory_db, monkeypatch):
    """patch store/cost_service 的 get_connection 到 memory_db;广播静默。"""
    async def _factory():
        return _NonClosing(memory_db)

    for mod in (chapter_fact_store, analysis_task_store):
        monkeypatch.setattr(mod, "get_connection", _factory)
    # cost_service 在函数内 `from src.db.sqlite_db import get_connection`,
    # patch 源头模块即可
    monkeypatch.setattr(sqlite_db, "get_connection", _factory)
    # world_structure_store.load 读不到结构时返回 None(无层级注入)
    monkeypatch.setattr(
        world_structure_store, "load", AsyncMock(return_value=None),
    )

    async def _noop_broadcast(novel_id, data):
        return None

    monkeypatch.setattr(manager, "broadcast", _noop_broadcast)

    # 关闭质量开关,隔离被测行为(单遍抽取,无 recall/幻觉判定 LLM 调用)
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)

    return memory_db


async def _seed(memory_db) -> None:
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "重试成本测试"),
    )
    await memory_db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
        " analysis_status) VALUES (?, ?, ?, ?, ?, ?)",
        (1, NOVEL, 1, "第1章", CH1, "failed"),
    )
    await memory_db.commit()


def _make_service(monkeypatch) -> AnalysisService:
    """AnalysisService + mock extractor;幻觉判定层短路为原样返回。"""
    from src.extraction.chapter_fact_extractor import ChapterFactExtractor

    svc = AnalysisService()
    svc.extractor = ChapterFactExtractor(llm=MockLLM())
    svc.context_builder = AsyncMock()
    svc.context_builder.build = AsyncMock(return_value="ctx")

    async def _no_review(novel_id, chapter_num, fact, chapter_text, protected):
        return fact

    monkeypatch.setattr(svc, "_review_hallucinations", _no_review)
    return svc


@pytest.mark.asyncio
async def test_manual_retry_records_cost_cloud(retry_env, monkeypatch):
    """云端模式:重试成功章节的 cost_usd/cost_cny 非零,与首试公式一致,
    且累加进月度账本。"""
    memory_db = retry_env
    await _seed(memory_db)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-chat")

    svc = _make_service(monkeypatch)
    rows = [{"id": 1, "chapter_num": 1, "content": CH1}]
    await svc._retry_failed_bg(NOVEL, rows)

    cursor = await memory_db.execute(
        "SELECT cost_usd, cost_cny, input_tokens, output_tokens"
        " FROM chapter_facts WHERE novel_id = ? AND chapter_id = 1",
        (NOVEL,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["cost_usd"] == _CH_COST_USD
    assert row["cost_usd"] > 0
    assert row["cost_cny"] == round(_CH_COST_USD * 7.2, 4)
    assert row["input_tokens"] == USAGE.prompt_tokens
    assert row["output_tokens"] == USAGE.completion_tokens

    # 月度账本:与首试同口径累加
    monthly = await get_monthly_usage()
    assert monthly["usd"] == round(_CH_COST_USD, 4)
    assert monthly["input_tokens"] == USAGE.prompt_tokens
    assert monthly["output_tokens"] == USAGE.completion_tokens


@pytest.mark.asyncio
async def test_manual_retry_records_cost_local(retry_env, monkeypatch):
    """本地模式:token 仍记录,费用为 0,且不产生任何月度分账记录。"""
    memory_db = retry_env
    await _seed(memory_db)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")

    svc = _make_service(monkeypatch)
    rows = [{"id": 1, "chapter_num": 1, "content": CH1}]
    await svc._retry_failed_bg(NOVEL, rows)

    cursor = await memory_db.execute(
        "SELECT cost_usd, cost_cny, input_tokens"
        " FROM chapter_facts WHERE novel_id = ? AND chapter_id = 1",
        (NOVEL,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["cost_usd"] == 0.0
    assert row["cost_cny"] == 0.0
    assert row["input_tokens"] == USAGE.prompt_tokens

    cursor = await memory_db.execute(
        "SELECT key FROM app_settings WHERE key LIKE 'cost%'",
    )
    assert await cursor.fetchall() == []
