"""层级方向语义:contains 的“位于”方向、船只主场景、名字包含的尺度守卫。

回归《海底两万里》地图/层级里的四条错误:
  加拿大 → 诺第留斯号       (船只被当成国家父节点)
  美国 → 内布拉斯加州       (contains “州位于国” 方向没翻)
  南极洲 → 南极             (名字包含把大陆挂到极点下)
  中国海 → 中国             (海域被当成国家的子地点)
"""

import json
from collections import Counter

import pytest

from src.services.geo_skills.edmonds_resolver import EdmondsResolver
from src.services.geo_skills.snapshot import HierarchySnapshot
from src.services.geo_skills.vote_builder import VoteBuilder
from src.utils.location_names import (
    is_mobile_container,
    is_vessel_interior,
    name_containment_direction,
)
from src.utils.spatial_semantics import (
    SOURCE_PARENT,
    TARGET_PARENT,
    contains_direction,
)

# ── Pure semantics ────────────────────────────────────────────────


class TestContainsDirection:
    def test_located_in_wording_flips_edge(self):
        assert contains_direction(
            "内布拉斯加州", "美国", "内布拉斯加州位于美国中部",
        ) == TARGET_PARENT

    def test_contains_wording_keeps_edge(self):
        assert contains_direction(
            "美国", "内布拉斯加州", "美国包括内布拉斯加州",
        ) == SOURCE_PARENT

    def test_subject_is_target(self):
        # Edge is 双水村→阳坡; the value's subject is 阳坡, so 双水村 is parent.
        assert contains_direction(
            "双水村", "阳坡", "阳坡在双水村山上",
        ) == SOURCE_PARENT

    def test_earliest_cue_wins_in_multi_clause(self):
        assert contains_direction(
            "花果山", "水帘洞", "花果山包括水帘洞，水帘洞位于花果山中",
        ) == SOURCE_PARENT

    def test_target_name_inside_source_name(self):
        # "大西洋" is a substring of "大西洋洲"; the first occurrence is inside
        # the source name and used to hide the real cue.
        assert contains_direction(
            "大西洋洲", "大西洋", "大西洋洲位于大西洋海底",
        ) == TARGET_PARENT

    def test_macro_continent_does_not_sit_inside_pole(self):
        assert contains_direction(
            "南极洲", "南极", "南极洲位于南极",
        ) is None

    def test_empty_value_falls_back(self):
        assert contains_direction("内布拉斯加州", "美国", "") is None


class TestNameContainmentGuard:
    def test_feature_extension_is_child(self):
        assert name_containment_direction("花果山", "花果山辕门") == "child"

    def test_macro_extension_is_reversed(self):
        assert name_containment_direction("南极", "南极洲") == "reverse"
        assert name_containment_direction("北美", "北美洲") == "reverse"

    def test_same_scale_variant_is_skipped(self):
        assert name_containment_direction("长安", "长安城") == "skip"
        assert name_containment_direction("中国", "中国海") == "skip"

    def test_mobile_container_detection(self):
        assert is_mobile_container("诺第留斯号", "区域")
        assert is_mobile_container("林肯号", "船只")
        assert not is_mobile_container("第四号路", "街道")
        assert not is_mobile_container("巴黎", "城市")
        assert is_vessel_interior("客厅")
        assert is_vessel_interior("中央铁梯")
        assert not is_vessel_interior("加拿大")


# ── VoteBuilder: 州/国方向 + 船只主场景 ──────────────────────────


class _NonClosing:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_vote_builder_flips_state_country_and_ignores_vessel_setting(
    memory_db,
):
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES ('nv-hier', '测试')"
    )
    await memory_db.execute(
        "INSERT INTO chapters (id, novel_id, chapter_num, title, content) "
        "VALUES (1, 'nv-hier', 1, '第一章', '')"
    )
    fact = {
        "chapter_id": 1,
        "novel_id": "nv-hier",
        "locations": [
            {"name": "内布拉斯加州", "type": "州", "role": "setting"},
            {"name": "美国", "type": "国家"},
            {"name": "诺第留斯号", "type": "船只"},
            {"name": "加拿大", "type": "区域"},
            {"name": "客厅", "type": "船舱"},
        ],
        "spatial_relationships": [
            {"source": "内布拉斯加州", "target": "美国",
             "relation_type": "contains", "confidence": "high",
             "value": "内布拉斯加州位于美国中部"},
        ],
        "events": [],
    }
    await memory_db.execute(
        "INSERT INTO chapter_facts (novel_id, chapter_id, fact_json) "
        "VALUES ('nv-hier', 1, ?)",
        (json.dumps(fact, ensure_ascii=False),),
    )
    await memory_db.commit()

    snapshot = HierarchySnapshot(
        location_parents={},
        location_tiers={
            "内布拉斯加州": "kingdom", "美国": "kingdom",
            "诺第留斯号": "region", "加拿大": "site", "客厅": "building",
        },
        parent_votes={},
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )

    async def _factory():
        return _NonClosing(memory_db)

    from unittest.mock import patch

    with patch("src.db.sqlite_db.get_connection", _factory):
        result = await VoteBuilder("nv-hier").execute(snapshot)

    votes = result.new_votes
    assert votes.get("内布拉斯加州", Counter()).get("美国", 0) > 0
    assert votes.get("美国", Counter()).get("内布拉斯加州", 0) == 0
    # 船只主场景不再给当章国家/舱室当爹
    assert votes.get("加拿大", Counter()).get("诺第留斯号", 0) == 0
    assert votes.get("客厅", Counter()).get("诺第留斯号", 0) == 0


# ── EdmondsResolver: 基础边纠偏 + 显式 None 删边 ──────────────────


def _snapshot_with_problem_edges() -> HierarchySnapshot:
    return HierarchySnapshot(
        location_parents={
            "主世界": "太平洋",  # keep the same odd root shape as the novel
            "太平洋": "主世界",
            "美国": "内布拉斯加州",
            "内布拉斯加州": "主世界",
            "南极洲": "南极",
            "南极": "主世界",
            "诺第留斯号": "主世界",
            "加拿大": "诺第留斯号",
            "客厅": "诺第留斯号",
            "中国海": "中国",
            "中国": "主世界",
            "大西洋": "主世界",
            "大西洋洲": "主世界",
        },
        location_tiers={
            "主世界": "world", "太平洋": "continent", "美国": "kingdom",
            "内布拉斯加州": "kingdom", "南极洲": "continent",
            "南极": "site", "诺第留斯号": "region", "加拿大": "site",
            "客厅": "building", "中国海": "region", "中国": "kingdom",
            "大西洋": "region", "大西洋洲": "region",
        },
        parent_votes={
            "内布拉斯加州": Counter({"美国": 3.0}),
            "大西洋洲": Counter({"大西洋": 2.7}),
            "中国海": Counter({"太平洋": 5.0}),
            "诺第留斯号": Counter({"主世界": 1.0}),
        },
        location_frequencies=Counter(),
        chapter_settings={},
        location_chapters={},
    )


@pytest.mark.asyncio
async def test_edmonds_fixes_inversion_vessel_and_water_parent():
    snap = _snapshot_with_problem_edges()
    result = await EdmondsResolver().execute(snap)
    overrides = result.parent_overrides

    # 州/国方向翻转: 美国不再是内布拉斯加的子节点
    assert overrides.get("美国") in (None, "主世界", "太平洋")
    assert overrides.get("内布拉斯加州") == "美国"

    # 船不能拥有国家; 真实舱室仍留在船下
    assert overrides.get("加拿大") in (None, "主世界", "太平洋")
    assert overrides.get("客厅") == "诺第留斯号"

    # 名字包含的宏观尺度: 南极 → 南极洲, 不是反过来
    assert overrides.get("南极") == "南极洲"
    assert overrides.get("南极洲") in (None, "主世界", "太平洋")

    # 海域不挂在国家下; 海(大西洋洲)仍在大西洋里
    assert overrides.get("中国海") in (None, "主世界", "太平洋")
    assert overrides.get("大西洋洲") == "大西洋"
