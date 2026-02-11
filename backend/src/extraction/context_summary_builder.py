"""Build a context summary from preceding ChapterFacts for LLM context."""

import json
import logging

from src.db.chapter_fact_store import get_all_chapter_facts
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

# How many recent chapters to consider for "active" entities
_ACTIVE_WINDOW = 20
# Approximate max characters for the summary (rough proxy for ~2000 tokens)
_MAX_CHARS = 6000


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

        if characters:
            lines = ["### 已知人物"]
            for name, info in list(characters.items())[:30]:
                parts = [name]
                if info.get("aliases"):
                    parts.append(f"(别名: {', '.join(info['aliases'][:3])})")
                if info.get("abilities"):
                    parts.append(f"[{', '.join(info['abilities'][:3])}]")
                lines.append("- " + " ".join(parts))
            sections.append("\n".join(lines))

        if relationships:
            lines = ["### 已知关系"]
            for rel in relationships[:20]:
                lines.append(f"- {rel['a']} ↔ {rel['b']}: {rel['type']}")
            sections.append("\n".join(lines))

        if locations:
            lines = ["### 已知地点"]
            for name, info in list(locations.items())[:20]:
                desc = f"- {name} ({info['type']})"
                if info.get("parent"):
                    desc += f" ⊂ {info['parent']}"
                lines.append(desc)
            sections.append("\n".join(lines))

        if items:
            lines = ["### 已知物品"]
            for name, info in list(items.items())[:15]:
                holder = info.get("holder", "未知")
                lines.append(f"- {name} ({info['type']}) — 持有: {holder}")
            sections.append("\n".join(lines))

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
