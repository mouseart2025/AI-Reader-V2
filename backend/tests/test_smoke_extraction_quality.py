"""smoke_extraction_quality 冒烟脚本单元测试(任务 A)。

全部离线:LLM 交互用脚本自带的 MockLLM / 构造 ChapterFact,不触网。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "smoke_extraction_quality.py"
_spec = importlib.util.spec_from_file_location("smoke_extraction_quality", _SCRIPT)
smoke = importlib.util.module_from_spec(_spec)
sys.modules["smoke_extraction_quality"] = smoke
_spec.loader.exec_module(smoke)

from src.models.chapter_fact import (  # noqa: E402
    ChapterFact,
    CharacterFact,
    EventFact,
    RelationshipFact,
)


def _run(coro):
    """运行协程并恢复主线程 event loop(与 test_quality_dashboard 同款防护)。"""
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _make_fact() -> ChapterFact:
    return ChapterFact(
        chapter_id=1,
        novel_id="t",
        characters=[
            CharacterFact(name="石猴"),
            CharacterFact(name="石猿", source="recall_pass"),
        ],
        relationships=[
            RelationshipFact(
                person_a="众猴", person_b="石猴", relation_type="臣服",
                polarity="positive", rel_subtype="主从", closeness="close",
                evidence="众猴听说，即拱伏无违",
            ),
            RelationshipFact(
                person_a="石猴", person_b="樵夫", relation_type="问路",
                evidence="",
            ),
        ],
        events=[
            EventFact(summary="跳水帘洞", type="成长", participants=["石猴"],
                      evidence="径跳入瀑布泉中", source="recall_pass"),
            EventFact(summary="拜王", type="社交", participants=["石猴", "众猴"]),
        ],
    )


_CHAPTER_TEXT = "石猴与众猴听说，即拱伏无违，朝上礼拜。石猿将身一纵，径跳入瀑布泉中。"


class TestDimensionMetrics:
    def test_fill_rates(self):
        m = smoke.dimension_metrics(_make_fact())
        assert m["total_relations"] == 2
        assert m["polarity_fill_rate"] == pytest.approx(0.5)
        assert m["rel_subtype_fill_rate"] == pytest.approx(0.5)
        assert m["closeness_fill_rate"] == pytest.approx(0.5)

    def test_empty_relations(self):
        fact = ChapterFact(chapter_id=1, novel_id="t")
        m = smoke.dimension_metrics(fact)
        assert m["total_relations"] == 0
        assert m["polarity_fill_rate"] is None


class TestEvidenceMetrics:
    def test_coverage_and_location(self):
        m = smoke.evidence_metrics(_make_fact(), _CHAPTER_TEXT)
        # 关系 1/2 有 evidence,事件 1/2 有 → 总体 2/4
        assert m["relation_coverage"] == pytest.approx(0.5)
        assert m["event_coverage"] == pytest.approx(0.5)
        assert m["overall_coverage"] == pytest.approx(0.5)
        # 两条非空 evidence 都能在原文定位
        assert m["span_located_rate"] == pytest.approx(1.0)

    def test_unlocated_span(self):
        fact = _make_fact()
        fact.relationships[0].evidence = "原文中根本不存在的编造引用"
        m = smoke.evidence_metrics(fact, _CHAPTER_TEXT)
        assert m["span_located_rate"] == pytest.approx(0.5)


class TestRecallMetrics:
    def test_source_counting(self):
        m = smoke.recall_metrics(_make_fact())
        assert m == {"characters": 1, "relationships": 0, "events": 1}


class TestPipelineLogStats:
    def test_counts_quality_events(self):
        stats = smoke.PipelineLogStats()
        logger = logging.getLogger("test.smoke.stats")
        logger.addHandler(stats)
        logger.setLevel(logging.DEBUG)
        try:
            logger.warning("Chapter 1: invalid polarity '友好' for a-b, reset to None")
            logger.warning("Chapter 1: invalid closeness '亲密' for a-b, reset to None")
            logger.info("Chapter 1: rel_subtype vote override a-b: 结拜 -> 敌对 (votes={})")
            logger.warning("Chapter 1: event 'x' 缺少 evidence,importance high→medium 并标记")
            logger.warning("Chapter 1: relationship a-b evidence 无法在原文定位: 'y'")
        finally:
            logger.removeHandler(stats)
        d = stats.as_dict()
        assert d["invalid_dimension_values"] == {"polarity": 1, "rel_subtype": 0, "closeness": 1}
        assert d["invalid_dimension_total"] == 2
        assert d["vote_overrides"] == 1
        assert d["evidence_missing_warnings"] == 1
        assert d["evidence_unlocated_warnings"] == 1


class TestEstimateCost:
    def test_deepseek_pricing(self):
        # deepseek-chat: 0.27 / 1.10 USD per 1M tokens
        cost = smoke.estimate_cost_usd(1_000_000, 1_000_000, "deepseek-chat")
        assert cost == pytest.approx(1.37)

    def test_unknown_model_fallback(self):
        cost = smoke.estimate_cost_usd(1_000_000, 0, "no-such-model-xyz")
        assert cost == pytest.approx(0.50)  # _DEFAULT_PRICING


class TestMockEndToEnd:
    """--mock 模式全链路:抽取 → 校验 → 幻觉判定(离线)。"""

    def test_run_smoke_mock(self, tmp_path):
        llm = smoke.MockLLM()
        log_path = tmp_path / "review.jsonl"
        result = _run(smoke.run_smoke(
            _CHAPTER_TEXT * 20, llm, review_log_path=log_path,
        ))
        # 越界值被 sanitize 拦截并计数
        assert result["sanitize"]["invalid_dimension_values"]["polarity"] == 1
        assert result["sanitize"]["invalid_dimension_values"]["closeness"] == 1
        # 投票 override:结拜 → 敌对
        assert result["sanitize"]["vote_overrides"] == 1
        # recall pass 补漏 1 人物(石猿)
        assert result["recall_pass"]["characters"] == 1
        # 幻觉候选被剔除(不存在之人)
        assert result["hallucination"]["candidates"] == ["不存在之人"]
        removed = [a for a in result["hallucination"]["actions"]
                   if a["action"] == "removed"]
        assert len(removed) == 1
        # 用量累计:主抽取 + 2 次投票 + 1 次 recall + 1 次幻觉判定 = 5 次
        assert result["usage"]["llm_calls"] == 5
        assert result["usage"]["total_tokens"] == 5 * 150
        # 审计条目已落盘
        assert log_path.exists()

    def test_build_and_render_report(self, tmp_path):
        llm = smoke.MockLLM()
        result = _run(smoke.run_smoke(
            _CHAPTER_TEXT * 20, llm,
            review_log_path=tmp_path / "review.jsonl",
        ))
        report = smoke.build_report(
            result, mode="mock", model="mock(deepseek-chat)",
            chapter_title="第一回", chapter_id=1, chapter_chars=100,
        )
        report["findings"] = smoke.derive_findings(report)
        assert report["schema"] == smoke.SCHEMA_VERSION
        assert report["usage"]["cost_usd"] > 0
        md = smoke.render_report_md(report)
        assert "Epic 1 关系维度" in md
        assert "Epic 3 证据锚定" in md
        assert "Epic 4 recall pass 与幻觉判定" in md
        assert "发现与建议" in md
        # 越界值应触发一条发现
        assert any("越界" in f for f in report["findings"])


class TestDryRunPlan:
    def test_load_chapter(self):
        title, text = smoke.load_chapter(smoke.DEFAULT_NOVEL_FILE, 1)
        assert "第一回" in title
        assert "花果山" in text

    def test_load_chapter_out_of_range(self):
        with pytest.raises(ValueError):
            smoke.load_chapter(smoke.DEFAULT_NOVEL_FILE, 99)
