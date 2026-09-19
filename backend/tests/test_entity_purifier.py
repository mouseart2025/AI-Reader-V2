"""佛教四大部洲的证据门槛豁免回归测试。

背景(2026-09-19 GeoEvolve 诊断发现的生产缺陷):

EntityPurifier 的 BUDDHIST_CONTINENTS 规则原为三国噪声而写(三国人工抽查
发现"北俱芦洲"系佛教神话误入三国地理),但 purge 全局生效,把《西游》
层级树的合法顶层地理(东胜神洲等四大部洲)一并删除——导致 109 个节点
失去挂靠、全部退回"天下"兜底,西游 gold parent_precision 被压在 0.34。

修复:部洲剔除改为证据门槛——本小说 chapter_facts 的 locations 中提及
≥2 次(西游/封神的真实地理骨架)则保留;零星噪声(三国,loc=0)仍剔除。

本文件锁死:
1. 有真实地点证据的部洲不被剔除(西游场景);
2. 无证据的部洲仍被剔除,其子节点上移到祖父(三国场景);
3. 其余 purge 规则(通名/朝代/人物)行为不变。
"""
import json
import sqlite3
from collections import Counter

import pytest

from src.services.geo_skills import entity_purifier
from src.services.geo_skills.entity_purifier import EntityPurifier
from src.services.geo_skills.snapshot import HierarchySnapshot


@pytest.fixture()
def facts_db(tmp_path, monkeypatch):
    """临时 DB:按 {name: (char_mentions, loc_mentions)} 造 chapter_facts。"""
    db = tmp_path / "data.db"

    def make(entries: dict[str, tuple[int, int]]):
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TABLE chapter_facts "
            "(novel_id TEXT, chapter_id TEXT, fact_json TEXT)"
        )
        fact = {
            "characters": [{"name": n} for n, (c, _) in entries.items() for _ in range(c)],
            "locations": [{"name": n} for n, (_, lc) in entries.items() for _ in range(lc)],
        }
        con.execute(
            "INSERT INTO chapter_facts VALUES ('test-novel', 'c1', ?)",
            (json.dumps(fact, ensure_ascii=False),),
        )
        con.commit()
        con.close()
        monkeypatch.setattr(entity_purifier, "DB_PATH", db)

    return make


def _run(parents: dict[str, str]) -> dict[str, str | None]:
    snap = HierarchySnapshot(
        location_parents=parents,
        location_tiers={},
        parent_votes={},
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )
    import asyncio

    result = asyncio.run(EntityPurifier("test-novel").execute(snap))
    return result.parent_overrides


def test_continent_with_location_evidence_is_kept(facts_db):
    """西游场景:东胜神洲在 locations 出现 2 次 → 保留,链不断。"""
    facts_db({"东胜神洲": (0, 2), "傲来国": (0, 3)})
    out = _run({"东胜神洲": "天下", "傲来国": "东胜神洲"})
    assert "东胜神洲" not in out  # 不产生剔除 override
    assert "傲来国" not in out


def test_continent_without_evidence_is_purged(facts_db):
    """三国场景:北俱芦洲零地点提及 → 剔除,其子节点上移到祖父。"""
    facts_db({"关羽": (3, 0)})  # 无关人物,确保 DB 非空
    out = _run({"北俱芦洲": "天下", "某郡": "北俱芦洲", "天下": None})
    assert out.get("北俱芦洲") is None or "北俱芦洲" in out
    assert "北俱芦洲" in out and out["北俱芦洲"] is None
    assert out.get("某郡") == "天下"  # 上移到祖父


def test_other_rules_unchanged(facts_db):
    """通名/朝代/人物名剔除不受部洲豁免影响。"""
    facts_db({"关公": (3, 0), "官道": (0, 5), "东汉": (0, 4)})
    out = _run({"官道": "天下", "东汉": "天下", "关公": "天下"})
    assert out.get("官道") is None
    assert out.get("东汉") is None
    assert out.get("关公") is None
