"""multi-pass Epic 3 Story 3.1 测试: pass_diff_service 章节 diff 引擎。

纯函数 diff_chapter_facts 不碰 DB(直接注入 alias_map);
PassDiffService 装配层用 memory_db + mock get_connection 验证缓存与
history 回填(air_unlocked_at / diff_counts)。

覆盖 spec 验收:
- 两边完全相同 → 空 diff;
- 构造性单字段差异 → 正确归类(type / identity / boundary / temporal /
  resolution);
- 别名假分歧:二审独立命名「少年」 vs 一审归一后「杨过」经 alias_map
  归一 → 不产生假 only_in_*;一审归一错误造成的真实差异保留;
- 事件按 (participant 集, 章节内序号邻近) 启发式匹配;
- 幂等 + 可缓存(键: pass_id + chapter_id + 双方内容 hash)。
"""

import json

import pytest

from src.db import analysis_pass_store, chapter_fact_store, chapter_store
from src.models.chapter_fact import ChapterFact
from src.services.pass_diff_service import (
    PassDiffService,
    diff_chapter_facts,
)

NOVEL = "novel-diff"


def _fact(**overrides) -> dict:
    """构造最小合法 fact dict(ChapterFact 同构,经 pydantic 往返,
    与两张表实际落库形态一致 —— 默认值字段齐全)。"""
    base = {
        "chapter_id": 1,
        "novel_id": NOVEL,
        "characters": [],
        "relationships": [],
        "locations": [],
        "events": [],
        "item_events": [],
        "org_events": [],
        "new_concepts": [],
    }
    base.update(overrides)
    return json.loads(
        ChapterFact.model_validate(base).model_dump_json(),
    )


# ── 完全相同 → 空 diff ──


def test_identical_facts_empty_diff():
    fact = _fact(
        characters=[{"name": "宋江", "new_aliases": ["公明"], "appearance": "黑矮"}],
        relationships=[
            {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
             "is_new": True, "evidence": "结拜为义兄弟"},
        ],
        locations=[{"name": "柴进庄", "type": "庄园", "parent": "沧州"}],
        events=[
            {"summary": "宋江武松结拜", "type": "社交", "importance": "high",
             "participants": ["宋江", "武松"], "location": "柴进庄",
             "evidence": "结拜为义兄弟"},
        ],
        new_concepts=[{"name": "义气", "category": "观念"}],
    )
    result = diff_chapter_facts(fact, json.loads(json.dumps(fact)))
    assert result["counts"] == {
        "only_in_main": 0, "only_in_pass": 0, "different": 0,
    }
    assert result["only_in_main"] == []
    assert result["only_in_pass"] == []
    assert result["different"] == []


def test_idempotent_same_input_same_output():
    main = _fact(characters=[{"name": "宋江"}], events=[
        {"summary": "事件甲", "type": "其他", "participants": ["宋江"]},
    ])
    other = _fact(characters=[{"name": "公明哥哥"}], events=[
        {"summary": "事件乙(措辞不同)", "type": "其他", "participants": ["宋江"]},
    ])
    amap = {"公明哥哥": "宋江"}
    r1 = diff_chapter_facts(main, other, amap)
    r2 = diff_chapter_facts(main, other, amap)
    assert json.dumps(r1, sort_keys=True, ensure_ascii=False) == json.dumps(
        r2, sort_keys=True, ensure_ascii=False,
    )


# ── 构造性单字段差异 → 正确归类 ──


def _only_diff(result):
    assert result["counts"]["only_in_main"] == 0
    assert result["counts"]["only_in_pass"] == 0
    assert result["counts"]["different"] == 1
    return result["different"][0]


def test_relation_type_diff_categorized_type():
    main = _fact(relationships=[
        {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟"},
    ])
    other = _fact(relationships=[
        {"person_a": "宋江", "person_b": "武松", "relation_type": "主从"},
    ])
    d = _only_diff(diff_chapter_facts(main, other))
    assert d["collection"] == "relationships"
    assert d["fields"] == [
        {"field": "relation_type", "category": "type",
         "main": "结拜兄弟", "pass": "主从"},
    ]


def test_relation_temporal_diff_categorized_temporal():
    """is_new/previous_type 分歧 = temporal state(关系是否本章新建立)。"""
    main = _fact(relationships=[
        {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
         "is_new": True, "previous_type": None},
    ])
    other = _fact(relationships=[
        {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
         "is_new": False, "previous_type": "朋友"},
    ])
    d = _only_diff(diff_chapter_facts(main, other))
    cats = {f["field"]: f["category"] for f in d["fields"]}
    assert cats == {"is_new": "temporal", "previous_type": "temporal"}


def test_evidence_presence_diff_categorized_resolution():
    """evidence 有无 = unresolved ↔ confirmed;措辞差异不算差异。"""
    main = _fact(relationships=[
        {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
         "evidence": "两人在柴进庄上结拜"},
    ])
    other = _fact(relationships=[
        {"person_a": "武松", "person_b": "宋江", "relation_type": "结拜兄弟",
         "evidence": ""},
    ])
    d = _only_diff(diff_chapter_facts(main, other))
    assert [f["category"] for f in d["fields"]] == ["resolution"]
    # 反向:双方都有 evidence 但措辞不同 → 不算差异
    other2 = _fact(relationships=[
        {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
         "evidence": "结拜为义兄弟"},
    ])
    assert diff_chapter_facts(main, other2)["counts"]["different"] == 0


def test_location_type_diff_categorized_type():
    main = _fact(locations=[{"name": "柴进庄", "type": "庄园"}])
    other = _fact(locations=[{"name": "柴进庄", "type": "城池"}])
    d = _only_diff(diff_chapter_facts(main, other))
    assert d["collection"] == "locations"
    assert d["fields"][0]["category"] == "type"


def test_event_boundary_diff_categorized_boundary():
    """事件边界:参与者集或发生地点分歧。summary 措辞不同不算差异。"""
    main = _fact(events=[
        {"summary": "宋江与武松结拜", "type": "社交",
         "participants": ["宋江", "武松"], "location": "柴进庄"},
    ])
    other = _fact(events=[
        {"summary": "二人结拜(独立二审措辞)", "type": "社交",
         "participants": ["宋江", "武松", "柴进"], "location": "柴进庄"},
    ])
    d = _only_diff(diff_chapter_facts(main, other))
    assert d["collection"] == "events"
    assert [f["category"] for f in d["fields"]] == ["boundary"]
    assert d["fields"][0]["field"] == "participants"


def test_character_alias_set_diff_categorized_identity():
    main = _fact(characters=[{"name": "宋江", "new_aliases": ["公明", "及时雨"]}])
    other = _fact(characters=[{"name": "宋江", "new_aliases": ["公明"]}])
    d = _only_diff(diff_chapter_facts(main, other))
    assert d["collection"] == "characters"
    assert d["fields"][0]["category"] == "identity"


# ── 别名假分歧(经主表 alias_map 双向归一)──


def test_alias_false_divergence_suppressed():
    """二审独立命名「少年」 vs 一审归一后「杨过」:经 alias_map 归一 → 匹配,
    不产生假 only_in_*。事件参与者同理归一。"""
    main = _fact(
        characters=[{"name": "杨过"}],
        relationships=[
            {"person_a": "杨过", "person_b": "小龙女", "relation_type": "师徒"},
        ],
        events=[
            {"summary": "杨过拜入古墓", "type": "成长",
             "participants": ["杨过", "小龙女"]},
        ],
    )
    other = _fact(
        characters=[{"name": "少年"}],
        relationships=[
            {"person_a": "少年", "person_b": "小龙女", "relation_type": "师徒"},
        ],
        events=[
            {"summary": "少年拜师(措辞不同)", "type": "成长",
             "participants": ["少年", "小龙女"]},
        ],
    )
    amap = {"少年": "杨过"}
    result = diff_chapter_facts(main, other, amap)
    assert result["counts"] == {
        "only_in_main": 0, "only_in_pass": 0, "different": 0,
    }


def test_real_identity_divergence_preserved():
    """一审归一错误造成的真实差异要保留:alias_map 不含映射时,
    「少年」与「杨过」归一后仍不同名 → only_in 双向出现。"""
    main = _fact(characters=[{"name": "杨过"}])
    other = _fact(characters=[{"name": "少年"}])
    result = diff_chapter_facts(main, other, {"不相关": "路人"})
    assert result["counts"] == {"only_in_main": 1, "only_in_pass": 1,
                                "different": 0}
    assert result["only_in_main"][0]["item"]["name"] == "杨过"
    assert result["only_in_pass"][0]["item"]["name"] == "少年"


# ── 事件匹配启发式(participant 集 + 序号邻近)──


def test_events_matched_by_participants_despite_order():
    """双方事件顺序不同:按参与者集匹配,不产生假差异。"""
    main = _fact(events=[
        {"summary": "结拜", "type": "社交", "participants": ["宋江", "武松"]},
        {"summary": "辞别", "type": "旅行", "participants": ["宋江", "柴进"]},
    ])
    other = _fact(events=[
        {"summary": "辞别(措辞不同)", "type": "旅行",
         "participants": ["柴进", "宋江"]},
        {"summary": "结拜(措辞不同)", "type": "社交",
         "participants": ["武松", "宋江"]},
    ])
    assert diff_chapter_facts(main, other)["counts"] == {
        "only_in_main": 0, "only_in_pass": 0, "different": 0,
    }


def test_event_only_in_pass_when_no_participant_overlap():
    main = _fact(events=[
        {"summary": "结拜", "type": "社交", "participants": ["宋江", "武松"]},
    ])
    other = _fact(events=[
        {"summary": "结拜", "type": "社交", "participants": ["宋江", "武松"]},
        {"summary": "景阳冈打虎", "type": "战斗", "participants": ["武松"]},
    ])
    result = diff_chapter_facts(main, other)
    assert result["counts"] == {"only_in_main": 0, "only_in_pass": 1,
                                "different": 0}
    assert result["only_in_pass"][0]["item"]["summary"] == "景阳冈打虎"


def test_event_without_participants_matched_by_index():
    """双方事件均无 participants:序号邻近(|i-j|<=1)可配对。"""
    main = _fact(events=[
        {"summary": "风雪山神庙", "type": "战斗", "participants": []},
    ])
    other = _fact(events=[
        {"summary": "雪夜山神庙(措辞不同)", "type": "战斗", "participants": []},
    ])
    assert diff_chapter_facts(main, other)["counts"] == {
        "only_in_main": 0, "only_in_pass": 0, "different": 0,
    }


# ── PassDiffService 装配层:缓存 + history 回填 ──


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.fixture
def diff_env(memory_db, monkeypatch):
    """播种 novel + 1 章 + 主表 fact;patch 相关 store 的 get_connection 与
    alias_map(注入别名映射,隔离 alias_resolver 内部)。"""
    import src.services.pass_diff_service as pds

    async def _factory():
        return _NonClosing(memory_db)

    for mod in (analysis_pass_store, chapter_fact_store, chapter_store):
        monkeypatch.setattr(mod, "get_connection", _factory)
    monkeypatch.setattr(pds, "build_alias_map", _alias_map_stub)
    return memory_db


_ALIAS_MAP: dict[str, str] = {}


async def _alias_map_stub(_novel_id):
    return dict(_ALIAS_MAP)


async def _seed(memory_db, main_fact: dict, pass_fact: dict | None):
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "测试小说"),
    )
    await memory_db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content,"
        " analysis_status) VALUES (1, ?, 1, '第1章', '正文', 'completed')",
        (NOVEL,),
    )
    await memory_db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json)"
        " VALUES (?, 1, ?)",
        (NOVEL, json.dumps(main_fact, ensure_ascii=False)),
    )
    await analysis_pass_store.create_pass("p1", NOVEL, 1, 1)
    if pass_fact is not None:
        await analysis_pass_store.upsert_pass_chapter_fact(
            "p1", 1, ChapterFact.model_validate(pass_fact),
        )
    await memory_db.commit()


@pytest.mark.asyncio
async def test_service_diff_and_history_backfill(diff_env):
    memory_db = diff_env
    main = _fact(characters=[{"name": "宋江"}], events=[
        {"summary": "结拜", "type": "社交", "participants": ["宋江"]},
    ])
    other = _fact(characters=[{"name": "宋江"}, {"name": "武松"}], events=[
        {"summary": "结拜", "type": "社交", "participants": ["宋江"]},
    ])
    await _seed(memory_db, main, other)

    svc = PassDiffService()
    result = await svc.get_chapter_diff("p1", 1)
    assert result["cached"] is False
    assert result["chapter"] == 1
    assert result["counts"] == {"only_in_main": 0, "only_in_pass": 1,
                                "different": 0}
    assert result["only_in_pass"][0]["item"]["name"] == "武松"

    # history 回填:air_unlocked_at + diff_counts
    pass_row = await analysis_pass_store.get_pass("p1")
    entry = pass_row["history_json"]["chapters"]["1"]
    assert entry["air_unlocked_at"] is not None
    assert entry["diff_counts"] == {"air_only": 0, "pass_only": 1,
                                    "different": 0}

    # 缓存命中:cached=True,air_unlocked_at 不被覆盖
    first_unlock = entry["air_unlocked_at"]
    again = await svc.get_chapter_diff("p1", 1)
    assert again["cached"] is True
    assert again["counts"] == result["counts"]
    pass_row2 = await analysis_pass_store.get_pass("p1")
    assert pass_row2["history_json"]["chapters"]["1"]["air_unlocked_at"] == (
        first_unlock
    )


@pytest.mark.asyncio
async def test_service_cache_invalidated_by_content_change(diff_env):
    """缓存键含双方内容 hash:二审结果变化 → 重新计算。"""
    memory_db = diff_env
    main = _fact(characters=[{"name": "宋江"}])
    await _seed(memory_db, main, _fact(characters=[{"name": "宋江"}]))

    svc = PassDiffService()
    r1 = await svc.get_chapter_diff("p1", 1)
    assert r1["counts"] == {"only_in_main": 0, "only_in_pass": 0,
                            "different": 0}

    # 二审该章内容变化 → 内容 hash 变化 → 重新计算(幂等仍成立)
    await analysis_pass_store.upsert_pass_chapter_fact(
        "p1", 1, ChapterFact.model_validate(
            _fact(characters=[{"name": "宋江"}, {"name": "卢俊义"}]),
        ),
    )
    r2 = await svc.get_chapter_diff("p1", 1)
    assert r2["cached"] is False
    assert r2["counts"]["only_in_pass"] == 1


@pytest.mark.asyncio
async def test_service_alias_map_applied(diff_env):
    """装配层把主表 alias_map 传给纯函数:别名假分歧被抑制。"""
    memory_db = diff_env
    _ALIAS_MAP.clear()
    _ALIAS_MAP.update({"少年": "杨过"})
    try:
        main = _fact(characters=[{"name": "杨过"}])
        await _seed(memory_db, main, _fact(characters=[{"name": "少年"}]))
        svc = PassDiffService()
        result = await svc.get_chapter_diff("p1", 1)
        assert result["counts"] == {"only_in_main": 0, "only_in_pass": 0,
                                    "different": 0}
    finally:
        _ALIAS_MAP.clear()


@pytest.mark.asyncio
async def test_service_errors(diff_env):
    memory_db = diff_env
    await _seed(memory_db, _fact(), None)

    svc = PassDiffService()
    with pytest.raises(ValueError, match="二审任务不存在"):
        await svc.get_chapter_diff("no-such-pass", 1)
    with pytest.raises(ValueError, match="章节不存在"):
        await svc.get_chapter_diff("p1", 99)
    with pytest.raises(ValueError, match="二审尚未覆盖"):
        await svc.get_chapter_diff("p1", 1)

    # 二审该章失败 → 不可 diff
    await analysis_pass_store.upsert_pass_chapter_fact(
        "p1", 1, ChapterFact(chapter_id=1, novel_id=NOVEL),
        status="failed", error="boom",
    )
    with pytest.raises(ValueError, match="未完成"):
        await svc.get_chapter_diff("p1", 1)

    # 主表无一审结果 → 不可 diff
    await analysis_pass_store.upsert_pass_chapter_fact(
        "p1", 1, ChapterFact(chapter_id=1, novel_id=NOVEL),
    )
    await memory_db.execute("DELETE FROM chapter_facts")
    await memory_db.commit()
    svc2 = PassDiffService()
    with pytest.raises(ValueError, match="没有一审分析结果"):
        await svc2.get_chapter_diff("p1", 1)
