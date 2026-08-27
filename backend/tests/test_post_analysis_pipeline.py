"""Epic 6 回归: 分析后后台任务编排竞态 + auto 链对齐.

背景: 分析完成后层级重建(GeoOrchestrator v2)与空间补全曾并发 create_task,
两者都对 world_structures 做 load-modify-save 整文档写回;空间补全
(LLM, 60-300s)后完成、后保存,用启动时加载的旧基线覆盖了重建结果
(last-writer-wins,实测: 西游 ws 1004 节点/331 子挂"天下", roots=332 失真)。
修复: geo 链串行(重建 → 空间补全),端点与 auto 链共用
build_default_orchestrator(SuffixNormalizer 最后跑)。
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.services.analysis_service import AnalysisService
from src.services.geo_skills.orchestrator import build_default_orchestrator


def test_default_chain_suffix_runs_last():
    """auto 链与 rebuild-hierarchy-v2 端点共用同一构建函数,
    SuffixNormalizer 必须最后(v0.71.1: 名合并需最终裁决权)。"""
    orch = build_default_orchestrator("novel-x", novel_title="西游记")
    tags = [tag for tag, _ in orch._skills]
    assert tags == ["tier", "votes", "prior", "edmonds", "suffix"]


@pytest.mark.asyncio
async def test_spatial_waits_for_rebuild_and_keeps_its_result():
    """模拟旧竞态(空间补全远慢于重建): 串行化后空间补全必须等重建
    完成才启动,最终 location_parents 来自重建快照而非旧基线。"""
    svc = AnalysisService.__new__(AnalysisService)
    order: list[str] = []
    # 分析前的旧式层级(如 331 子节点挂"天下")
    ws = {"location_parents": {"旧地点": "天下"}}

    async def fake_rebuild(novel_id: str) -> None:
        order.append("rebuild")
        await asyncio.sleep(0)
        ws["location_parents"] = {"花果山": "傲来国", "傲来国": "东胜神洲"}

    async def fake_spatial(novel_id: str) -> None:
        order.append("spatial")
        # 空间补全启动时加载的基线 —— 串行化后必须是重建后的结果
        baseline = dict(ws["location_parents"])
        await asyncio.sleep(0.05)  # 模拟 LLM 耗时(远慢于重建)
        ws["location_parents"] = baseline  # load-modify-save 整文档写回

    with patch.object(svc, "_auto_rebuild_hierarchy", AsyncMock(side_effect=fake_rebuild), create=True), \
         patch.object(svc, "_auto_spatial_completion", AsyncMock(side_effect=fake_spatial), create=True):
        await svc._run_geo_pipeline("novel-x")

    assert order == ["rebuild", "spatial"]
    assert ws["location_parents"] == {"花果山": "傲来国", "傲来国": "东胜神洲"}


@pytest.mark.asyncio
async def test_rebuild_failure_does_not_block_spatial():
    """非致命语义: 重建内部异常被吞掉,空间补全仍照常执行。"""
    svc = AnalysisService.__new__(AnalysisService)
    spatial_ran = False

    async def fake_spatial(novel_id: str) -> None:
        nonlocal spatial_ran
        spatial_ran = True

    # 用真实的 _auto_rebuild_hierarchy,但让其内部 DB 读取失败
    with patch("src.db.novel_store.get_novel", AsyncMock(side_effect=RuntimeError("db down"))), \
         patch.object(svc, "_auto_spatial_completion", AsyncMock(side_effect=fake_spatial), create=True):
        await svc._run_geo_pipeline("novel-x")

    assert spatial_ran


def test_schedule_creates_geo_chain_and_entity_resolution():
    """调度: geo 链(重建+空间补全)为单个串行任务;
    entity resolution(不写 world_structures)保持独立并发任务。"""
    svc = AnalysisService.__new__(AnalysisService)
    names: list[str] = []

    def fake_create_task(coro, *, name=None):
        names.append(name)
        coro.close()  # 避免 unawaited coroutine 警告
        return None

    with patch("asyncio.create_task", side_effect=fake_create_task), \
         patch("src.infra.config.ENTITY_RESOLUTION_ENABLED", True):
        svc._schedule_post_analysis("novel-x")

    assert names == [
        "post-analysis-geo-novel-x",
        "entity-resolution-novel-x",
    ]
