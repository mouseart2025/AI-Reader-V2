"""Regression fixture for synthetic Vietnamese source-novel analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.extraction.fact_validator import FactValidator
from src.models.chapter_fact import ChapterFact
from src.services.encyclopedia_service import (
    get_category_stats,
    get_encyclopedia_entries,
)
from src.services.visualization_service import (
    get_factions_data,
    get_graph_data,
    get_map_data,
    get_timeline_data,
)
from src.utils.chapter_splitter import split_chapters_ex

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vietnamese_synthetic_analysis_fixture.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def vi_fixture() -> dict:
    return _load_fixture()


@pytest.fixture
def patch_analysis_reads(memory_db, monkeypatch):
    """Patch module-level get_connection references to use the shared test DB."""

    class _NonClosingConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            return None

    async def _factory():
        return _NonClosingConnection(memory_db)

    import src.db.chapter_fact_store as chapter_fact_store
    import src.db.world_structure_store as world_structure_store
    import src.services.alias_resolver as alias_resolver
    import src.services.visualization_service as visualization_service

    async def _fake_geo_auto_resolve(*_args, **_kwargs):
        return None, "fantasy", None, {}

    async def _fake_compute_or_load_layout(
        _novel_id,
        _chapter_hash,
        locations,
        _spatial_constraints,
        _first_chapter_map,
        **_kwargs,
    ):
        layout = [
            {
                "name": loc["name"],
                "x": 100 + idx * 40,
                "y": 100 + idx * 30,
                "level": loc.get("level", 0),
            }
            for idx, loc in enumerate(locations)
        ]
        return layout, "hierarchy", None, {"constrained_location_names": []}

    monkeypatch.setattr(chapter_fact_store, "get_connection", _factory)
    monkeypatch.setattr(world_structure_store, "get_connection", _factory)
    monkeypatch.setattr(alias_resolver, "get_connection", _factory)
    monkeypatch.setattr(visualization_service, "get_connection", _factory)
    monkeypatch.setattr(visualization_service, "geo_auto_resolve", _fake_geo_auto_resolve)
    monkeypatch.setattr(visualization_service, "_compute_or_load_layout", _fake_compute_or_load_layout)
    monkeypatch.setattr(visualization_service, "_enhance_constraints", lambda constraints, *_args, **_kwargs: constraints)
    monkeypatch.setattr(visualization_service, "generate_landmasses", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(visualization_service, "generate_rivers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(visualization_service, "generate_roads", lambda *_args, **_kwargs: [])
    visualization_service._map_cache.clear()
    yield memory_db


async def _seed_fixture(db, fixture: dict) -> str:
    novel_id = fixture["_meta"]["fixture_id"]
    chapters = fixture["chapters"]

    await db.execute(
        """
        INSERT INTO novels
            (id, title, author, file_hash, total_chapters, total_words, source_language, prescan_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            novel_id,
            fixture["_meta"]["title"],
            fixture["_meta"]["author"],
            novel_id,
            len(chapters),
            sum(len(ch["content"]) for ch in chapters),
            fixture["_meta"]["source_language"],
            "completed",
        ),
    )

    for chapter in chapters:
        await db.execute(
            """
            INSERT INTO chapters
                (novel_id, chapter_num, title, content, word_count, analysis_status, is_excluded)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                novel_id,
                chapter["chapter_num"],
                chapter["title"],
                chapter["content"],
                len(chapter["content"]),
                "completed",
            ),
        )

    chapter_rows = await (
        await db.execute(
            "SELECT id, chapter_num FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
            (novel_id,),
        )
    ).fetchall()
    chapter_pk_by_num = {row["chapter_num"]: row["id"] for row in chapter_rows}

    for row in fixture["facts"]:
        await db.execute(
            """
            INSERT INTO chapter_facts
                (novel_id, chapter_id, fact_json, llm_model, extraction_ms, input_tokens, output_tokens,
                 cost_usd, cost_cny, is_truncated, segment_count)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 1)
            """,
            (
                novel_id,
                chapter_pk_by_num[row["chapter_num"]],
                json.dumps(row["fact"], ensure_ascii=False),
                "fixture",
                0,
            ),
        )

    await db.execute(
        """
        INSERT INTO world_structures (novel_id, structure_json, updated_at)
        VALUES (?, ?, datetime('now'))
        """,
        (
            novel_id,
            json.dumps(fixture["world_structure"], ensure_ascii=False),
        ),
    )

    await db.commit()
    return novel_id


def test_vietnamese_fixture_split_regression(vi_fixture: dict):
    result = split_chapters_ex(
        vi_fixture["raw_text"],
        source_language=vi_fixture["_meta"]["source_language"],
    )

    expected = vi_fixture["expected"]["split"]
    assert result.matched_mode == expected["matched_mode"]
    assert len(result.chapters) == expected["chapter_count"]
    assert [chapter.title for chapter in result.chapters] == expected["titles"]


def test_vietnamese_fixture_fact_validator_regression(vi_fixture: dict):
    validator = FactValidator(genre="historical")
    expected = vi_fixture["expected"]
    expected_persons = set(expected["encyclopedia"]["persons"])
    expected_locations = set(expected["encyclopedia"]["locations"])

    actual_persons: set[str] = set()
    actual_locations: set[str] = set()

    for row in vi_fixture["facts"]:
        fact = ChapterFact.model_validate(row["fact"])
        validated = validator.validate(fact)
        actual_persons.update(character.name for character in validated.characters)
        actual_locations.update(location.name for location in validated.locations)

    assert expected_persons <= actual_persons
    assert expected_locations <= actual_locations


@pytest.mark.asyncio
async def test_vietnamese_fixture_aggregate_regression(
    vi_fixture: dict,
    patch_analysis_reads,
):
    db = patch_analysis_reads
    novel_id = await _seed_fixture(db, vi_fixture)
    expected = vi_fixture["expected"]

    graph = await get_graph_data(novel_id, 1, len(vi_fixture["chapters"]))
    node_by_name = {node["name"]: node for node in graph["nodes"]}
    for name, node_expectation in expected["graph"]["nodes"].items():
        assert name in node_by_name
        assert node_by_name[name]["chapter_count"] == node_expectation["chapter_count"]
        assert node_by_name[name]["org"] == node_expectation["org"]

    edge_by_pair = {
        frozenset((edge["source"], edge["target"])): edge
        for edge in graph["edges"]
    }
    assert len(edge_by_pair) == len(expected["graph"]["edges"])
    for edge_expectation in expected["graph"]["edges"]:
        key = frozenset((edge_expectation["source"], edge_expectation["target"]))
        assert key in edge_by_pair
        actual = edge_by_pair[key]
        assert actual["relation_type"] == edge_expectation["relation_type"]
        assert actual["weight"] == edge_expectation["weight"]
        assert actual["category"] == edge_expectation["category"]

    timeline = await get_timeline_data(novel_id, 1, len(vi_fixture["chapters"]))
    summaries = {event["summary"] for event in timeline["events"]}
    for summary in expected["timeline"]["must_include_summaries"]:
        assert summary in summaries
    for needle in expected["timeline"]["relation_event_substrings"]:
        assert any(needle in summary for summary in summaries)

    category_stats = await get_category_stats(novel_id)
    assert category_stats == expected["category_stats"]

    person_entries = await get_encyclopedia_entries(novel_id, category="person", sort_by="name")
    location_entries = await get_encyclopedia_entries(novel_id, category="location", sort_by="hierarchy")
    item_entries = await get_encyclopedia_entries(novel_id, category="item", sort_by="name")
    org_entries = await get_encyclopedia_entries(novel_id, category="org", sort_by="name")

    assert [entry["name"] for entry in person_entries] == expected["encyclopedia"]["persons"]
    assert sorted(entry["name"] for entry in location_entries) == sorted(expected["encyclopedia"]["locations"])
    assert [entry["name"] for entry in item_entries] == expected["encyclopedia"]["items"]
    assert [entry["name"] for entry in org_entries] == expected["encyclopedia"]["orgs"]

    location_by_name = {entry["name"]: entry for entry in location_entries}
    assert location_by_name["bến Chương Dương"]["parent"] == "sông Hồng"
    assert location_by_name["bến Chương Dương"]["tier"] == "site"
    assert location_by_name["chùa Phổ Minh"]["icon"] == "temple"

    factions = await get_factions_data(novel_id, 1, len(vi_fixture["chapters"]))
    actual_org_names = sorted(org["name"] for org in factions["orgs"])
    assert actual_org_names == expected["factions"]["orgs"]

    actual_members = {
        org_name: sorted(member["person"] for member in members)
        for org_name, members in factions["members"].items()
    }
    assert actual_members["nghĩa quân Lam Sơn"] == expected["factions"]["members"]["nghĩa quân Lam Sơn"]
    assert actual_members["quân Đại Việt"] == expected["factions"]["members"]["quân Đại Việt"]

    actual_relations = {
        (relation["source"], relation["target"], relation["type"])
        for relation in factions["relations"]
    }
    expected_relations = {
        (relation["source"], relation["target"], relation["type"])
        for relation in expected["factions"]["relations"]
    }
    assert actual_relations == expected_relations


@pytest.mark.asyncio
async def test_vietnamese_fixture_map_regression(
    vi_fixture: dict,
    patch_analysis_reads,
):
    db = patch_analysis_reads
    novel_id = await _seed_fixture(db, vi_fixture)
    expected = vi_fixture["expected"]["map"]

    map_data = await get_map_data(novel_id, 1, len(vi_fixture["chapters"]))

    location_by_name = {loc["name"]: loc for loc in map_data["locations"]}
    assert sorted(location_by_name) == sorted(expected["locations"])
    for name, expectation in expected["location_details"].items():
        assert name in location_by_name
        actual = location_by_name[name]
        assert actual["parent"] == expectation["parent"]
        assert actual["tier"] == expectation["tier"]
        assert actual["icon"] == expectation["icon"]
        assert actual["mention_count"] == expectation["mention_count"]

    constraints = {
        (constraint["source"], constraint["target"], constraint["relation_type"])
        for constraint in map_data["spatial_constraints"]
    }
    assert constraints == {
        (constraint["source"], constraint["target"], constraint["relation_type"])
        for constraint in expected["spatial_constraints"]
    }

    trajectories = map_data["trajectories"]
    for person, locations in expected["trajectory_locations"].items():
        assert person in trajectories
        assert [entry["location"] for entry in trajectories[person]] == locations

    assert map_data["layout_mode"] == "hierarchy"
    layout_names = {entry["name"] for entry in map_data["layout"]}
    assert layout_names == set(expected["locations"])

    context_text = json.dumps(map_data["geography_context"], ensure_ascii=False)
    for needle in expected["context_substrings"]:
        assert needle in context_text
