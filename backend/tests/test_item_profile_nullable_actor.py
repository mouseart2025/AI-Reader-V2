"""aggregate_item 对 actor 为空的 item_event 不得崩(issue #70 反馈的百科物品详情 500)。

抽取层 ItemEventFact.actor 可空(「出现/遗失/被发现」类动作无行为者),
聚合模型 ItemFlowEntry.actor 必须同样可空,否则 pydantic ValidationError → 路由 500。
"""

import json

import pytest
import pytest_asyncio
from unittest.mock import patch

from src.services import entity_aggregator

NOVEL = "test-item-null-actor"


@pytest_asyncio.fixture
async def item_db(memory_db):
    """种子:一章,一条 actor 为 null 的 item_event + 一条正常 item_event。"""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    fact = {
        "characters": [{"name": "孙悟空"}],
        "item_events": [
            # 无行为者:抽取层合法(actor=None)
            {"item_name": "金箍棒", "item_type": "兵器", "action": "遗失"},
            {"item_name": "金箍棒", "item_type": "兵器", "action": "获得", "actor": "孙悟空"},
        ],
    }
    await memory_db.execute("INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试"))
    cur = await memory_db.execute(
        "INSERT INTO chapters (novel_id, chapter_num, title, content) VALUES (?, ?, ?, ?)",
        (NOVEL, 1, "第一回", "正文……"),
    )
    await memory_db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) VALUES (?, ?, ?)",
        (NOVEL, cur.lastrowid, json.dumps(fact, ensure_ascii=False)),
    )
    await memory_db.commit()

    with (
        patch("src.services.entity_aggregator.get_connection", factory),
        patch("src.services.alias_resolver.get_connection", factory),
        patch("src.db.entity_override_store.get_connection", factory),
        patch("src.db.chapter_fact_store.get_connection", factory),
    ):
        yield memory_db


@pytest.mark.asyncio
async def test_aggregate_item_tolerates_null_actor(item_db):
    """含 null actor 的物品详情正常返回:该条 flow 的 actor 为 None,其余不受影响。"""
    profile = await entity_aggregator.aggregate_item(NOVEL, "金箍棒")

    assert profile.name == "金箍棒"
    by_action = {f.action: f for f in profile.flow}
    assert by_action["遗失"].actor is None
    assert by_action["获得"].actor == "孙悟空"
