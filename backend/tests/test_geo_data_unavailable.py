"""GeoNames 数据不可用时地图接口降级测试(issue #82)。

新装环境(无 geonames 数据)首次请求地图时,_ensure_data 逐个下载数据集,
慢网络下请求挂起 10+ 分钟无响应。修复后:
  - 单次下载有整体超时上限(_GEO_DOWNLOAD_OVERALL_TIMEOUT_S)
  - 下载/加载失败抛 GeoDataUnavailableError,调用方降级为虚构布局
  - 失败不留下残缺的 TSV 文件,下次请求可重试
"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from src.services import geo_resolver as gr


class _FailingClient:
    """httpx.AsyncClient 替身: get 直接抛网络错误。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        raise httpx.ConnectError("network unreachable")


class _SlowClient:
    """httpx.AsyncClient 替身: get 缓慢推进永不完成(模拟慢网络 trickle)。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        await asyncio.sleep(60)


@pytest.fixture
def empty_geonames_dir(tmp_path, monkeypatch):
    """把 GEONAMES_DIR 指到空临时目录,模拟全新安装。"""
    monkeypatch.setattr(gr, "GEONAMES_DIR", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_download_network_error_raises_unavailable(empty_geonames_dir, monkeypatch):
    monkeypatch.setattr(gr.httpx, "AsyncClient", _FailingClient)
    resolver = gr.GeoResolver("cn")
    with pytest.raises(gr.GeoDataUnavailableError):
        await resolver.ensure_ready()
    # 不留残缺文件 — 下次请求可重试下载
    assert not (empty_geonames_dir / "CN.txt").exists()


@pytest.mark.asyncio
async def test_download_overall_timeout_raises_unavailable(empty_geonames_dir, monkeypatch):
    """慢速但持续有数据的下载也必须在整体超时后失败,而不是一直挂起。"""
    monkeypatch.setattr(gr.httpx, "AsyncClient", _SlowClient)
    monkeypatch.setattr(gr, "_GEO_DOWNLOAD_OVERALL_TIMEOUT_S", 0.05)
    resolver = gr.GeoResolver("cn")
    with pytest.raises(gr.GeoDataUnavailableError):
        await resolver.ensure_ready()
    assert not (empty_geonames_dir / "CN.txt").exists()


@pytest.mark.asyncio
async def test_index_load_failure_raises_unavailable(empty_geonames_dir, monkeypatch):
    """TSV 已存在但解析失败(截断/损坏)同样转为 GeoDataUnavailableError。"""
    (empty_geonames_dir / "CN.txt").write_text("broken", encoding="utf-8")
    monkeypatch.setattr(
        gr.GeoResolver, "_load_index",
        lambda self: (_ for _ in ()).throw(OSError("corrupt tsv")),
    )
    resolver = gr.GeoResolver("cn")
    with pytest.raises(gr.GeoDataUnavailableError):
        await resolver.ensure_ready()


@pytest.mark.asyncio
async def test_auto_resolve_propagates_unavailable(empty_geonames_dir, monkeypatch):
    """auto_resolve 不吞 GeoDataUnavailableError — 由调用方降级为虚构布局,
    且不持久化 geo_type(下次请求重试下载)。"""
    monkeypatch.setattr(
        gr.GeoResolver, "ensure_ready",
        AsyncMock(side_effect=gr.GeoDataUnavailableError("no data")),
    )
    with pytest.raises(gr.GeoDataUnavailableError):
        await gr.auto_resolve(
            "historical", ["荆州", "许都"], ["荆州"],
            known_geo_type="realistic",
        )


@pytest.mark.asyncio
async def test_auto_resolve_normal_path_propagates_unavailable(empty_geonames_dir, monkeypatch):
    """首次检测路径(无缓存 geo_type)同样向上抛,不返回伪 'fantasy'。"""
    monkeypatch.setattr(
        gr.GeoResolver, "ensure_ready",
        AsyncMock(side_effect=gr.GeoDataUnavailableError("no data")),
    )
    with pytest.raises(gr.GeoDataUnavailableError):
        await gr.auto_resolve("historical", ["荆州", "许都"], ["荆州"])
