"""场景并入主抽取 (issue #78) 测试。

覆盖:
- SCENE_MERGE_ENABLED 开关解析: 默认云端开/本地关, env 显式设置优先;
- 合并模式单段章: 一次 LLM 调用同时产出 facts + scenes, prompt 注入
  【Pn】段落标记, scenes 经统一校验(坐标钳制/覆盖修复/重排),
  不污染 ChapterFact / fact_json;
- 分段章回退: 不走合并(无标记注入、meta.scenes 为 None);
- 开关关闭: prompt 与解析行为与现状一致;
- 证据锚定: 【P\\d+】标记剥离后跨段 span 照常定位;
- 服务端: 合并产物直接写 scenes_json(独立 scene 调用不发生),
  分段章回退独立 scene 调用并落库。

全部使用 mock LLM (+ service 级用 memory DB),不打真实 API。
"""

from __future__ import annotations

import json
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
from src.extraction import chapter_fact_extractor as cfe
from src.extraction.chapter_fact_extractor import ChapterFactExtractor
from src.infra import config
from src.infra.llm_client import LlmUsage
from src.services import embedding_service, visualization_service
from src.services.analysis_service import AnalysisService, manager

CHAPTER_TEXT = (
    "宋江与武松在柴进庄上结拜为义兄弟。\n"
    "两人把酒言欢,结为生死之交。\n"
    "次日,宋江辞别柴进,独自上路投青州去了。"
)

SCENES_RAW = [
    {
        "paragraph_range": [0, 1],
        "title": "结拜",
        "location": "柴进庄",
        "time_of_day": "午",
        "characters": ["宋江", "武松"],
        "emotional_tone": "欢乐",
        "event_type": "对话",
        "summary": "宋江与武松在柴进庄上结拜为义兄弟,把酒言欢。",
        "key_dialogue": [],
    },
    {
        "paragraph_range": [2, 2],
        "title": "辞别",
        "location": "柴进庄",
        "time_of_day": "早",
        "characters": ["宋江", "柴进"],
        "emotional_tone": "平静",
        "event_type": "旅行",
        "summary": "宋江辞别柴进,独自上路投青州。",
        "key_dialogue": [],
    },
]


def _fact_dict(with_scenes: bool = True) -> dict:
    data = {
        "characters": [{"name": "宋江"}, {"name": "武松"}, {"name": "柴进"}],
        "relationships": [
            {
                "person_a": "宋江",
                "person_b": "武松",
                "relation_type": "结拜兄弟",
                "evidence": "宋江与武松在柴进庄上结拜为义兄弟",
            },
        ],
        "locations": [{"name": "柴进庄", "type": "庄园"}],
        "events": [
            {
                "summary": "宋江辞别柴进独自上路",
                "type": "旅行",
                "importance": "medium",
                "participants": ["宋江", "柴进"],
                "location": "柴进庄",
                "evidence": "宋江辞别柴进,独自上路投青州去了",
            },
        ],
    }
    if with_scenes:
        data["scenes"] = [dict(s) for s in SCENES_RAW]
    return data


class MockLLM:
    """返回固定 facts(+scenes);记录全部调用参数。"""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.calls.append({
            "system": system, "prompt": prompt, "format": format,
        })
        return self.response, LlmUsage(100, 50, 150)


@pytest.fixture
def merge_off_switches(monkeypatch):
    """关闭与场景合并无关的质量开关,隔离被测行为。"""
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)


def _cloud_extractor(llm) -> ChapterFactExtractor:
    """云端口径的 extractor(mock LLM 不是云端 client,手动置 _is_cloud)。"""
    ext = ChapterFactExtractor(llm=llm)
    ext._is_cloud = True
    return ext


# ── 开关解析 ──


def test_switch_default_cloud_on_local_off(monkeypatch):
    monkeypatch.delenv("SCENE_MERGE_ENABLED", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert config.get_scene_merge_enabled() is True
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert config.get_scene_merge_enabled() is False


def test_switch_env_explicit_wins(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "true")
    assert config.get_scene_merge_enabled() is True
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "0")
    assert config.get_scene_merge_enabled() is False


# ── wrapper schema ──


def test_wrapper_schema_scenes_position(merge_off_switches):
    """scenes 在 new_concepts 之后、world_declarations 之前(靠后但非最后),
    并加入 required 引导模型产出;基 schema 不被污染。"""
    wrapper = cfe._build_extraction_schema_with_scenes()
    keys = list(wrapper["properties"])
    assert keys.index("new_concepts") < keys.index("scenes")
    assert keys.index("scenes") < keys.index("world_declarations")
    assert "scenes" in wrapper["required"]
    base = cfe._build_extraction_schema()
    assert "scenes" not in base["properties"]
    assert "scenes" not in base.get("required", [])


# ── 合并模式: 单段章一次调用产出 facts + scenes ──


@pytest.mark.asyncio
async def test_merged_single_segment_one_call(merge_off_switches, monkeypatch):
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "true")
    llm = MockLLM(_fact_dict())
    extractor = _cloud_extractor(llm)
    fact, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    # 恰好一次 LLM 调用(recall/投票已关)
    assert len(llm.calls) == 1
    call = llm.calls[0]
    # user prompt 注入【Pn】段落标记 + 场景拆分要求
    assert "【P0】宋江与武松" in call["prompt"]
    assert "【P2】次日" in call["prompt"]
    assert "【场景拆分】scenes" in call["prompt"]
    # system 注入场景拆分规则;输出 schema 是 wrapper(带 scenes)
    assert "场景拆分规则" in call["system"]
    assert "scenes" in call["format"]["properties"]

    # facts 照常解析;scenes 不污染 ChapterFact
    assert [c.name for c in fact.characters] == ["宋江", "武松", "柴进"]
    assert "scenes" not in fact.model_dump()

    # scenes 经统一校验: 覆盖全部段落、index 重排
    assert meta.scene_total_paragraphs == 3
    assert meta.scenes is not None and len(meta.scenes) == 2
    assert meta.scenes[0]["paragraph_range"] == [0, 1]
    assert meta.scenes[-1]["paragraph_range"] == [2, 2]
    assert [s["index"] for s in meta.scenes] == [0, 1]
    assert meta.scenes[0]["chapter"] == 1


@pytest.mark.asyncio
async def test_merged_scenes_coverage_fixed(merge_off_switches, monkeypatch):
    """LLM 产出的 scenes 有缺口/越界时按统一规则修复(与独立调用同口径)。"""
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "true")
    bad_scenes = [
        {"paragraph_range": [1, 1], "title": "中段", "summary": "中段。"},
        {"paragraph_range": [2, 99], "title": "尾段", "summary": "尾段。"},
    ]
    llm = MockLLM(_fact_dict())
    llm.response["scenes"] = bad_scenes
    extractor = _cloud_extractor(llm)
    _, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert meta.scenes is not None
    # 首个场景起点钳到 0,末个场景终点钳到 total-1
    assert meta.scenes[0]["paragraph_range"][0] == 0
    assert meta.scenes[-1]["paragraph_range"][1] == 2


# ── 分段章回退(坐标系破坏,不走合并)──


@pytest.mark.asyncio
async def test_segmented_chapter_no_merge(merge_off_switches, monkeypatch):
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "true")
    monkeypatch.setattr(config, "CONTEXT_WINDOW_SIZE", 131072)  # segment_enabled
    long_text = "\n".join(f"第{i}段,宋江一行人继续赶路,天色渐晚。" for i in range(400))
    assert len(long_text) > 7000  # _SEGMENT_THRESHOLD_2

    llm = MockLLM(_fact_dict())
    extractor = _cloud_extractor(llm)
    _, _, meta = await extractor.extract("test-novel", 1, long_text)

    assert meta.segment_count > 1
    # 分段路径不注入段落标记、不产出合并 scenes(由调用方回退独立调用)
    assert meta.scene_total_paragraphs == 0
    assert meta.scenes is None
    for call in llm.calls:
        assert "【P0】" not in call["prompt"]
        assert "scenes" not in call["format"]["properties"]


# ── 开关关闭 / 本地模式: 行为与现状一致 ──


@pytest.mark.asyncio
async def test_switch_off_behaves_as_today(merge_off_switches, monkeypatch):
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "false")
    llm = MockLLM(_fact_dict())  # mock 误产 scenes,应被剥离丢弃
    extractor = _cloud_extractor(llm)
    fact, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    call = llm.calls[0]
    assert "【P0】" not in call["prompt"]
    assert "【场景拆分】" not in call["prompt"]
    assert "场景拆分规则" not in call["system"]
    assert "scenes" not in call["format"]["properties"]
    assert meta.scene_total_paragraphs == 0
    assert meta.scenes is None
    assert "scenes" not in fact.model_dump()


@pytest.mark.asyncio
async def test_local_mode_no_merge(merge_off_switches, monkeypatch):
    """本地 Ollama(默认开关关 + 非云端 client)不走合并。"""
    monkeypatch.delenv("SCENE_MERGE_ENABLED", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    llm = MockLLM(_fact_dict())
    extractor = ChapterFactExtractor(llm=llm)  # _is_cloud=False
    fact, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert "【P0】" not in llm.calls[0]["prompt"]
    assert meta.scenes is None
    assert "scenes" not in fact.model_dump()


# ── 证据锚定: 【P\d+】剥离后照常定位 ──


def test_span_located_paragraph_markers_stripped():
    marked = (
        "【P0】宋江与武松在柴进庄上结拜为义兄弟。\n"
        "【P1】两人把酒言欢,结为生死之交。"
    )
    # 跨段 span(标记位于 span 中间)
    assert cfe.span_located("结拜为义兄弟。两人把酒言欢", marked)
    # 段首 span
    assert cfe.span_located("两人把酒言欢,结为生死之交", marked)
    # span 本身混入标记(LLM 连标记一起引用)
    assert cfe.span_located("结拜为义兄弟。【P1】两人把酒言欢", CHAPTER_TEXT)
    # 内容差异仍判不可定位
    assert not cfe.span_located("结拜为义兄弟。两人大打出手", marked)


@pytest.mark.asyncio
async def test_merged_evidence_still_located(monkeypatch):
    """合并模式 + 证据锚定开: 证据 span(引自未标记原文)照常定位,不误报。"""
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "true")
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 1)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)

    llm = MockLLM(_fact_dict())
    extractor = _cloud_extractor(llm)
    _, _, meta = await extractor.extract("test-novel", 1, CHAPTER_TEXT)

    assert meta.scenes  # 合并产物仍在
    assert meta.evidence_missing_relations == 0
    assert meta.evidence_missing_events == 0
    assert meta.evidence_unlocated_spans == 0


# ── 服务端: 合并产物写 scenes_json / 分段回退独立调用 ──


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.fixture
def svc_env(memory_db, monkeypatch):
    """patch 全部 DB 访问到 memory_db;广播捕获;关闭质量开关;云端模式。"""
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
    monkeypatch.setattr(sqlite_db, "get_connection", _factory)
    import src.services.analysis_service as analysis_service_mod
    monkeypatch.setattr(analysis_service_mod, "get_connection", _factory)

    broadcasts: list[tuple[str, dict]] = []

    async def _capture(novel_id, data):
        broadcasts.append((novel_id, data))

    monkeypatch.setattr(manager, "broadcast", _capture)

    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", False)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", False)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("ANALYSIS_CONCURRENCY", "1")
    monkeypatch.setenv("SCENE_MERGE_ENABLED", "true")

    monkeypatch.setattr(embedding_service, "index_chapter", MagicMock())
    monkeypatch.setattr(
        embedding_service, "index_entities_from_fact", MagicMock(),
    )

    return {"db": memory_db, "broadcasts": broadcasts}


async def _seed(db, novel_id: str, contents: list[str]) -> None:
    await db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, "场景合并测试"),
    )
    for i, content in enumerate(contents, start=1):
        await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content,"
            " analysis_status) VALUES (?, ?, ?, ?, ?)",
            (novel_id, i, f"第{i}章", content, "pending"),
        )
    await db.commit()


def _make_service(monkeypatch, mock_llm, scene_return=None) -> AnalysisService:
    svc = AnalysisService()
    svc.extractor = ChapterFactExtractor(llm=mock_llm)
    svc.extractor._is_cloud = True  # 云端口径(开关默认云端开)
    svc.scene_extractor = AsyncMock()
    svc.scene_extractor.extract = AsyncMock(return_value=scene_return or [])

    async def _no_review(novel_id, chapter_num, fact, chapter_text, protected):
        return fact

    monkeypatch.setattr(svc, "_review_hallucinations", _no_review)

    async def _noop_world(world_agent, chapter_num, chapter_text, fact):
        return None

    monkeypatch.setattr(
        AnalysisService, "_update_world_structure", staticmethod(_noop_world),
    )
    svc._schedule_post_analysis = lambda novel_id: None
    return svc


async def _chapter_facts_row(db, novel_id: str) -> dict:
    cursor = await db.execute(
        "SELECT fact_json, scenes_json FROM chapter_facts WHERE novel_id = ?",
        (novel_id,),
    )
    row = await cursor.fetchone()
    return {
        "fact": json.loads(row["fact_json"]),
        "scenes": json.loads(row["scenes_json"]) if row["scenes_json"] else None,
    }


@pytest.mark.asyncio
async def test_service_merged_scenes_written_no_extra_call(svc_env, monkeypatch):
    """合并模式单段章: 一次 LLM 调用产出 facts+scenes, scenes 直接写
    scenes_json,独立 scene 调用不发生,fact_json 不含 scenes。"""
    db = svc_env["db"]
    await _seed(db, "novel-merge", [CHAPTER_TEXT])

    llm = MockLLM(_fact_dict())
    svc = _make_service(monkeypatch, llm)
    await analysis_task_store.create_task("task-m", "novel-merge", 1, 1)
    svc._task_signals["task-m"] = "running"
    await svc._run_loop("task-m", "novel-merge", 1, 1)

    # 全书仅一次结构化抽取调用(无独立 scene 调用);
    # 第二次调用(若有)是分析结束后的概要生成,非抽取
    extract_calls = [c for c in llm.calls if c["format"] is not None]
    assert len(extract_calls) == 1
    svc.scene_extractor.extract.assert_not_called()

    row = await _chapter_facts_row(db, "novel-merge")
    assert "scenes" not in row["fact"]
    assert row["scenes"] is not None and len(row["scenes"]) == 2
    assert row["scenes"][0]["title"] == "结拜"
    assert row["scenes"][0]["paragraph_range"] == [0, 1]


@pytest.mark.asyncio
async def test_service_segmented_falls_back_to_scene_extractor(
    svc_env, monkeypatch,
):
    """分段长章: 主抽取不产出 scenes,回退独立 scene 调用并落库。"""
    monkeypatch.setattr(config, "CONTEXT_WINDOW_SIZE", 131072)  # segment_enabled
    db = svc_env["db"]
    long_text = "\n".join(f"第{i}段,宋江一行人继续赶路,天色渐晚。" for i in range(400))
    await _seed(db, "novel-seg", [long_text])

    fallback_scene = dict(SCENES_RAW[0])
    llm = MockLLM(_fact_dict())
    svc = _make_service(monkeypatch, llm, scene_return=[fallback_scene])
    await analysis_task_store.create_task("task-s", "novel-seg", 1, 1)
    svc._task_signals["task-s"] = "running"
    await svc._run_loop("task-s", "novel-seg", 1, 1)

    # 分段抽取 ≥2 次调用 + 独立 scene 调用恰好一次
    assert len(llm.calls) >= 2
    svc.scene_extractor.extract.assert_awaited_once()

    row = await _chapter_facts_row(db, "novel-seg")
    assert "scenes" not in row["fact"]
    assert row["scenes"] == [fallback_scene]
