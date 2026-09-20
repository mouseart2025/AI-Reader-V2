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
                            parents: dict | None = None) -> dict[str, Counter]:
    skill = VoteBuilder("n1")
    result = await skill.execute(_snap(tiers, parents=parents))
    assert result.success, result.error_message
    return result.new_votes


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
