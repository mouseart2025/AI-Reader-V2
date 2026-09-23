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
    reason="fresh rebuild 的 v0 从当前 ws 导入,Edmonds 以 ws parents 为 "
           "base(~632 条保留)+ MWA 全局重组,两个 base 态互为映射且带漂移"
           "(非严格周期 2);baseline 注入已被消融实验证明是减振稳定器而非"
           "驱动(vote_builder.py:226-234);震荡边含 6 条金标节点边"
           "(2026-09-22 证伪实验,详见测试 docstring)",
)
@pytest.mark.skipif(not _LIVE_DB.exists(), reason="需要真实库副本")
@pytest.mark.asyncio
async def test_rebuild_apply_idempotent_honglou(tmp_path, monkeypatch):
    """同书连跑两轮 rebuild+apply,逐边 diff 应为 0(幂等回归)。

    2026-09-22 证伪实验关键数据(scratch 副本,三轮 rebuild+apply):
    - 三轮 diff:r1→r2=125 边,r2→r3=112,r1→r3=37(漂移交替,非周期 2);
    - 票仓追踪:省亲别墅三轮票池恒为 {大观园: 4.50} 或 {大观园: 3.50}
      (±baseline 1 票),紫菱洲全程 0 票,但结果 紫菱洲↔大观园 交替;
    - 快照链定位:翻转发生在 v4 Edmonds——v0=大观园/票=大观园 4.5 全票,
      v4 仍被改挂 0 票的 紫菱洲(疑似成环规避/orphan fill);
    - baseline 消融:baseline_weight=0 两轮 diff=125,默认 w=1 diff=112
      ——baseline 是减振稳定器,不是驱动(vote_builder.py:226-234 同旨);
    - 金标区域亦未幸免:6 条金标节点边震荡(嘉荫堂/大明宫/省亲别墅/
      翠烟桥/芦雪广/芦雪庵,多集中在大观园区域)——红楼 fixture PP 在
      0.8511↔0.8958、chain 在 0.7447↔0.8085 之间的轮间漂移即此所致
      (金标子集守卫见下方 test_..._gold_region,当前 xfail 待修复)。
    """
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


@pytest.mark.skipif(not _LIVE_DB.exists(), reason="需要真实库副本")
@pytest.mark.asyncio
async def test_rebuild_apply_idempotent_honglou_gold_region(
        tmp_path, monkeypatch):
    """金标节点子集幂等守卫:两轮 rebuild+apply,fixture 金标节点的
    parents 逐边 diff 必须 = 0。

    2026-09-22 首测即红:6 条金标节点边震荡(嘉荫堂/大明宫/省亲别墅/
    翠烟桥/芦雪广/芦雪庵),大观园区域为主。此前"震荡全在非金标区域"
    的推断(由各轮 fixture 指标稳定得出)不成立——指标稳定只证明同输入
    确定性,跨 ws 起点的边级漂移早以 fixture PP 0.8511↔0.8958 轮间
    漂移的形式存在。真凶=_balance_degrees 票盲改挂(纯结构启发式不看
    票仓);修复=edmonds.zero_vote_reassign 守卫(当前 parent 有正票而
    候选 absorber 零票时禁止改挂)后本测试转绿。整图版(含非金标震荡)
    仍 xfail,不在本期范围。
    """
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
    fixture = json.loads(
        (Path(__file__).parent / "fixtures"
         / "golden_standard_dream_of_red_chamber.json").read_text(
            encoding="utf-8"))
    gold_names = {loc["name"] for loc in fixture["locations"]}

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
    diff = {n for n in gold_names if first.get(n) != second.get(n)}
    assert not diff, (
        f"金标区域两轮非幂等,{len(diff)} 边震荡(严重性升级): "
        f"{sorted(diff)[:10]}")


def test_uber_root_fallback_parent_hub():
    """纯起点兜底:tier=world 缺失且 天下 不入 tiers 时,从 parents 值集
    识别枢纽 uber_root,Phase 0 收口 parent-only 散根,保持单根保证
    (三国/封神 2026-09-23 纯起点实测 roots=5/4 → 1)。"""
    ws = _ws(
        parents={
            "官渡": "天下", "赤壁": "天下",      # 天下:无 tier、不在 tiers 键
            "某县": "武陵城",                      # 武陵城:parent-only 散根
        },
        # 真实失败形态:散根 tier=None 不入 tiers;tiers 键全部有 parent,
        # tier=world 缺失 → 旧双兜底皆落空
        tiers={"官渡": "site", "赤壁": "site", "某县": "site"},
    )
    GeoOrchestrator._inject_layer_roots(ws, "三国演义")
    p = ws.location_parents
    # uber_root 识别为 天下(子节点最多),散根被收口
    # (Phase 0 挂 天下 后被图层分组改指 主世界,链终端仍是 天下)
    assert p["武陵城"] in ("天下", "主世界")
    # 全图单根:每个节点可沿父链到 天下
    def reaches_root(n):
        cur, seen = n, set()
        while cur in p and cur not in seen:
            seen.add(cur)
            cur = p[cur]
        return cur
    all_nodes = set(p) | set(p.values())
    roots = {n for n in all_nodes if n not in p}
    assert roots == {"天下"}
    for n in all_nodes - {"天下"}:
        assert reaches_root(n) == "天下"


def test_uber_root_tier_world_path_unchanged():
    """回归:tier=world 存在时走原路径,不触发新兜底(行为不变)。"""
    ws = _ws(
        parents={"京畿": "天下"},
        tiers={"天下": "world", "京畿": "region"},
    )
    GeoOrchestrator._inject_layer_roots(ws, "水浒传")
    assert ws.location_parents["京畿"] == "天下"   # 佐证边保持(免疫)
    assert "天下" not in ws.virtual_locations       # 水浒天下=真实节点
