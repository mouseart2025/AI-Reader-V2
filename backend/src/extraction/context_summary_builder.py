"""Build a context summary from preceding ChapterFacts for LLM context."""

import json
import logging

from src.db.chapter_fact_store import get_all_chapter_facts
from src.db import entity_dictionary_store, world_structure_store
from src.infra.config import LLM_PROVIDER
from src.models.chapter_fact import ChapterFact
from src.models.world_structure import WorldStructure

logger = logging.getLogger(__name__)

# How many recent chapters to consider for "active" entities
_ACTIVE_WINDOW = 20
# Approximate max characters for the summary
# Ollama (local 8B): ~2000 tokens ≈ 6000 chars (tight context budget)
# Cloud (256K ctx): ~6000 tokens ≈ 18000 chars (more context = better extraction)
_MAX_CHARS = 18000 if LLM_PROVIDER == "openai" else 6000


class ContextSummaryBuilder:
    """Aggregate preceding ChapterFacts into a concise text summary."""

    async def build(self, novel_id: str, chapter_num: int) -> str:
        """Build context summary for the given chapter.

        Returns empty string for chapter 1 (no preceding data).
        """
        if chapter_num <= 1:
            return ""

        all_facts = await get_all_chapter_facts(novel_id)
        if not all_facts:
            return ""

        # Only use facts from chapters before current one
        preceding = [
            f for f in all_facts if f["fact"]["chapter_id"] < chapter_num
        ]
        if not preceding:
            return ""

        # Parse into ChapterFact objects
        chapter_facts: list[ChapterFact] = []
        for row in preceding:
            try:
                chapter_facts.append(ChapterFact.model_validate(row["fact"]))
            except Exception:
                continue

        if not chapter_facts:
            return ""

        # Determine active window
        recent_cutoff = chapter_num - _ACTIVE_WINDOW
        recent_facts = [f for f in chapter_facts if f.chapter_id >= recent_cutoff]
        # If no recent facts, use last 5 available
        if not recent_facts:
            recent_facts = chapter_facts[-5:]

        # Aggregate
        characters = self._aggregate_characters(chapter_facts, recent_facts)
        relationships = self._aggregate_relationships(chapter_facts, recent_facts)
        locations = self._aggregate_locations(chapter_facts, recent_facts)
        items = self._aggregate_items(chapter_facts, recent_facts)

        # Build summary text
        sections: list[str] = []

        _is_cloud = LLM_PROVIDER == "openai"
        char_limit = 60 if _is_cloud else 30
        rel_limit = 40 if _is_cloud else 20
        loc_limit = 40 if _is_cloud else 20
        item_limit = 30 if _is_cloud else 15

        if characters:
            lines = ["### 已知人物"]
            for name, info in list(characters.items())[:char_limit]:
                parts = [name]
                if info.get("aliases"):
                    parts.append(f"(别名: {', '.join(info['aliases'][:3])})")
                if info.get("abilities"):
                    parts.append(f"[{', '.join(info['abilities'][:3])}]")
                lines.append("- " + " ".join(parts))
            sections.append("\n".join(lines))

        if relationships:
            lines = ["### 已知关系"]
            for rel in relationships[:rel_limit]:
                lines.append(f"- {rel['a']} ↔ {rel['b']}: {rel['type']}")
            sections.append("\n".join(lines))

        if locations:
            lines = ["### 已知地点"]
            for name, info in list(locations.items())[:loc_limit]:
                desc = f"- {name} ({info['type']})"
                if info.get("parent"):
                    desc += f" ⊂ {info['parent']}"
                lines.append(desc)
            sections.append("\n".join(lines))

        if items:
            lines = ["### 已知物品"]
            for name, info in list(items.items())[:item_limit]:
                holder = info.get("holder", "未知")
                lines.append(f"- {name} ({info['type']}) — 持有: {holder}")
            sections.append("\n".join(lines))

        # World structure summary
        world_section = await self._build_world_structure_section(novel_id)
        if world_section:
            sections.append(world_section)

        # Entity dictionary injection (pre-scan results)
        dict_section = await self._build_dictionary_section(novel_id)
        if dict_section:
            sections.append(dict_section)

        summary = "\n\n".join(sections)

        # Truncate if too long
        if len(summary) > _MAX_CHARS:
            summary = summary[:_MAX_CHARS] + "\n...(已截断)"

        return summary

    def _aggregate_characters(
        self, all_facts: list[ChapterFact], recent_facts: list[ChapterFact]
    ) -> dict[str, dict]:
        """Aggregate character info, prioritizing recently active characters."""
        # Collect all known characters
        chars: dict[str, dict] = {}
        for fact in all_facts:
            for ch in fact.characters:
                if ch.name not in chars:
                    chars[ch.name] = {"aliases": [], "abilities": []}
                entry = chars[ch.name]
                for alias in ch.new_aliases:
                    if alias not in entry["aliases"]:
                        entry["aliases"].append(alias)
                for ab in ch.abilities_gained:
                    label = f"{ab.dimension}:{ab.name}"
                    if label not in entry["abilities"]:
                        entry["abilities"].append(label)

        # Filter to recently active
        recent_names = set()
        for fact in recent_facts:
            for ch in fact.characters:
                recent_names.add(ch.name)

        return {name: info for name, info in chars.items() if name in recent_names}

    def _aggregate_relationships(
        self, all_facts: list[ChapterFact], recent_facts: list[ChapterFact]
    ) -> list[dict]:
        """Aggregate relationships, keeping latest type per pair."""
        # Track latest relationship per pair
        pair_map: dict[tuple[str, str], str] = {}
        for fact in all_facts:
            for rel in fact.relationships:
                key = tuple(sorted([rel.person_a, rel.person_b]))
                pair_map[key] = rel.relation_type

        # Filter to pairs involving recently active characters
        recent_names = set()
        for fact in recent_facts:
            for ch in fact.characters:
                recent_names.add(ch.name)

        result = []
        for (a, b), rtype in pair_map.items():
            if a in recent_names or b in recent_names:
                result.append({"a": a, "b": b, "type": rtype})
        return result

    def _aggregate_locations(
        self, all_facts: list[ChapterFact], recent_facts: list[ChapterFact]
    ) -> dict[str, dict]:
        """Aggregate location info."""
        locs: dict[str, dict] = {}
        for fact in all_facts:
            for loc in fact.locations:
                if loc.name not in locs:
                    locs[loc.name] = {"type": loc.type, "parent": loc.parent}
                elif loc.parent and not locs[loc.name].get("parent"):
                    locs[loc.name]["parent"] = loc.parent

        # Filter to recently mentioned locations
        recent_loc_names = set()
        for fact in recent_facts:
            for loc in fact.locations:
                recent_loc_names.add(loc.name)
            for ch in fact.characters:
                recent_loc_names.update(ch.locations_in_chapter)

        return {name: info for name, info in locs.items() if name in recent_loc_names}

    def _aggregate_items(
        self, all_facts: list[ChapterFact], recent_facts: list[ChapterFact]
    ) -> dict[str, dict]:
        """Aggregate item info with latest holder."""
        items: dict[str, dict] = {}
        for fact in all_facts:
            for ie in fact.item_events:
                if ie.item_name not in items:
                    items[ie.item_name] = {"type": ie.item_type, "holder": ie.actor}
                # Update holder based on action
                if ie.action in ("获得", "赠予") and ie.recipient:
                    items[ie.item_name]["holder"] = ie.recipient
                elif ie.action == "获得":
                    items[ie.item_name]["holder"] = ie.actor
                elif ie.action in ("丢失", "损毁", "消耗"):
                    items[ie.item_name]["holder"] = "无"

        # Filter to recently mentioned items
        recent_item_names = set()
        for fact in recent_facts:
            for ie in fact.item_events:
                recent_item_names.add(ie.item_name)

        return {name: info for name, info in items.items() if name in recent_item_names}

    async def _build_world_structure_section(self, novel_id: str) -> str:
        """Load WorldStructure and format as context section. Returns empty if trivial."""
        try:
            ws = await world_structure_store.load(novel_id)
        except Exception:
            return ""
        if ws is None:
            return ""
        return self._format_world_structure(ws)

    async def _build_dictionary_section(self, novel_id: str) -> str:
        """Build entity dictionary section from pre-scan results."""
        try:
            dictionary = await entity_dictionary_store.get_all(novel_id)
        except Exception:
            return ""
        if not dictionary:
            return ""

        lines = [
            "### 本书高频实体参考",
            "以下实体在全书中高频出现，提取时请特别注意不要遗漏（仅供参考，仍以原文为准）：",
        ]
        for entry in dictionary[:100]:  # Top-100
            line = f"- {entry.name}（{entry.entity_type}，出现{entry.frequency}次）"
            if entry.aliases:
                line += f" 别名：{'、'.join(entry.aliases)}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _format_world_structure(ws: WorldStructure) -> str:
        """Format WorldStructure as a concise summary (≤ 500 chars).

        Returns empty string if the structure is trivially default (only overworld
        with no regions, no portals, and minimal location mappings).
        """
        has_regions = any(layer.regions for layer in ws.layers)
        has_extra_layers = len(ws.layers) > 1
        has_portals = bool(ws.portals)

        if not has_regions and not has_extra_layers and not has_portals:
            return ""

        lines: list[str] = ["### 已知世界结构"]

        for layer in ws.layers:
            if layer.regions:
                region_parts = []
                for r in layer.regions[:10]:
                    dir_str = f"({r.cardinal_direction})" if r.cardinal_direction else ""
                    region_parts.append(f"{r.name}{dir_str}")
                lines.append(f"- {layer.name}区域: {', '.join(region_parts)}")

        for layer in ws.layers:
            if layer.layer_id == "overworld":
                continue
            # Collect locations assigned to this layer
            locs = [
                name for name, lid in ws.location_layer_map.items()
                if lid == layer.layer_id
            ]
            if locs:
                locs_str = ", ".join(locs[:8])
                lines.append(f"- {layer.name} ({layer.layer_id}): {locs_str}")
            elif layer.layer_id not in ("overworld",):
                lines.append(f"- {layer.name} ({layer.layer_id})")

        if ws.portals:
            portal_parts = []
            for p in ws.portals[:5]:
                portal_parts.append(
                    f"{p.name} ({p.source_layer} ↔ {p.target_layer})"
                )
            lines.append(f"- 传送门: {', '.join(portal_parts)}")

        result = "\n".join(lines)
        # Cap at 500 chars
        if len(result) > 500:
            result = result[:497] + "..."
        return result
