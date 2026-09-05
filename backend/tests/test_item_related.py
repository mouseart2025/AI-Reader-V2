"""issue #70 item relation 缺陷修复测试。

缺陷 A(语义过宽):related_items 原先由「同章共现」启发式产生——同场景偶然
共现、同类别、同场战斗的不同人物持有物都会被错标为关联物品。修复后
related_items 只来自 LLM 显式记录的 item_events[].related(须附原文证据)。

缺陷 B(target-type 泄漏):related item 目标必须是 item 类型实体;领域/能力
机制/人物/地点/概念类非物品实体在验证层(章内判据)与聚合层(全书物品集合)
两道防线剔除。

覆盖:
- FactValidator: 非物品端被拦 / 无 evidence 被拦 / 自引用与重复剔除 /
  正常组成、持有转移伴随关系保留
- aggregate_item: 同章共现不再产生 related / 显式关系双向保留 /
  仅概念端(从未作为物品出现)在读取侧被拦
"""

import json

import pytest
import pytest_asyncio
from unittest.mock import patch

from src.extraction.fact_validator import FactValidator
from src.models.chapter_fact import ChapterFact, ItemEventFact, RelatedItemFact
from src.services import entity_aggregator


# ── FactValidator 软校验 ──────────────────────────────


def _make_fact(item_events: list[dict], **extra) -> ChapterFact:
    return ChapterFact(
        chapter_id=1,
        novel_id="test-novel",
        item_events=[ItemEventFact(**e) for e in item_events],
        characters=extra.get("characters", []),
        locations=extra.get("locations", []),
        new_concepts=extra.get("new_concepts", []),
    )


class TestRelatedItemValidation:
    def test_non_item_target_dropped(self):
        """related 目标是概念(领域/能力机制类)而非物品 → 剔除(target-type 泄漏)。"""
        fact = _make_fact(
            [
                {
                    "item_name": "青釭剑",
                    "item_type": "武器",
                    "action": "使用",
                    "related": [
                        {
                            "name": "无极领域",
                            "relation": "同源",
                            "evidence": "青釭剑出鞘,无极领域随之展开",
                        }
                    ],
                },
                {"item_name": "剑鞘", "item_type": "器物", "action": "出现"},
            ],
            new_concepts=[{"name": "无极领域", "category": "功法", "definition": "领域类能力"}],
        )
        result = FactValidator().validate(fact)
        assert result.item_events[0].related == []

    def test_missing_evidence_dropped(self):
        """related 无 evidence → 剔除(证据门控,口径同 org_event「无依据不记录」)。"""
        fact = _make_fact(
            [
                {
                    "item_name": "青釭剑",
                    "item_type": "武器",
                    "action": "获得",
                    "related": [{"name": "剑鞘", "relation": "组成", "evidence": ""}],
                },
                {"item_name": "剑鞘", "item_type": "器物", "action": "出现"},
            ]
        )
        result = FactValidator().validate(fact)
        assert result.item_events[0].related == []

    def test_target_not_in_chapter_items_dropped(self):
        """related 目标在本章根本不是物品(未出现在 item_events)→ 剔除。"""
        fact = _make_fact(
            [
                {
                    "item_name": "青釭剑",
                    "item_type": "武器",
                    "action": "使用",
                    "related": [
                        {
                            "name": "赵云",
                            "relation": "互动",
                            "evidence": "赵云拔出青釭剑",
                        }
                    ],
                },
            ],
            characters=[{"name": "赵云"}],
        )
        result = FactValidator().validate(fact)
        assert result.item_events[0].related == []

    def test_valid_composition_and_transfer_kept(self):
        """正常的组成/持有转移伴随关系保留,并 clamp 名字、去重、剔自引用。"""
        fact = _make_fact(
            [
                {
                    "item_name": "青釭剑",
                    "item_type": "武器",
                    "action": "赠予",
                    "related": [
                        {
                            "name": "剑鞘",
                            "relation": "组成",
                            "evidence": "剑鞘与青釭剑本为一套",
                        },
                        # 重复 → 去重
                        {
                            "name": "剑鞘",
                            "relation": "组成",
                            "evidence": "剑鞘与青釭剑本为一套",
                        },
                        # 自引用 → 剔除
                        {
                            "name": "青釭剑",
                            "relation": "同源",
                            "evidence": "青釭剑锋利无比",
                        },
                    ],
                },
                {"item_name": "剑鞘", "item_type": "器物", "action": "赠予"},
            ]
        )
        result = FactValidator().validate(fact)
        related = result.item_events[0].related
        assert len(related) == 1
        assert related[0].name == "剑鞘"
        assert related[0].relation == "组成"
        assert related[0].evidence == "剑鞘与青釭剑本为一套"

    def test_empty_related_unchanged(self):
        """无 related 的 item_event 行为不变(默认路径零影响)。"""
        fact = _make_fact(
            [{"item_name": "金箍棒", "item_type": "兵器", "action": "使用"}]
        )
        result = FactValidator().validate(fact)
        assert result.item_events[0].item_name == "金箍棒"
        assert result.item_events[0].related == []

    def test_related_item_fact_defaults(self):
        """旧 JSON 无 related 字段 → 反序列化为空列表(向后兼容)。"""
        ie = ItemEventFact(item_name="金箍棒", item_type="兵器", action="出现")
        assert ie.related == []
        rel = RelatedItemFact(name="剑鞘")
        assert rel.relation == "" and rel.evidence == ""


# ── aggregate_item 聚合 ───────────────────────────────

NOVEL = "test-item-related"


@pytest_asyncio.fixture
async def item_db(memory_db):
    """种子两章:

    - 第 1 章:金箍棒与紫金铃同章共现但无显式关系(缺陷 A 场景);
      金箍棒的 related 指向「无极领域」(仅概念,从未作为物品出现,缺陷 B 场景)。
    - 第 2 章:青釭剑 related 剑鞘(组成,有证据);雌雄双剑同章共现无关系。
    """

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def factory():
        return _NonClosing(memory_db)

    fact1 = {
        "characters": [{"name": "孙悟空"}],
        "item_events": [
            {
                "item_name": "金箍棒",
                "item_type": "兵器",
                "action": "使用",
                "actor": "孙悟空",
                "related": [
                    {"name": "无极领域", "relation": "同源", "evidence": "棒出领域开"}
                ],
            },
            {"item_name": "紫金铃", "item_type": "法宝", "action": "出现"},
        ],
        "new_concepts": [{"name": "无极领域", "category": "功法", "definition": "领域"}],
    }
    fact2 = {
        "characters": [{"name": "赵云"}],
        "item_events": [
            {
                "item_name": "青釭剑",
                "item_type": "武器",
                "action": "赠予",
                "actor": "曹操",
                "recipient": "赵云",
                "related": [
                    {"name": "剑鞘", "relation": "组成", "evidence": "剑鞘与青釭剑本为一套"}
                ],
            },
            {"item_name": "剑鞘", "item_type": "器物", "action": "赠予"},
            {"item_name": "雌雄双剑", "item_type": "武器", "action": "使用"},
        ],
    }
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试")
    )
    for num, fact in ((1, fact1), (2, fact2)):
        cur = await memory_db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) VALUES (?, ?, ?, ?)",
            (NOVEL, num, f"第{num}回", "正文……"),
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
async def test_cooccurrence_no_longer_produces_related(item_db):
    """缺陷 A:同章共现(金箍棒×紫金铃、青釭剑×雌雄双剑)不再是 related。"""
    profile = await entity_aggregator.aggregate_item(NOVEL, "金箍棒")
    assert "紫金铃" not in profile.related_items

    profile2 = await entity_aggregator.aggregate_item(NOVEL, "青釭剑")
    assert "雌雄双剑" not in profile2.related_items


@pytest.mark.asyncio
async def test_explicit_relation_kept_both_directions(item_db):
    """显式组成关系在双方物品档案中都可见。"""
    sword = await entity_aggregator.aggregate_item(NOVEL, "青釭剑")
    assert "剑鞘" in sword.related_items

    sheath = await entity_aggregator.aggregate_item(NOVEL, "剑鞘")
    assert "青釭剑" in sheath.related_items


@pytest.mark.asyncio
async def test_non_item_target_blocked_at_aggregation(item_db):
    """缺陷 B:related 目标「无极领域」只作为概念存在 → 读取侧剔除。"""
    profile = await entity_aggregator.aggregate_item(NOVEL, "金箍棒")
    assert "无极领域" not in profile.related_items
