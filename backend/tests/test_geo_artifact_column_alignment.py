"""`save_geo_artifacts` 的列对齐 —— 一个静默数据串列的回归测试。

背景（真实事故）：store 的签名里 `geo_coords_json` 被插到了 `shelf_depth_json`
**之前**，而唯一调用点仍按老顺序传位置参数。结果每一次写入都把深度数组存进了
`geo_coords_json`，`shelf_depth_json` 一直是 NULL：

    geo_coords_json  = '[1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 0.0]'   <- 深度
    shelf_depth_json = NULL

写入成功、不报错；读取端拿到 `[]`；前端于是把 5 条浅滩带**全部按"近岸浅色"画**，
整个群岛外面套了一圈巨大的浅色光晕。已有的 `test_map_geo_artifacts.py` 没能拦住，
因为它的桩函数 `_fake_landmasses` 只返回 `landmasses`/`shelves`，压根没有 `shelf_depth`。

这个文件守住两件事：
1. 深度值必须落到**它自己那一列**，而 `geo_coords_json` 保持 NULL；
2. 两个尾参是**关键字专属**，位置传参必须 TypeError —— 让这类错位无法再静默发生。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.db import world_structure_store as store
from src.db.sqlite_db import init_db

NOVEL = "test-geo-column-align"
CH_HASH = "deadbeefdeadbeef"

_LAND = [{"id": "l0", "coastline": [[0, 0], [10, 0], [10, 10]], "holes": []}]
_SHELVES = [[[0, 0], [20, 0], [20, 20]]]
_RIVERS = [{"id": "r0", "path": [[1, 1], [2, 2]]}]
_ROADS = [{"from": "A", "to": "B", "path": [[1, 1], [2, 2]]}]
# 两个不同的值，这样"写错列"一定会被看出来
_DEPTH = [0.0, 0.5, 1.0]


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


async def _seed_novel() -> None:
    await init_db()
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO novels (id, title) VALUES (?, ?)", (NOVEL, "列对齐测试")
        )
        await conn.commit()
    finally:
        await conn.close()


def _save(**kwargs):
    return store.save_geo_artifacts(
        NOVEL, "overworld", CH_HASH,
        _j(_LAND), _j(_SHELVES), _j(_RIVERS), _j(_ROADS), **kwargs,
    )


async def _read_raw() -> tuple[str | None, str | None]:
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        cur = await conn.execute(
            "SELECT shelf_depth_json, geo_coords_json FROM map_geo_artifacts "
            "WHERE novel_id=? AND layer_id=? AND chapter_hash=?",
            (NOVEL, "overworld", CH_HASH),
        )
        row = await cur.fetchone()
    finally:
        await conn.close()
    assert row is not None, "写入后应当能读到这一行"
    return row[0], row[1]


def test_depth_lands_in_its_own_column_and_geo_coords_stays_null():
    async def run():
        await _seed_novel()
        await _save(shelf_depth_json=_j(_DEPTH))
        return await _read_raw()

    depth_json, geo_json = asyncio.run(run())
    assert json.loads(depth_json) == _DEPTH, "深度必须写进 shelf_depth_json"
    assert geo_json is None, "geo_coords_json 不该被顺带塞进任何东西"


def test_the_two_trailing_params_are_keyword_only():
    """位置传参必须直接报错 —— 静默串列就是这么发生的。"""
    async def run():
        await _seed_novel()
        with pytest.raises(TypeError):
            await store.save_geo_artifacts(
                NOVEL, "overworld", CH_HASH,
                _j(_LAND), _j(_SHELVES), _j(_RIVERS), _j(_ROADS), _j(_DEPTH),
            )

    asyncio.run(run())


def test_load_geo_artifacts_returns_the_depth_bands():
    """读回来的路径也要带上深度 —— 前端就是靠它决定每条带的颜色。"""
    async def run():
        await _seed_novel()
        await _save(shelf_depth_json=_j(_DEPTH))
        return await store.load_geo_artifacts(NOVEL, "overworld", CH_HASH)

    loaded = asyncio.run(run())
    assert loaded is not None
    assert loaded.get("shelf_depth") == _DEPTH
