"""Series Bible export — collect entity profiles + viz data for Markdown export."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.services.entity_aggregator import (
    aggregate_item,
    aggregate_location,
    aggregate_org,
    aggregate_person,
    get_all_entities,
)
from src.services.visualization_service import (
    get_analyzed_range,
    get_graph_data,
    get_timeline_data,
)

logger = logging.getLogger(__name__)

# ── Noise filters ────────────────────────────────

_ITEM_NOISE_NAMES = frozenset({
    "银子", "荷包", "头发", "燕窝", "帕子", "衣服", "鞋子", "茶",
    "酒", "饭", "药", "信", "银两", "轿子", "马", "书", "花",
    "礼物", "手帕", "扇子",
})

_TIMELINE_NOISE_TYPES = frozenset({"角色登场", "物品交接"})

# Available export modules
MODULES = [
    "characters",   # 人物档案
    "relations",    # 关系网络
    "locations",    # 地点百科
    "items",        # 物品道具
    "orgs",         # 组织势力
    "timeline",     # 时间线
]


@dataclass
class SeriesBibleData:
    """Collected data for Series Bible export."""

    novel_title: str = ""
    novel_author: str | None = None
    chapter_range: tuple[int, int] = (0, 0)
    modules: list[str] = field(default_factory=list)

    # Module data
    characters: list[dict] = field(default_factory=list)
    relations: dict = field(default_factory=dict)
    locations: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    orgs: list[dict] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)


async def collect_data(
    novel_id: str,
    modules: list[str] | None = None,
    chapter_start: int | None = None,
    chapter_end: int | None = None,
) -> SeriesBibleData:
    """Collect all requested module data for Series Bible export.

    Args:
        novel_id: Novel ID.
        modules: List of module names to include. None = all modules.
        chapter_start: Start chapter (None = from first analyzed).
        chapter_end: End chapter (None = to last analyzed).
    """
    from src.db import novel_store

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise ValueError(f"小说不存在: {novel_id}")

    # Determine chapter range
    analyzed_start, analyzed_end = await get_analyzed_range(novel_id)
    if analyzed_start == 0:
        raise ValueError("该小说尚未进行分析，无法导出")

    ch_start = chapter_start or analyzed_start
    ch_end = chapter_end or analyzed_end

    selected_modules = modules or MODULES
    # Filter to valid modules
    selected_modules = [m for m in selected_modules if m in MODULES]

    data = SeriesBibleData(
        novel_title=novel["title"],
        novel_author=novel.get("author"),
        chapter_range=(ch_start, ch_end),
        modules=selected_modules,
    )

    # Get entity list for aggregation
    all_entities = await get_all_entities(novel_id)

    # ── Characters ──────────────────────────────
    if "characters" in selected_modules:
        persons = [e for e in all_entities if e.type == "person"]
        # Limit to top 50 by chapter_count for performance
        persons = sorted(persons, key=lambda e: e.chapter_count, reverse=True)[:50]
        for entity in persons:
            try:
                profile = await aggregate_person(novel_id, entity.name)
                data.characters.append(profile.model_dump())
            except Exception as e:
                logger.warning("Failed to aggregate person %s: %s", entity.name, e)

    # ── Relations ───────────────────────────────
    if "relations" in selected_modules:
        try:
            graph = await get_graph_data(novel_id, ch_start, ch_end)
            data.relations = graph
        except Exception as e:
            logger.warning("Failed to get graph data: %s", e)

    # ── Locations ───────────────────────────────
    if "locations" in selected_modules:
        locs = [e for e in all_entities if e.type == "location"]
        locs = sorted(locs, key=lambda e: e.chapter_count, reverse=True)[:50]
        for entity in locs:
            try:
                profile = await aggregate_location(novel_id, entity.name)
                data.locations.append(profile.model_dump())
            except Exception as e:
                logger.warning("Failed to aggregate location %s: %s", entity.name, e)

    # ── Items ───────────────────────────────────
    if "items" in selected_modules:
        items = [e for e in all_entities if e.type == "item"]
        items = sorted(items, key=lambda e: e.chapter_count, reverse=True)
        items = [e for e in items if e.name not in _ITEM_NOISE_NAMES and len(e.name) >= 2][:30]
        for entity in items:
            try:
                profile = await aggregate_item(novel_id, entity.name)
                data.items.append(profile.model_dump())
            except Exception as e:
                logger.warning("Failed to aggregate item %s: %s", entity.name, e)

    # ── Orgs ────────────────────────────────────
    if "orgs" in selected_modules:
        orgs = [e for e in all_entities if e.type == "org"]
        orgs = sorted(orgs, key=lambda e: e.chapter_count, reverse=True)[:20]
        for entity in orgs:
            try:
                profile = await aggregate_org(novel_id, entity.name)
                data.orgs.append(profile.model_dump())
            except Exception as e:
                logger.warning("Failed to aggregate org %s: %s", entity.name, e)

    # ── Timeline ────────────────────────────────
    if "timeline" in selected_modules:
        try:
            tl = await get_timeline_data(novel_id, ch_start, ch_end)
            raw_events = tl.get("events", [])
            data.timeline = [e for e in raw_events if e.get("type") not in _TIMELINE_NOISE_TYPES]
        except Exception as e:
            logger.warning("Failed to get timeline data: %s", e)

    return data
