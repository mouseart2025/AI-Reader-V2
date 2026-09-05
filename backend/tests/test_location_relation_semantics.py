"""Tests for issue #70 「地点层级与关系语义加固」批次:

- D2: 地点 type 与名字后缀形态矛盾时软降级为「区域」(fact_validator)
- D3: adjacent/direction/in_between 不再产生 parent 传播票
      (world_structure_agent + geo_skills/vote_builder)
- D4: factions Source 3 到访 ≠ 成员(独立 visitors 输出)+ _is_org_type 收窄
- D5: OrgEventFact.evidence 可选字段向后兼容
"""

import json
import logging
from collections import Counter
from unittest.mock import patch

import pytest

from src.extraction.fact_validator import (
    FactValidator,
    _downgrade_inconsistent_location_type,
)
from src.models.chapter_fact import (
    ChapterFact,
    LocationFact,
    OrgEventFact,
    SpatialRelationship,
)
from src.models.world_structure import WorldStructure
from src.services.world_structure_agent import WorldStructureAgent
from src.services.visualization_service import _is_org_concept_category, _is_org_type


# ── D2: 地点 type 软校验 ──────────────────────────


class TestLocationTypeConsistency:
    """type 与名字后缀形态族明显矛盾 → 降级为「区域」+ logger.info。"""

    def test_ocean_typed_as_continent_downgraded(self, caplog):
        """海洋(type=大陆)→ 区域:issue #70 实测漂移案例。"""
        with caplog.at_level(logging.INFO, logger="src.extraction.fact_validator"):
            result = _downgrade_inconsistent_location_type("东海", "大陆")
        assert result == "区域"
        assert any("东海" in r.message for r in caplog.records)

    def test_street_typed_as_kingdom_downgraded(self):
        """街道(type=国)→ 区域。"""
        assert _downgrade_inconsistent_location_type("朱雀大街", "国") == "区域"

    def test_matching_type_unchanged(self):
        """形态一致时不做任何改动。"""
        assert _downgrade_inconsistent_location_type("花果山", "山") == "山"
        assert _downgrade_inconsistent_location_type("长安城", "城市") == "城市"
        assert _downgrade_inconsistent_location_type("东海", "海") == "海"
        assert _downgrade_inconsistent_location_type("灵霄宝殿", "宫殿") == "宫殿"

    def test_unknown_type_unchanged(self):
        """词表外的 type 不做判断(默认路径行为不变)。"""
        assert _downgrade_inconsistent_location_type("花果山", "圣地") == "圣地"

    def test_unknown_suffix_unchanged(self):
        """名字无可识别后缀时不做判断。"""
        assert _downgrade_inconsistent_location_type("怡红院X", "大陆") == "大陆"

    def test_city_name_exception_unchanged(self):
        """上海/丽江等以江/海/原结尾的城市不参与矛盾判断。"""
        assert _downgrade_inconsistent_location_type("上海", "城市") == "城市"
        assert _downgrade_inconsistent_location_type("丽江", "城市") == "城市"

    def test_ambiguous_fu_suffix_unchanged(self):
        """「府」双义(行政府 vs 宅邸),不作为矛盾依据:荣国府 type=府邸 不动。"""
        assert _downgrade_inconsistent_location_type("荣国府", "府邸") == "府邸"

    def test_region_type_passthrough(self):
        """type 已是「区域」时保持不动。"""
        assert _downgrade_inconsistent_location_type("东海", "区域") == "区域"

    def test_validate_locations_integration(self, caplog):
        """FactValidator._validate_locations 端到端:矛盾降级、正常不动。"""
        validator = FactValidator()
        locs = [
            LocationFact(name="东海", type="大陆"),
            LocationFact(name="花果山", type="山"),
        ]
        with caplog.at_level(logging.INFO, logger="src.extraction.fact_validator"):
            result = validator._validate_locations(locs)
        by_name = {loc.name: loc for loc in result}
        assert by_name["东海"].type == "区域"
        assert by_name["花果山"].type == "山"
        assert any(
            "Location type downgrade" in r.message for r in caplog.records
        )


# ── D3: 邻近 ≠ 包含 ──────────────────────────────


def _make_agent() -> WorldStructureAgent:
    agent = WorldStructureAgent("test-novel")
    agent.structure = WorldStructure.create_default("test-novel")
    return agent


class TestNeighborNotContainment:
    """adjacent/direction/in_between 不再产生 parent 传播票。"""

    def test_adjacent_no_parent_propagation(self):
        """B 有明确 parent 票,A 与 B adjacent → A 不得继承 B 的 parent。"""
        agent = _make_agent()
        # 预置:西街已有明确的 parent 票(长安城, ≥2)
        agent._parent_votes["西街"] = Counter({"长安城": 3})
        fact = ChapterFact(
            chapter_id=1,
            novel_id="test-novel",
            locations=[
                LocationFact(name="东街", type="街道"),
                LocationFact(name="西街", type="街道"),
                LocationFact(name="长安城", type="城市"),
            ],
            spatial_relationships=[
                SpatialRelationship(
                    source="东街", target="西街", relation_type="adjacent",
                    value="adjacent",
                ),
            ],
        )
        agent._apply_heuristic_updates(1, fact)
        assert "东街" not in agent._parent_votes

    def test_direction_no_parent_propagation(self):
        """direction 关系同样不产生 parent 票。"""
        agent = _make_agent()
        agent._parent_votes["青牛镇"] = Counter({"襄阳府": 4})
        fact = ChapterFact(
            chapter_id=1,
            novel_id="test-novel",
            locations=[
                LocationFact(name="青牛镇", type="城镇"),
                LocationFact(name="白沙镇", type="城镇"),
                LocationFact(name="襄阳府", type="府"),
            ],
            spatial_relationships=[
                SpatialRelationship(
                    source="白沙镇", target="青牛镇", relation_type="direction",
                    value="south_of",
                ),
            ],
        )
        agent._apply_heuristic_updates(1, fact)
        assert "白沙镇" not in agent._parent_votes

    def test_contains_still_produces_parent_vote(self):
        """明确的 contains 关系不受影响,仍产生 parent 票。"""
        agent = _make_agent()
        fact = ChapterFact(
            chapter_id=1,
            novel_id="test-novel",
            locations=[
                LocationFact(name="花果山", type="山"),
                LocationFact(name="水帘洞", type="洞府"),
            ],
            spatial_relationships=[
                SpatialRelationship(
                    source="花果山", target="水帘洞", relation_type="contains",
                    confidence="high",
                ),
            ],
        )
        agent._apply_heuristic_updates(1, fact)
        assert agent._parent_votes.get("水帘洞", Counter()).get("花果山", 0) > 0


class TestVoteBuilderNoNeighborPropagation:
    """VoteBuilder(geo_skills v2 管线)同步移除邻近传播。"""

    @pytest.mark.asyncio
    async def test_adjacent_no_propagation_contains_ok(self, memory_db):
        from src.services.geo_skills.snapshot import HierarchySnapshot
        from src.services.geo_skills.vote_builder import VoteBuilder

        await memory_db.execute(
            "INSERT INTO novels (id, title) VALUES ('nv1', '测试')"
        )
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content) "
            "VALUES (1, 'nv1', 1, '第一章', '')"
        )
        fact = {
            "chapter_id": 1,
            "novel_id": "nv1",
            "characters": [],
            "locations": [
                {"name": "花果山", "type": "山"},
                {"name": "水帘洞", "type": "洞府"},
                {"name": "傲来国", "type": "国"},
            ],
            "spatial_relationships": [
                # contains:花果山 包含 水帘洞(正常产票)
                {"source": "花果山", "target": "水帘洞",
                 "relation_type": "contains", "confidence": "high"},
                # adjacent:傲来国 邻接 花果山 —— 不应产生任何 parent 票
                {"source": "傲来国", "target": "花果山",
                 "relation_type": "adjacent", "value": "adjacent"},
            ],
            "events": [],
        }
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES ('nv1', 1, ?)",
            (json.dumps(fact, ensure_ascii=False),),
        )
        await memory_db.commit()

        snapshot = HierarchySnapshot(
            location_parents={},
            location_tiers={"花果山": "region", "水帘洞": "site", "傲来国": "kingdom"},
            parent_votes={},
            location_frequencies=Counter(),
            chapter_settings={},
            location_chapters={},
        )

        async def _factory():
            return memory_db

        with patch("src.db.sqlite_db.get_connection", _factory):
            result = await VoteBuilder("nv1").execute(snapshot)

        votes = result.new_votes
        # contains 正常产票
        assert votes.get("水帘洞", Counter()).get("花果山", 0) > 0
        # adjacent 双方都不因传播而获得 parent 票
        assert not votes.get("傲来国")
        assert votes.get("花果山", Counter()).get("傲来国", 0) == 0


# ── D4: 到访 ≠ 成员 ──────────────────────────────


class _NonClosingConnection:
    """包装共享 memory_db:业务代码里的 close() 变为 no-op。"""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


class TestIsOrgTypeNarrowed:
    """_is_org_type 收窄:地点形态词不再误判为组织。"""

    def test_org_types_still_match(self):
        assert _is_org_type("门派")
        assert _is_org_type("宗门")
        assert _is_org_type("帮派")
        assert _is_org_type("教派")
        assert _is_org_type("同盟")
        assert _is_org_type("军队")
        assert _is_org_type("朝廷")
        assert _is_org_type("家族")

    def test_place_form_words_no_longer_match(self):
        """国/府/会/阁/堂/殿/院 类地点形态词已收窄。"""
        assert not _is_org_type("王国")
        assert not _is_org_type("国家")
        assert not _is_org_type("府邸")
        assert not _is_org_type("宫殿")
        assert not _is_org_type("院落")
        assert not _is_org_type("会馆")

    def test_rongguofu_not_org(self):
        """荣国府类宅邸(type=府邸)不误判为组织。"""
        assert not _is_org_type("府邸")
        assert not _is_org_type("宅邸")

    def test_gate_types_not_org(self):
        """「门」指建筑(城门/宫门/营门)时不判 org:三国演义实测噪音源
        (嘉德门/东门/北掖门/吕布寨·辕门/荆州城门外)。"""
        assert not _is_org_type("城门")
        assert not _is_org_type("宫门")
        assert not _is_org_type("营门")
        assert not _is_org_type("城门口")

    def test_military_camp_types_still_org(self):
        """真军营类地点保留为 org 候选(曹军水寨/魏寨/蜀营 type=军营)。"""
        assert _is_org_type("军营")
        assert _is_org_type("军队")
        assert _is_org_type("朝廷")


class TestIsOrgConceptCategory:
    """Source 4 概念路径白名单:只有明确组织/势力/门派/政权类才进。"""

    def test_org_categories_match(self):
        """宗教组织(五斗米道/太平道)、军事组织(御林军)等保留。"""
        assert _is_org_concept_category("宗教组织")
        assert _is_org_concept_category("军事组织")
        assert _is_org_concept_category("门派")
        assert _is_org_concept_category("政治势力")
        assert _is_org_concept_category("政权")

    def test_military_concept_categories_excluded(self):
        """军事计谋/战术/制度/编制/器械一律排除(三国演义实测 70 个噪音)。"""
        assert not _is_org_concept_category("军事计谋")
        assert not _is_org_concept_category("军事战术")
        assert not _is_org_concept_category("军事制度")
        assert not _is_org_concept_category("军事编制")
        assert not _is_org_concept_category("军事器械")
        assert not _is_org_concept_category("军事设施")
        assert not _is_org_concept_category("军法")

    def test_near_miss_categories_excluded(self):
        """含单字「宗/教/盟」但非组织的 category 不误判。"""
        assert not _is_org_concept_category("宗庙制度")
        assert not _is_org_concept_category("宗教仪式")
        assert not _is_org_concept_category("盟誓仪式")


class TestFactionsOrgNoiseFiltered:
    """get_factions_data 端到端:城门类地点与军事概念不进 orgs,
    真组织(军营类地点/宗教组织概念)保留。"""

    @pytest.mark.asyncio
    async def test_gate_location_and_military_concept_excluded(self, memory_db):
        from src.services.visualization_service import get_factions_data

        await memory_db.execute(
            "INSERT INTO novels (id, title) VALUES ('nv3', '测试')"
        )
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content) "
            "VALUES (1, 'nv3', 1, '第一章', '')"
        )
        fact = {
            "chapter_id": 1,
            "novel_id": "nv3",
            "characters": [
                {"name": "曹操", "locations_in_chapter": ["曹军水寨", "东门"]},
            ],
            "locations": [
                {"name": "曹军水寨", "type": "军营"},
                {"name": "东门", "type": "城门"},
                {"name": "嘉德门", "type": "宫门"},
            ],
            "org_events": [],
            "new_concepts": [
                {"name": "太平道", "category": "宗教组织",
                 "definition": "张角创立的宗教组织"},
                {"name": "诈死计", "category": "军事计谋",
                 "definition": "佯装身死的计谋"},
                {"name": "军令状", "category": "军事制度",
                 "definition": "立状为凭"},
            ],
            "events": [],
        }
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES ('nv3', 1, ?)",
            (json.dumps(fact, ensure_ascii=False),),
        )
        await memory_db.commit()

        async def _factory():
            return _NonClosingConnection(memory_db)

        with patch("src.services.visualization_service.get_connection", _factory), \
             patch("src.services.alias_resolver.get_connection", _factory):
            data = await get_factions_data("nv3", 1, 1)

        org_names = {o["name"] for o in data["orgs"]}
        # 真组织保留
        assert "曹军水寨" in org_names
        assert "太平道" in org_names
        # 噪音剔除
        assert "东门" not in org_names
        assert "嘉德门" not in org_names
        assert "诈死计" not in org_names
        assert "军令状" not in org_names
        # 曹操到访曹军水寨 → visitors
        visitors = data["visitors"].get("曹军水寨", [])
        assert {v["person"] for v in visitors} == {"曹操"}
        # 东门不是 org,不产生访客记录
        assert "东门" not in data["visitors"]


class TestFactionsVisitorsNotMembers:
    """factions Source 3:出现在 org 类地点的人物进入 visitors,不进 members。"""

    @pytest.mark.asyncio
    async def test_visitors_separated_from_members(self, memory_db):
        from src.services.visualization_service import get_factions_data

        await memory_db.execute(
            "INSERT INTO novels (id, title) VALUES ('nv2', '测试')"
        )
        await memory_db.execute(
            "INSERT INTO chapters (id, novel_id, chapter_num, title, content) "
            "VALUES (1, 'nv2', 1, '第一章', '')"
        )
        fact = {
            "chapter_id": 1,
            "novel_id": "nv2",
            "characters": [
                {"name": "韩立", "locations_in_chapter": ["七玄门"]},
                {"name": "路人甲", "locations_in_chapter": ["七玄门"]},
            ],
            "locations": [
                {"name": "七玄门", "type": "门派"},
            ],
            "org_events": [
                {"org_name": "七玄门", "org_type": "门派", "member": "韩立",
                 "action": "加入", "role": "弟子",
                 "evidence": "韩立拜入七玄门"},
            ],
            "events": [],
        }
        await memory_db.execute(
            "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
            "VALUES ('nv2', 1, ?)",
            (json.dumps(fact, ensure_ascii=False),),
        )
        await memory_db.commit()

        async def _factory():
            return _NonClosingConnection(memory_db)

        with patch("src.services.visualization_service.get_connection", _factory), \
             patch("src.services.alias_resolver.get_connection", _factory):
            data = await get_factions_data("nv2", 1, 1)

        members = data["members"].get("七玄门", [])
        member_names = {m["person"] for m in members}
        # org_event 明确加入的韩立是成员;纯到访的路人甲不是
        assert "韩立" in member_names
        assert "路人甲" not in member_names
        # 路人甲进入独立的 visitors 输出
        visitors = data["visitors"].get("七玄门", [])
        assert {v["person"] for v in visitors} == {"路人甲"}
        # member_count 不计访客
        org = next(o for o in data["orgs"] if o["name"] == "七玄门")
        assert org["member_count"] == 1


# ── D5: OrgEventFact evidence ────────────────────


class TestOrgEventEvidence:
    """OrgEventFact.evidence 可选字段:带/不带都能解析(向后兼容)。"""

    def test_legacy_payload_without_evidence(self):
        oe = OrgEventFact.model_validate(
            {"org_name": "七玄门", "member": "韩立", "action": "加入"}
        )
        assert oe.evidence == ""

    def test_payload_with_evidence(self):
        oe = OrgEventFact.model_validate(
            {
                "org_name": "七玄门", "member": "韩立", "action": "加入",
                "evidence": "韩立拜入七玄门",
            }
        )
        assert oe.evidence == "韩立拜入七玄门"
        # Round-trip
        oe2 = OrgEventFact.model_validate_json(oe.model_dump_json())
        assert oe2 == oe

    def test_chapter_fact_org_events_backward_compat(self):
        """旧 JSON(无 evidence 字段)整章反序列化不变。"""
        fact = ChapterFact.model_validate(
            {
                "chapter_id": 1,
                "novel_id": "t",
                "org_events": [
                    {"org_name": "七玄门", "member": "韩立", "action": "加入"},
                ],
            }
        )
        assert fact.org_events[0].evidence == ""
