"""multi-pass Epic 2 Story 2.2 测试: source-pass prompt 三件套 + extract_source_pass。

全部使用 mock LLM,不打真实 API。验收口径:
- schema 字段集与主抽取一致(diff 前提);
- prompt 经 prompt_registry 加载、位于 prompts/ 目录(compile_prompts.py
  自动扫描该目录,可编译进 sidecar);
- 结果打 source="source_pass" provenance;
- 单章恰好 1 次 LLM 调用(不做一审的 recall 补漏与 subtype 投票);
- 维度清洗/证据锚定清洗与一审同口径。
"""

from pathlib import Path

import pytest

from src.extraction.chapter_fact_extractor import (
    ChapterFactExtractor,
    _build_extraction_schema,
    _build_source_pass_schema,
)
from src.extraction.prompt_registry import get_prompt
from src.infra import config
from src.infra.llm_client import LlmUsage

CHAPTER_TEXT = (
    "宋江与武松在柴进庄上结拜为义兄弟。两人把酒言欢,结为生死之交。\n"
    "次日,宋江辞别柴进,独自上路投青州去了。"
)


class MockLLM:
    """Mock LLM:固定返回 response,记录全部 (system, prompt) 调用。"""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def generate(self, system, prompt, format=None, temperature=0.1,
                       max_tokens=4096, timeout=120, num_ctx=None):
        self.calls.append((system, prompt))
        return self.response, LlmUsage(100, 50, 150)


def _response() -> dict:
    return {
        "characters": [{"name": "宋江"}, {"name": "武松"}],
        "relationships": [
            {
                "person_a": "宋江",
                "person_b": "武松",
                "relation_type": "结拜兄弟",
                "evidence": "宋江与武松在柴进庄上结拜为义兄弟",
            }
        ],
        "events": [
            {
                "summary": "宋江辞别柴进独自上路",
                "type": "旅行",
                "importance": "high",
                "participants": ["宋江", "柴进"],
                "location": "柴进庄",
                "evidence": "宋江辞别柴进,独自上路投青州去了",
            }
        ],
    }


@pytest.fixture
def quality_gates_on(monkeypatch):
    """打开全部质量开关:验证二审不做 recall/投票(单章 1 次调用),
    且维度/证据清洗与一审同口径。"""
    monkeypatch.setattr(config, "RECALL_PASS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_DIMENSIONS_ENABLED", True)
    monkeypatch.setattr(config, "RELATION_SUBTYPE_VOTE_SAMPLES", 3)
    monkeypatch.setattr(config, "EVIDENCE_GROUNDING_ENABLED", True)


# ── schema 同构(diff 前提)──


def test_source_pass_schema_field_set_matches_main():
    """source-pass schema 与主抽取 schema 字段集一致(逐模型比较 properties 键)。"""
    main = _build_extraction_schema()
    source = _build_source_pass_schema()

    assert set(main.get("properties", {})) == set(source.get("properties", {}))
    main_defs = main.get("$defs", {})
    source_defs = source.get("$defs", {})
    assert set(main_defs) == set(source_defs)
    for name in main_defs:
        main_props = set(main_defs[name].get("properties", {}))
        source_props = set(source_defs[name].get("properties", {}))
        assert main_props == source_props, f"{name} 字段集不一致"


def test_source_pass_schema_allows_empty_lists():
    """二审报告不确定而非猜测:不加 minItems 非空约束(空列表合法)。"""
    schema = _build_source_pass_schema()
    for field in ("characters", "relationships", "locations", "events"):
        assert "minItems" not in schema["properties"].get(field, {})


# ── prompt 经 prompt_registry 加载、可编译进 sidecar ──


def test_source_pass_prompt_loadable_via_registry():
    """prompt 经 prompt_registry 加载,含占位符与独立二审口径。"""
    prompt = get_prompt("source_pass_system")
    assert "独立二审" in prompt
    assert "{context}" in prompt
    assert "{genre_context}" in prompt
    # 报告不确定而非猜测
    assert "不确定" in prompt


def test_source_pass_prompt_file_in_prompts_dir():
    """prompt 文件位于 prompts/ 目录 — compile_prompts.py 自动扫描该目录,
    无需额外登记即可编译进 sidecar。"""
    prompts_dir = (
        Path(__file__).parent.parent / "src" / "extraction" / "prompts"
    )
    assert (prompts_dir / "source_pass_system.txt").exists()


# ── extract_source_pass: provenance + 单调用 + 清洗同口径 ──


@pytest.mark.asyncio
async def test_extract_source_pass_tags_provenance(quality_gates_on):
    """二审产出统一打 source="source_pass" 溯源标记。"""
    llm = MockLLM(_response())
    extractor = ChapterFactExtractor(llm=llm)
    fact, usage, meta = await extractor.extract_source_pass(
        "test-novel", 1, CHAPTER_TEXT,
    )

    assert fact.characters
    assert all(ch.source == "source_pass" for ch in fact.characters)
    assert all(rel.source == "source_pass" for rel in fact.relationships)
    assert all(ev.source == "source_pass" for ev in fact.events)
    assert usage.prompt_tokens == 100
    assert meta.original_len == len(CHAPTER_TEXT)


@pytest.mark.asyncio
async def test_source_pass_single_llm_call_no_recall_no_vote(quality_gates_on):
    """单章恰好 1 次 LLM 调用:不做 recall 补漏、不做 rel_subtype 投票
    (均为一审增量层;二审自身就是独立的一遍)。"""
    llm = MockLLM(_response())
    extractor = ChapterFactExtractor(llm=llm)
    await extractor.extract_source_pass("test-novel", 1, CHAPTER_TEXT)
    assert len(llm.calls) == 1
    system, _ = llm.calls[0]
    assert "查漏专家" not in system
    assert "关系类型判定专家" not in system


@pytest.mark.asyncio
async def test_source_pass_system_prompt_carries_context(quality_gates_on):
    """独立 system prompt 生效,{context} 占位符注入二审上下文。"""
    llm = MockLLM(_response())
    extractor = ChapterFactExtractor(llm=llm)
    await extractor.extract_source_pass(
        "test-novel", 2, CHAPTER_TEXT, context_summary="二审前文: 宋江出场",
    )
    system, prompt = llm.calls[0]
    assert "独立二审" in system
    assert "二审前文: 宋江出场" in system
    # user prompt 是二审口径(独立阅读要求),不是一审的【关键要求】
    assert "【二审要求】" in prompt
    assert "【关键要求】" not in prompt


@pytest.mark.asyncio
async def test_source_pass_sanitize_parity_with_main(quality_gates_on):
    """清洗同口径: 证据锚定开启时,缺 evidence 的事件 importance 降一级,
    与一审 sanitize 行为一致。"""
    response = _response()
    response["events"][0]["evidence"] = ""  # 缺证据 → high 降 medium
    llm = MockLLM(response)
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract_source_pass(
        "test-novel", 1, CHAPTER_TEXT,
    )
    assert fact.events[0].importance == "medium"
    assert fact.events[0].source == "source_pass"


@pytest.mark.asyncio
async def test_source_pass_empty_lists_accepted(quality_gates_on):
    """二审允许空列表输出(报告不确定而非猜测),解析不报错。"""
    llm = MockLLM({"characters": [], "relationships": [], "events": []})
    extractor = ChapterFactExtractor(llm=llm)
    fact, _, _ = await extractor.extract_source_pass(
        "test-novel", 1, CHAPTER_TEXT,
    )
    assert fact.characters == []
    assert fact.relationships == []
    assert fact.events == []
