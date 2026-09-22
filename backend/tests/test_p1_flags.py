"""P1 增强组件(evolve_param 开关,默认关=零行为变化)正反向单测。

  A1 votes.confidence_score_blend — contains/located_in 票乘 (0.5+score)
  A2 votes.primary_setting_discount — 主场景推断票乘性折扣
  B  votes.conflict_decay — TSDF 式冲突降权(整组降置信,不删票)
  C  auditor.enabled / auditor.report_only — AuditorSkill 入网门禁

全部用内存数据:VoteBuilder 走 conftest memory_db(打入最小
novels/chapters/chapter_facts 行),AuditorSkill 直接构造快照
(virtual_roots 构造注入,不触 DB)。
"""

import asyncio
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from src.services.geo_skills import evolve_params as ep
from src.services.geo_skills.auditor_skill import AuditorSkill
from src.services.geo_skills.snapshot import HierarchySnapshot
from src.services.geo_skills.vote_builder import VoteBuilder


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("EVOLVE_PARAMS_JSON", raising=False)
    ep.reset_cache()
    yield
    ep.reset_cache()


def _set_params(tmp_path: Path, monkeypatch, params: dict) -> None:
    p = tmp_path / "params.json"
    p.write_text(json.dumps(params), encoding="utf-8")
    monkeypatch.setenv("EVOLVE_PARAMS_JSON", str(p))
    ep.reset_cache()


@pytest_asyncio.fixture
async def facts_db(memory_db):
    """Patch sqlite_db.get_connection to the shared in-memory DB."""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def _factory():
        return _NonClosing(memory_db)

    with patch("src.db.sqlite_db.get_connection", _factory):
        yield memory_db


async def _seed_facts(db, facts: list[dict], novel_id: str = "n1") -> None:
    await db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (novel_id, "测试"))
    for i, fact in enumerate(facts, start=1):
        cur = await db.execute(
            "INSERT INTO chapters (novel_id, chapter_num, title, content) "
            "VALUES (?, ?, ?, ?)", (novel_id, i, f"第{i}回", "正文"))
        chapter_id = cur.lastrowid
        fact = dict(fact, chapter_id=chapter_id)
        await db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES (?, ?, ?)", (novel_id, chapter_id, json.dumps(fact)))
    await db.commit()


def _snap(tiers: dict, parents: dict | None = None,
          votes: dict | None = None) -> HierarchySnapshot:
    return HierarchySnapshot(
        location_parents=parents or {},
        location_tiers=tiers,
        parent_votes={k: Counter(v) for k, v in (votes or {}).items()},
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )


async def _run_vote_builder(facts: list[dict], tiers: dict,
                            parents: dict | None = None,
                            novel_title: str = "") -> dict[str, Counter]:
    skill = VoteBuilder("n1", novel_title=novel_title)
    result = await skill.execute(_snap(tiers, parents=parents))
    assert result.success, result.error_message
    return result.new_votes


# ── 地名别名归一(geo_alias.enabled / LOCATION_ALIAS_MAP) ───────────

_ALIAS_FACTS = [
    # 东京系三方分摊:同一 child 三章分别挂 东京/京师/汴梁城
    {"locations": [{"name": "高太尉府", "parent": "东京"}]},
    {"locations": [{"name": "高太尉府", "parent": "京师"}]},
    {"locations": [{"name": "高太尉府", "parent": "汴梁城"}]},
    # 北京系:梁中书府→大名府(应并入 北京)
    {"locations": [{"name": "梁中书府", "parent": "大名府"}]},
    # 未收录名:不受影响
    {"locations": [{"name": "五台山僧堂", "parent": "五台山"}]},
]
_ALIAS_TIERS = {"高太尉府": "site", "东京": "city", "京师": "city",
                "汴梁城": "city", "梁中书府": "building", "大名府": "city",
                "北京": "city", "五台山僧堂": "building", "五台山": "region"}


@pytest.mark.asyncio
async def test_alias_merges_votes_into_canonical(facts_db):
    """东京系三方票合流为一个 Counter;北京系并入 北京;未收录名不动。"""
    await _seed_facts(facts_db, _ALIAS_FACTS)
    votes = await _run_vote_builder(_ALIAS_FACTS, _ALIAS_TIERS,
                                    novel_title="水浒传")
    # 5 章 chapter_weight 1.0~1.4:前三章票全部合到 东京;京师/汴梁城 不成键
    assert votes["高太尉府"]["东京"] == pytest.approx(1.0 + 1.1 + 1.2)
    assert "京师" not in votes["高太尉府"]
    assert "汴梁城" not in votes["高太尉府"]
    assert votes["梁中书府"]["北京"] == pytest.approx(1.3)
    assert "大名府" not in votes["梁中书府"]
    assert votes["五台山僧堂"]["五台山"] == pytest.approx(1.4)


@pytest.mark.asyncio
async def test_alias_disabled_keeps_raw_names(facts_db, tmp_path, monkeypatch):
    """开关关闭:与现状逐边一致(别名各自成键,不合流)。"""
    await _seed_facts(facts_db, _ALIAS_FACTS)
    _set_params(tmp_path, monkeypatch, {"geo_alias.enabled": False})
    votes = await _run_vote_builder(_ALIAS_FACTS, _ALIAS_TIERS,
                                    novel_title="水浒传")
    assert votes["高太尉府"]["东京"] == pytest.approx(1.0)
    assert votes["高太尉府"]["京师"] == pytest.approx(1.1)
    assert votes["高太尉府"]["汴梁城"] == pytest.approx(1.2)
    assert votes["梁中书府"]["大名府"] == pytest.approx(1.3)


def test_alias_prior_self_loop_removed():
    """汴梁城→东京 先验边映射后 self-loop 剔除;北京→河北 只投一次(w=20);
    东京→京畿 不重复(京师→京畿 已迁)。"""
    from src.services.geo_skills.knowledge_prior import KnowledgePrior

    snap = _snap(
        tiers={"东京": "city", "京畿": "region", "河北": "region",
               "北京": "city", "汴梁城": "city", "京师": "city",
               "大名府": "city", "北京大名府": "city",
               "梁中书府": "building"},
        votes={"汴梁城": {"东京": 3.0}, "北京": {"河北": 5.0}},
    )
    result = asyncio.run(KnowledgePrior("水浒传").execute(snap))
    assert result.success
    assert "汴梁城" not in result.new_votes  # self-loop 整条不投
    assert ("汴梁城", "东京") not in result.prior_edges
    assert result.new_votes["北京"]["河北"] == 20  # 一票,不累加
    assert result.new_votes["东京"]["京畿"] == 20  # 一票(京师→京畿 已迁)
    assert "大名府" not in result.new_votes
    assert "北京大名府" not in result.new_votes
    # 表中残留别名写法的 parent(梁中书府→北京大名府)被短路到 canonical
    assert result.new_votes["梁中书府"]["北京"] == 20


@pytest.mark.asyncio
async def test_alias_baseline_injection_canonical(facts_db):
    """baseline 注入按 canonical 对齐 evidence:旧边 高太尉府→京师 有
    (高太尉府→东京) 证据时,票注到 canonical 边;汴梁城→东京 self-loop 跳过。"""
    await _seed_facts(facts_db, _ALIAS_FACTS)
    votes = await _run_vote_builder(
        _ALIAS_FACTS, _ALIAS_TIERS, novel_title="水浒传",
        parents={"高太尉府": "京师", "汴梁城": "东京", "京师": "京畿"})
    # 旧别名边 (高太尉府→京师) canonical 对齐后有证据 → baseline +1 注到 东京
    assert votes["高太尉府"]["东京"] == pytest.approx(1.0 + 1.1 + 1.2 + 1.0)
    # 汴梁城→东京 self-loop 跳过,不产生 东京→东京 票
    assert "东京" not in votes.get("东京", {})


def test_revisit_alias_canonicalizes_conflicts():
    """revisit 指标:同一 city 三章三个异名挂不同 parent,原始口径计冲突,
    canonical 口径归一后冲突消除。"""
    from src.utils.spatial_quality import compute_revisit_consistency

    facts = [
        {"chapter_id": i, "spatial_relationships": [{
            "source": "高太尉府", "target": t, "relation_type": "located_in",
        }]} for i, t in enumerate(["东京", "京师", "汴梁城"], start=1)
    ]
    raw = compute_revisit_consistency(facts)
    assert raw["parent_conflicts"] == 1
    ali = compute_revisit_consistency(
        facts, alias_map={"京师": "东京", "汴梁城": "东京"})
    assert ali["parent_conflicts"] == 0
    assert ali["parent_consistency"] == 1.0


# 扩书条目票合流(2026-09-22):每本至少一条
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title, child, alias, canonical",
    [
        ("西游记", "火云洞", "六百里钻头号山", "号山"),
        ("红楼梦", "荣国府", "神京", "都中"),
        ("封神演义", "银安殿", "闻太师府", "太师府"),
    ],
)
async def test_alias_expansion_books_merge(
        facts_db, title, child, alias, canonical):
    """西游/红楼/封神新收录条目:别名票合流到 canonical,别名不成键。"""
    facts = [
        {"locations": [{"name": child, "parent": alias}]},
        {"locations": [{"name": child, "parent": canonical}]},
    ]
    tiers = {child: "site", alias: "region", canonical: "region"}
    await _seed_facts(facts_db, facts)
    votes = await _run_vote_builder(facts, tiers, novel_title=title)
    # 2 章 chapter_weight 1.0 / 1.25 全部合到 canonical
    assert votes[child][canonical] == pytest.approx(2.25)
    assert alias not in votes[child]


# ── apply 层别名归并(geo_alias.apply_merge / apply_alias_merge) ──────

_APPLY_PARENTS = {
    # canonical 链
    "东京": "京畿", "京畿": "主世界", "北京": "河北", "河北": "主世界",
    # 别名节点及其子节点
    "汴梁城": "东京",          # 佐证保留边(fixture)
    "金明池": "汴梁城", "香椒铺": "汴梁城",
    "京师": "京畿",            # 无佐证 → 删边、摘壳
    "大名府": "河北",          # 佐证保留边(fixture)
    "大名府留守司": "大名府",
    "北京大名府": "主世界",     # 无佐证 → 删边、摘壳
    "徐宁宅": "北京大名府", "店肆": "北京大名府",
    # 未收录名:不受影响
    "五台山僧堂": "五台山", "五台山": "河东",
}


def test_apply_alias_merge_repoints_children():
    """别名节点的 children 改指 canonical;canonical 链与非别名边不动。"""
    from src.services.geo_skills.orchestrator import apply_alias_merge

    merged, report = apply_alias_merge(dict(_APPLY_PARENTS), "水浒传")
    assert merged["金明池"] == "东京"
    assert merged["香椒铺"] == "东京"
    assert merged["大名府留守司"] == "北京"
    assert merged["徐宁宅"] == "北京"
    assert merged["店肆"] == "北京"
    assert merged["东京"] == "京畿"      # canonical 链不变
    assert merged["北京"] == "河北"
    assert merged["五台山僧堂"] == "五台山"  # 未收录名不动
    assert len(report["repointed_children"]) == 5


def test_apply_alias_merge_edge_attestation():
    """佐证边(汴梁城→东京/大名府→河北)保留;无佐证边
    (北京大名府→主世界/京师→京畿)删除,空壳别名节点摘除。"""
    from src.services.geo_skills.orchestrator import apply_alias_merge

    merged, report = apply_alias_merge(dict(_APPLY_PARENTS), "水浒传")
    assert merged["汴梁城"] == "东京"    # 保留
    assert merged["大名府"] == "河北"    # 保留
    assert "京师" not in merged          # 摘壳
    assert "北京大名府" not in merged    # 摘壳
    assert ("京师", "京畿") in report["removed_edges"]
    assert ("北京大名府", "主世界") in report["removed_edges"]
    assert set(report["kept_alias_edges"]) == {("汴梁城", "东京"), ("大名府", "河北")}
    assert set(report["removed_alias_nodes"]) == {"京师", "北京大名府"}
    # 无 self-loop、无指向别名节点的残留边
    assert all(c != p for c, p in merged.items())
    assert not set(merged.values()) & {"京师", "汴梁城", "大名府", "北京大名府"}


def test_apply_alias_merge_disabled_noop(tmp_path, monkeypatch):
    """开关关闭:parent 表原样返回(逐边一致),报告为 None。"""
    from src.services.geo_skills.orchestrator import apply_alias_merge

    _set_params(tmp_path, monkeypatch, {"geo_alias.apply_merge": False})
    merged, report = apply_alias_merge(dict(_APPLY_PARENTS), "水浒传")
    assert report is None
    assert merged == _APPLY_PARENTS


def test_apply_alias_merge_empty_table_noop():
    """表为空的小说(三国:2026-09-22 扩书挖掘零收录):零行为。"""
    from src.services.geo_skills.orchestrator import apply_alias_merge

    merged, report = apply_alias_merge(dict(_APPLY_PARENTS), "三国演义")
    assert report is None
    assert merged == _APPLY_PARENTS


# ── A1: confidence_score_blend ──────────────────────────────────────

_A1_FACTS = [{
    "locations": [{"name": "花果山"}],
    "spatial_relationships": [{
        "source": "傲来国", "target": "花果山",
        "relation_type": "contains", "confidence": "high",
        "confidence_score": 0.9,
    }],
}]
_A1_TIERS = {"傲来国": "kingdom", "花果山": "region"}


@pytest.mark.asyncio
async def test_a1_off_ignores_confidence_score(facts_db):
    await _seed_facts(facts_db, _A1_FACTS)
    votes = await _run_vote_builder(_A1_FACTS, _A1_TIERS)
    # 关:high=2 × chapter_weight(1.0),confidence_score 不参与
    assert votes["花果山"]["傲来国"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_a1_on_blends_confidence_score(facts_db, tmp_path, monkeypatch):
    await _seed_facts(facts_db, _A1_FACTS)
    _set_params(tmp_path, monkeypatch, {"votes.confidence_score_blend": True})
    votes = await _run_vote_builder(_A1_FACTS, _A1_TIERS)
    # 开:2 × (0.5 + 0.9) = 2.8
    assert votes["花果山"]["傲来国"] == pytest.approx(2.8)


@pytest.mark.asyncio
async def test_a1_on_without_score_falls_back(facts_db, tmp_path, monkeypatch):
    facts = [{
        "locations": [{"name": "花果山"}],
        "spatial_relationships": [{
            "source": "傲来国", "target": "花果山",
            "relation_type": "contains", "confidence": "high",
        }],
    }]
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.confidence_score_blend": True})
    votes = await _run_vote_builder(facts, _A1_TIERS)
    # 开但无 confidence_score → 回退原档位权重
    assert votes["花果山"]["傲来国"] == pytest.approx(2.0)


# ── A2: primary_setting_discount ────────────────────────────────────

_A2_FACTS = [{
    "locations": [
        {"name": "青龙山", "role": "setting"},
        {"name": "山神庙", "role": "scene"},
    ],
}]
_A2_TIERS = {"青龙山": "region", "山神庙": "building"}


@pytest.mark.asyncio
async def test_a2_default_is_unchanged(facts_db):
    await _seed_facts(facts_db, _A2_FACTS)
    votes = await _run_vote_builder(_A2_FACTS, _A2_TIERS)
    # 默认 discount=1.0:主场景推断票 = 2
    assert votes["山神庙"]["青龙山"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_a2_discount_applies(facts_db, tmp_path, monkeypatch):
    await _seed_facts(facts_db, _A2_FACTS)
    _set_params(tmp_path, monkeypatch, {"votes.primary_setting_discount": 0.5})
    votes = await _run_vote_builder(_A2_FACTS, _A2_TIERS)
    assert votes["山神庙"]["青龙山"] == pytest.approx(1.0)


# ── B: conflict_decay (TSDF 式冲突降权) ─────────────────────────────

def _conflict_facts(n_a: int, n_b: int) -> list[dict]:
    """丙村 前 n_a 章挂 丁郡、后 n_b 章挂 戊郡 的直接证据。"""
    return (
        [{"locations": [{"name": "丙村", "parent": "丁郡"}]} for _ in range(n_a)]
        + [{"locations": [{"name": "丙村", "parent": "戊郡"}]} for _ in range(n_b)]
    )

_B_TIERS = {"丙村": "city", "丁郡": "region", "戊郡": "region"}


@pytest.mark.asyncio
async def test_b_default_decay_is_unchanged(facts_db):
    facts = _conflict_facts(2, 2)
    await _seed_facts(facts_db, facts)
    votes = await _run_vote_builder(facts, _B_TIERS)
    # 4 章 chapter_weight: 1.0, 1.125, 1.25, 1.375
    assert votes["丙村"]["丁郡"] == pytest.approx(1.0 + 1.125)
    assert votes["丙村"]["戊郡"] == pytest.approx(1.25 + 1.375)


@pytest.mark.asyncio
async def test_b_decay_halves_conflicted_child(facts_db, tmp_path, monkeypatch):
    facts = _conflict_facts(2, 2)
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.conflict_decay": 0.5})
    votes = await _run_vote_builder(facts, _B_TIERS)
    # top1/top2 = 2.625/2.125 ≈ 1.24 < 2,双方各 ≥2 章 → 整组 ×0.5
    assert votes["丙村"]["丁郡"] == pytest.approx((1.0 + 1.125) * 0.5)
    assert votes["丙村"]["戊郡"] == pytest.approx((1.25 + 1.375) * 0.5)


@pytest.mark.asyncio
async def test_b_no_decay_when_top2_lacks_chapters(
        facts_db, tmp_path, monkeypatch):
    facts = _conflict_facts(2, 1)  # 戊郡 仅 1 章证据
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.conflict_decay": 0.5})
    votes = await _run_vote_builder(facts, _B_TIERS)
    # 3 章 chapter_weight: 1.0, 1.1667, 1.3333
    assert votes["丙村"]["丁郡"] == pytest.approx(1.0 + 1.0 + 0.5 / 3)
    assert votes["丙村"]["戊郡"] == pytest.approx(1.0 + 0.5 * 2 / 3)


@pytest.mark.asyncio
async def test_b_baseline_votes_not_decayed(facts_db, tmp_path, monkeypatch):
    facts = _conflict_facts(2, 2)
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.conflict_decay": 0.5})
    # 既有 parent 丁郡(有证据对 → baseline 注入 +1)不参与降权;
    # parents 链到 天下,避免 丁郡 被当成 uber_root 触发票封顶。
    votes = await _run_vote_builder(
        facts, _B_TIERS, parents={"丙村": "丁郡", "丁郡": "天下"})
    assert votes["丙村"]["丁郡"] == pytest.approx(1.0 + 1.125 + 1.0)
    assert votes["丙村"]["戊郡"] == pytest.approx((1.25 + 1.375) * 0.5)


# ── E: single_source_discount(单章孤证降权) ───────────────────────

@pytest.mark.asyncio
async def test_e_default_discount_is_unchanged(facts_db):
    facts = _conflict_facts(1, 0)  # 丙村→丁郡 仅 1 章
    await _seed_facts(facts_db, facts)
    votes = await _run_vote_builder(facts, _B_TIERS)
    # 默认 1.0:单章票全价(chapter_weight=1.0)
    assert votes["丙村"]["丁郡"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_e_single_chapter_ticket_discounted(facts_db, tmp_path, monkeypatch):
    facts = _conflict_facts(1, 0)
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.single_source_discount": 0.5})
    votes = await _run_vote_builder(facts, _B_TIERS)
    assert votes["丙村"]["丁郡"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_e_multi_chapter_ticket_full_price(facts_db, tmp_path, monkeypatch):
    facts = _conflict_facts(2, 0)  # 同一对跨 2 章
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.single_source_discount": 0.5})
    votes = await _run_vote_builder(facts, _B_TIERS)
    # 2 章 corroboration 不降权:1.0 + 1.25
    assert votes["丙村"]["丁郡"] == pytest.approx(2.25)


@pytest.mark.asyncio
async def test_e_baseline_ticket_exempt(facts_db, tmp_path, monkeypatch):
    facts = _conflict_facts(1, 0)
    await _seed_facts(facts_db, facts)
    _set_params(tmp_path, monkeypatch, {"votes.single_source_discount": 0.5})
    # baseline 注入票所在 (child,parent) 整条豁免(有机 1.0 + baseline 1.0)
    votes = await _run_vote_builder(
        facts, _B_TIERS, parents={"丙村": "丁郡", "丁郡": "天下"})
    assert votes["丙村"]["丁郡"] == pytest.approx(2.0)


# ── C: AuditorSkill 入网门禁 ────────────────────────────────────────

def _run_auditor(snap: HierarchySnapshot, **kwargs) -> object:
    return asyncio.run(AuditorSkill(**kwargs).execute(snap))


def test_c_report_only_records_without_removal(tmp_path, monkeypatch):
    _set_params(tmp_path, monkeypatch, {"auditor.report_only": True})
    # 青州(kingdom, rank 2)挂在 李家村(site)下 → TIER_INVERSION
    snap = _snap(
        tiers={"青州": "kingdom", "李家村": "site"},
        parents={"青州": "李家村"},
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert result.parent_overrides == {}  # report_only=True:只记录不剔除
    audit = result.metadata["auditor"]
    assert audit["report_only"] is True
    assert any(v["code"] == "TIER_INVERSION" for v in audit["violations"])
    assert audit["removable"] == [{"edge": ["青州", "李家村"],
                                   "code": "TIER_INVERSION"}]


def test_c_default_enforces_and_chain_includes_auditor():
    """默认配置(无任何 EVOLVE_PARAMS_JSON):auditor 在标准链路中,
    且剔除 怡红院→东胜神洲 类 error 边(building 直挂 continent 宏观根)。"""
    from src.services.geo_skills.orchestrator import build_default_orchestrator

    orch = build_default_orchestrator("novel-x", novel_title="西游记")
    tags = [tag for tag, _ in orch._skills]
    assert "auditor" in tags
    assert tags.index("edmonds") < tags.index("auditor") < tags.index("suffix")

    # report_only 默认 False → 剔除生效
    snap = _snap(
        tiers={"怡红院": "building", "东胜神洲": "continent"},
        parents={"怡红院": "东胜神洲"},
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert result.metadata["auditor"]["report_only"] is False
    assert result.parent_overrides == {"怡红院": None}


def test_c_enforce_removes_violating_edge(tmp_path, monkeypatch):
    _set_params(tmp_path, monkeypatch, {"auditor.report_only": False})
    snap = _snap(
        tiers={"青州": "kingdom", "李家村": "site"},
        parents={"青州": "李家村"},
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert result.parent_overrides == {"青州": None}


def test_c_prior_edges_never_removed(tmp_path, monkeypatch):
    _set_params(tmp_path, monkeypatch, {"auditor.report_only": False})
    snap = _snap(
        tiers={"青州": "kingdom", "李家村": "site"},
        parents={"青州": "李家村"},
    )
    snap = HierarchySnapshot(
        location_parents=snap.location_parents,
        location_tiers=snap.location_tiers,
        parent_votes=snap.parent_votes,
        location_frequencies=snap.location_frequencies,
        chapter_settings=snap.chapter_settings,
        location_chapters=snap.location_chapters,
        prior_edges=frozenset({("青州", "李家村")}),
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert result.parent_overrides == {}
    assert result.metadata["auditor"]["prior_exempted"] == [
        {"edge": ["青州", "李家村"], "code": "TIER_INVERSION"}]


def test_c_homonym_ambiguous_marked_and_detached(tmp_path, monkeypatch):
    _set_params(tmp_path, monkeypatch, {"auditor.report_only": False})
    snap = _snap(
        tiers={"皇宫": "building", "赵都": "city", "魏都": "city"},
        parents={"皇宫": "赵都"},
        votes={"皇宫": {"赵都": 5.0, "魏都": 4.0}},  # top1/top2 = 1.25 < 2
    )
    result = _run_auditor(snap, virtual_roots=set())
    audit = result.metadata["auditor"]
    assert any(v["code"] == "HOMONYM_AMBIGUOUS" for v in audit["violations"])
    assert result.parent_overrides == {"皇宫": None}


def test_c_homonym_clear_winner_not_flagged():
    snap = _snap(
        tiers={"皇宫": "building", "赵都": "city", "魏都": "city"},
        parents={"皇宫": "赵都"},
        votes={"皇宫": {"赵都": 9.0, "魏都": 4.0}},  # 9/4 = 2.25 ≥ 2
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert not any(v["code"] == "HOMONYM_AMBIGUOUS"
                   for v in result.metadata["auditor"]["violations"])
    assert result.parent_overrides == {}


def test_c_deterministic_ordering():
    parents = {"青州": "李家村", "徐州": "王家庄"}
    tiers = {"青州": "kingdom", "徐州": "kingdom",
             "李家村": "site", "王家庄": "site"}
    r1 = _run_auditor(_snap(tiers=tiers, parents=parents), virtual_roots=set())
    r2 = _run_auditor(_snap(tiers=tiers, parents=parents), virtual_roots=set())
    assert r1.metadata["auditor"]["violations"] == \
        r2.metadata["auditor"]["violations"]


def test_c_refined_scale_skip_exempts_macro_containers(tmp_path, monkeypatch):
    """精修 SCALE_SKIP:僧堂→五台山(building→region)是常态,不剔。"""
    _set_params(tmp_path, monkeypatch, {"auditor.report_only": False})
    snap = _snap(
        tiers={"五台山僧堂": "building", "五台山": "region"},
        parents={"五台山僧堂": "五台山"},
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert result.parent_overrides == {}
    assert not any(v["code"] == "SCALE_SKIP"
                   for v in result.metadata["auditor"]["violations"])


def test_c_refined_scale_skip_still_flags_continent_root(
        tmp_path, monkeypatch):
    """精修 SCALE_SKIP:building 直挂 continent 宏观根仍是真跨级,剔除。"""
    _set_params(tmp_path, monkeypatch, {"auditor.report_only": False})
    snap = _snap(
        tiers={"怡红院": "building", "东胜神洲": "continent"},
        parents={"怡红院": "东胜神洲"},
    )
    result = _run_auditor(snap, virtual_roots=set())
    assert result.parent_overrides == {"怡红院": None}


def test_apply_alias_merge_xiyouji_keep_edges():
    """西游扩书:errata 佐证的全称别名边保留,错误节点(敕建宝林寺)摘除。"""
    from src.services.geo_skills.orchestrator import apply_alias_merge

    parents = {
        "号山": "西牛贺洲", "西牛贺洲": "主世界",
        "六百里钻头号山": "西牛贺洲",   # errata 正确@西牛贺洲 → 保留
        "石板桥": "六百里钻头号山",     # → 号山
        "八百里狮驼岭": "狮驼岭",       # 别名自指边,errata 正确 → 保留
        "南海落伽山": "南海",           # errata 正确@南海 → 保留
        "号山枯松涧": "号山",           # errata 正确@号山 → 保留
        "敕建宝林寺": "乌鸡国",         # errata 错误节点 → 删边摘壳
        "二层山门": "敕建宝林寺",       # → 宝林寺
        "宝林寺": "乌鸡国",
        "南膳部洲": "南赡部洲",         # 错字别名(2026-09-22 收)→ 删边摘壳
        "南赡部洲": "天下",
    }
    merged, report = apply_alias_merge(parents, "西游记")
    assert merged["六百里钻头号山"] == "西牛贺洲"
    assert merged["八百里狮驼岭"] == "狮驼岭"
    assert merged["南海落伽山"] == "南海"
    assert merged["号山枯松涧"] == "号山"
    assert merged["石板桥"] == "号山"        # 子节点归并
    assert merged["二层山门"] == "宝林寺"
    assert "敕建宝林寺" not in merged       # 摘壳
    assert "南膳部洲" not in merged         # 错字别名摘壳
    assert merged["南赡部洲"] == "天下"
    assert report["removed_alias_nodes"] == ["南膳部洲", "敕建宝林寺"]
