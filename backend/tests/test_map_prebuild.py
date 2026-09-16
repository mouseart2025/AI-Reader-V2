"""地图预建: geo 后台链调度顺序 + map_prebuild 广播 + 非致命语义。

预建必须排在层级重建 → 空间补全之后(它消费两者写回的 world_structures),
失败只广播 error,不向上传播。
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.analysis_service import AnalysisService


@pytest.mark.asyncio
async def test_geo_pipeline_runs_prebuild_last():
    """_run_geo_pipeline 顺序: rebuild → spatial → prebuild。"""
    svc = AnalysisService.__new__(AnalysisService)
    order: list[str] = []

    async def fake_rebuild(novel_id: str) -> None:
        order.append("rebuild")

    async def fake_spatial(novel_id: str) -> None:
        order.append("spatial")

    async def fake_prebuild(novel_id: str) -> None:
        order.append("prebuild")

    with patch.object(svc, "_auto_rebuild_hierarchy", AsyncMock(side_effect=fake_rebuild), create=True), \
         patch.object(svc, "_auto_spatial_completion", AsyncMock(side_effect=fake_spatial), create=True), \
         patch.object(svc, "_auto_map_prebuild", AsyncMock(side_effect=fake_prebuild), create=True):
        await svc._run_geo_pipeline("novel-x")

    assert order == ["rebuild", "spatial", "prebuild"]


@pytest.mark.asyncio
async def test_prebuild_broadcasts_running_then_done():
    """成功路径: 先 invalidate,再广播 running → 调 get_map_data → 广播 done。"""
    svc = AnalysisService.__new__(AnalysisService)
    broadcasts: list[dict] = []

    async def fake_broadcast(novel_id: str, msg: dict) -> None:
        broadcasts.append(msg)

    with patch("src.db.novel_store.get_novel",
               AsyncMock(return_value={"total_chapters": 12})), \
         patch("src.services.visualization_service.invalidate_map_response_cache",
               AsyncMock()) as inv, \
         patch("src.services.visualization_service.get_map_data",
               AsyncMock(return_value={})) as gmd, \
         patch("src.services.analysis_service.manager") as mgr:
        mgr.broadcast = AsyncMock(side_effect=fake_broadcast)
        await svc._auto_map_prebuild("novel-x")

    inv.assert_awaited_once_with("novel-x")
    gmd.assert_awaited_once_with("novel-x", 1, 12)
    assert broadcasts == [
        {"type": "map_prebuild", "status": "running", "stage": "构建世界地图..."},
        {"type": "map_prebuild", "status": "done"},
    ]


@pytest.mark.asyncio
async def test_prebuild_failure_is_non_fatal():
    """get_map_data 抛异常: 不向上传播,广播 error。"""
    svc = AnalysisService.__new__(AnalysisService)
    broadcasts: list[dict] = []

    async def fake_broadcast(novel_id: str, msg: dict) -> None:
        broadcasts.append(msg)

    with patch("src.db.novel_store.get_novel",
               AsyncMock(return_value={"total_chapters": 12})), \
         patch("src.services.visualization_service.invalidate_map_response_cache",
               AsyncMock()), \
         patch("src.services.visualization_service.get_map_data",
               AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("src.services.analysis_service.manager") as mgr:
        mgr.broadcast = AsyncMock(side_effect=fake_broadcast)
        await svc._auto_map_prebuild("novel-x")  # must not raise

    assert broadcasts == [
        {"type": "map_prebuild", "status": "running", "stage": "构建世界地图..."},
        {"type": "map_prebuild", "status": "error"},
    ]


@pytest.mark.asyncio
async def test_prebuild_skips_when_no_chapters():
    """无章节(或 novel 不存在)时直接返回,不广播、不调 get_map_data。"""
    svc = AnalysisService.__new__(AnalysisService)

    with patch("src.db.novel_store.get_novel", AsyncMock(return_value=None)), \
         patch("src.services.visualization_service.get_map_data",
               AsyncMock()) as gmd, \
         patch("src.services.analysis_service.manager") as mgr:
        mgr.broadcast = AsyncMock()
        await svc._auto_map_prebuild("novel-x")

    gmd.assert_not_awaited()
    mgr.broadcast.assert_not_awaited()
