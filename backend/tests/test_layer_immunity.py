"""Tests for credentialed-edge immunity in _inject_layer_roots and
virtual-aware topology scoring (2026-09-22 rebuilt-vs-applied 归因修复)."""

import json
from pathlib import Path

import pytest

from src.models.world_structure import LayerType, MapLayer, WorldStructure
from src.services.geo_skills.orchestrator import GeoOrchestrator


def _set_params(tmp_path: Path, monkeypatch, params: dict) -> None:
    from src.services.geo_skills import evolve_params as ep

    p = tmp_path / "params.json"
    p.write_text(json.dumps(params), encoding="utf-8")
    monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
    ep.reset_cache()


def _ws(parents: dict, tiers: dict, layer_map: dict | None = None,
        layers: list | None = None) -> WorldStructure:
    if layers is None:
        layers = [
            MapLayer(layer_id="overworld", name="主世界",
                     layer_type=LayerType.overworld),
            MapLayer(layer_id="underwater", name="海底",
                     layer_type=LayerType.underwater),
        ]
    return WorldStructure(
        novel_id="test",
        layers=layers,
        location_parents=dict(parents),
        location_tiers=dict(tiers),
        location_layer_map=dict(layer_map or {}),
    )


# ── 资信边免疫:图层分组 ────────────────────────────────────────────


def test_immunity_grouping_skips_credentialed():
    """水浒:金标佐证的 京畿→天下 不参与 主世界 分组;无佐证节点照常分组;
    主世界 虚拟根本身仍创建。"""
    ws = _ws(
        parents={"京畿": "天下", "淮西某州": "天下", "荆南某郡": "天下"},
        tiers={"天下": "world", "京畿": "region", "淮西某州": "region",
               "荆南某郡": "region"},
    )
    GeoOrchestrator._inject_layer_roots(ws, "水浒传")
    assert ws.location_parents["京畿"] == "天下"          # 佐证边豁免
    assert ws.location_parents["淮西某州"] == "主世界"     # 非佐证照常分组
    assert ws.location_parents["荆南某郡"] == "主世界"
    assert ws.location_parents["主世界"] == "天下"         # 虚拟根仍创建
    assert "主世界" in ws.virtual_locations


def test_immunity_grouping_disabled_reverts(tmp_path, monkeypatch):
    """开关关闭:恢复旧行为,佐证边同样被分组。"""
    _set_params(tmp_path, monkeypatch,
                {"layer.credentialed_edge_immunity": False})
    ws = _ws(
        parents={"京畿": "天下", "淮西某州": "天下"},
        tiers={"天下": "world", "京畿": "region", "淮西某州": "region"},
    )
    GeoOrchestrator._inject_layer_roots(ws, "水浒传")
    assert ws.location_parents["京畿"] == "主世界"
    assert ws.location_parents["淮西某州"] == "主世界"


# ── 资信边免疫:Phase A 跨层解挂 ─────────────────────────────────────


def test_immunity_phaseA_skips_credentialed_cross_layer():
    """西游:金标佐证的跨层边 龙宫→东海 不解挂;无佐证的跨层边照常解挂。"""
    ws = _ws(
        parents={"龙宫": "东海", "某荒潭": "东海", "东海": "天下"},
        tiers={"天下": "world", "东海": "region", "龙宫": "site",
               "某荒潭": "site"},
        layer_map={"龙宫": "underwater", "某荒潭": "underwater"},
    )
    GeoOrchestrator._inject_layer_roots(ws, "西游记")
    assert ws.location_parents["龙宫"] == "东海"      # 佐证边豁免
    assert ws.location_parents["某荒潭"] == "天下"     # 非佐证照常解挂


# ── 资信孤儿补挂:Phase 0 ────────────────────────────────────────────


def test_immunity_phase0_attaches_credentialed_parent():
    """红楼:金标名 芦雪庵(fixture 芦雪庵→大观园)补挂到 大观园,
    不再误挂 主世界;无资信孤儿仍走 uber_root/分组兜底。"""
    ws = _ws(
        parents={"大观园": "天下", "某无名轩外": "天下"},
        tiers={"天下": "world", "大观园": "region", "芦雪庵": "site",
               "某无名轩外": "site", "某无考庵": "site"},
    )
    GeoOrchestrator._inject_layer_roots(ws, "红楼梦")
    assert ws.location_parents["芦雪庵"] == "大观园"   # 资信补挂
    assert "某无考庵" in ws.location_parents           # 无资信仍兜底挂上
    assert ws.location_parents["某无考庵"] in ("天下", "主世界")


def test_immunity_phase0_cycle_guard():
    """资信 parent 会成环时不挂该 parent,回退 uber_root 兜底。"""
    # 人为构造:测试名不得出现在任何书的资信表中,改用 monkeypatch 免疫表
    from src.services.geo_skills import credentialed_edges as ce

    ce.reset_credentialed_cache()
    orig = ce.credentialed_edges

    def fake(title):
        return frozenset({("甲", "乙"), ("乙", "甲")})

    ce.credentialed_edges = fake
    try:
        ws = _ws(
            parents={"乙": "天下"},
            tiers={"天下": "world", "甲": "region", "乙": "region"},
        )
        # 乙→甲 已存在(假注资信),甲 无 parent;若按资信 甲→乙,乙→甲→乙…
        # 不成环(乙→天下)。真正成环情形:把 乙 的 parent 改成 甲 不可,
        # 改为直接构造 乙→甲 且 甲 的资信 parent=乙:
        ws = _ws(
            parents={"乙": "甲"},
            tiers={"天下": "world", "甲": "region", "乙": "region"},
        )
        GeoOrchestrator._inject_layer_roots(ws, "测试")
        # 甲 挂 乙 会形成 甲→乙→甲 环,应回退
        assert ws.location_parents["甲"] != "乙"
    finally:
        ce.credentialed_edges = orig
        ce.reset_credentialed_cache()


# ── 虚拟感知评分 ────────────────────────────────────────────────────

_GOLDEN = [
    {"name": "高太尉府", "correct_parent": "东京", "tier": "site"},
    {"name": "东京", "correct_parent": "京畿", "tier": "city"},
    {"name": "京畿", "correct_parent": "天下", "tier": "region"},
    {"name": "天下", "correct_parent": None, "tier": "world"},
]


def test_virtual_aware_scoring_penetrates():
    """京畿→主世界(virtual)→天下 穿透后计作 京畿→天下:PP/chain 收复。"""
    from src.utils.spatial_quality import compute_topology_metrics_virtual_aware
    from src.utils.topology_metrics import compute_topology_metrics

    predicted = {"高太尉府": "东京", "东京": "京畿",
                 "京畿": "主世界", "主世界": "天下"}
    raw = compute_topology_metrics(predicted, _GOLDEN)
    assert raw["parent_precision"] < 1.0      # 京畿→主世界 被判错
    aware = compute_topology_metrics_virtual_aware(
        predicted, _GOLDEN, {"主世界"})
    assert aware["parent_precision"] == 1.0
    assert aware["chain_accuracy"] == 1.0
    assert aware["parent_recall"] == 1.0


def test_virtual_aware_scoring_empty_virtual_identical():
    """virtual 为空:与原函数逐边一致。"""
    from src.utils.spatial_quality import compute_topology_metrics_virtual_aware
    from src.utils.topology_metrics import compute_topology_metrics

    predicted = {"高太尉府": "东京", "东京": "京畿",
                 "京畿": "主世界", "主世界": "天下"}
    assert (compute_topology_metrics_virtual_aware(predicted, _GOLDEN, set())
            == compute_topology_metrics(predicted, _GOLDEN))
    assert (compute_topology_metrics_virtual_aware(predicted, _GOLDEN, None)
            == compute_topology_metrics(predicted, _GOLDEN))


def test_resolve_virtual_parents_cycle_safe():
    """virtual 链成环时不死循环,保留可达的最近非 virtual parent。"""
    from src.utils.spatial_quality import resolve_virtual_parents

    resolved = resolve_virtual_parents(
        {"甲": "V1", "V1": "V2", "V2": "V1"}, {"V1", "V2"})
    assert resolved["甲"] in ("V1", "V2")     # 终止即可,不挂起


# ── 震荡回归(立项附带,当前预期失败) ─────────────────────────────────

_LIVE_DB = Path.home() / ".ai-reader-v2" / "data.db"


@pytest.mark.xfail(
    strict=False,
    reason="baseline 注入使 rebuild 依赖当前 ws 态:红楼 省亲别墅 等边"
           "大观园↔紫菱洲 逐轮交替(2026-09-22 归因机制③),立项未修",
)
@pytest.mark.skipif(not _LIVE_DB.exists(), reason="需要真实库副本")
@pytest.mark.asyncio
async def test_rebuild_apply_idempotent_honglou(tmp_path, monkeypatch):
    """同书连跑两轮 rebuild+apply,逐边 diff 应为 0(幂等回归)。"""
    import shutil

    import aiosqlite

    scratch = tmp_path / "data.db"
    shutil.copy(_LIVE_DB, scratch)
    monkeypatch.setenv("AI_READER_DATA_DIR", str(tmp_path))

    import importlib

    import src.infra.config as cfg
    importlib.reload(cfg)
    import src.db.sqlite_db as sdb
    importlib.reload(sdb)
    from src.services.geo_skills import orchestrator as orch_mod
    importlib.reload(orch_mod)

    nid = "c384901a-8b71-437a-af35-b5ec1c56c696"

    async def cycle() -> dict:
        orch = orch_mod.build_default_orchestrator(nid, novel_title="红楼梦")
        async for _ in orch.run(fresh=True):
            pass
        await orch.apply_to_world_structure()
        async with aiosqlite.connect(scratch) as conn:
            row = await conn.execute(
                "SELECT structure_json FROM world_structures WHERE novel_id=?",
                (nid,))
            ws = json.loads((await row.fetchone())[0])
        return ws.get("location_parents", {})

    first = await cycle()
    second = await cycle()
    diff = {k for k in set(first) | set(second) if first.get(k) != second.get(k)}
    assert not diff, f"两轮 rebuild+apply 非幂等,{len(diff)} 边震荡: {sorted(diff)[:10]}"
