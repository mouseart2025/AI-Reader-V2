"""Build a context summary from preceding ChapterFacts for LLM context."""

import json
import logging

from src.db.chapter_fact_store import get_all_chapter_facts
from src.db import entity_dictionary_store, world_structure_store
from src.infra.context_budget import get_budget
from src.models.chapter_fact import ChapterFact
from src.models.world_structure import WorldStructure

logger = logging.getLogger(__name__)

# How many recent chapters to consider for "active" entities
_ACTIVE_WINDOW = 20


class ContextSummaryBuilder:
    """Aggregate preceding ChapterFacts into a concise text summary."""

    async def build(
        self,
        novel_id: str,
        chapter_num: int,
        location_parents: dict[str, str] | None = None,
    ) -> str:
        """Build context summary for the given chapter.

        Args:
            novel_id: The novel ID.
            chapter_num: Current chapter number (1-indexed).
            location_parents: Authoritative parent map from WorldStructure
                (location_name → parent_name). Used to build hierarchy chains.

        Returns context string. For early chapters with no preceding facts,
        still returns entity dictionary and world structure sections if available.
        """
        # Build summary text
        sections: list[str] = []
        budget = get_budget()

        # ── Preceding chapter fact aggregation (skip for chapter 1) ──
        chapter_facts: list[ChapterFact] = []
        if chapter_num > 1:
            all_facts = await get_all_chapter_facts(novel_id)
            if all_facts:
                preceding = [
                    f for f in all_facts
                    if f["fact"]["chapter_id"] < chapter_num
                ]
                for row in preceding:
                    try:
                        chapter_facts.append(
                            ChapterFact.model_validate(row["fact"])
                        )
                    except Exception:
                        continue

        if chapter_facts:
            # Determine active window
            recent_cutoff = chapter_num - _ACTIVE_WINDOW
            recent_facts = [
                f for f in chapter_facts if f.chapter_id >= recent_cutoff
            ]
            if not recent_facts:
                recent_facts = chapter_facts[-5:]

            # Aggregate
            characters = self._aggregate_characters(chapter_facts, recent_facts)
            relationships = self._aggregate_relationships(
                chapter_facts, recent_facts,
            )
            locations = self._aggregate_locations(chapter_facts, recent_facts)
            items = self._aggregate_items(chapter_facts, recent_facts)

            char_limit = budget.char_limit
            rel_limit = budget.rel_limit
            loc_limit = budget.loc_limit
            item_limit = budget.item_limit

            if characters:
                lines = ["### 已知人物"]
                for name, info in list(characters.items())[:char_limit]:
                    parts = [name]
                    if info.get("aliases"):
                        short_aliases = [
                            a for a in info["aliases"] if len(a) <= 5
                        ][:3]
                        if short_aliases:
                            parts.append(
                                f"(别名: {', '.join(short_aliases)})"
                            )
                    if info.get("abilities"):
                        parts.append(
                            f"[{', '.join(info['abilities'][:3])}]"
                        )
                    lines.append("- " + " ".join(parts))
                sections.append("\n".join(lines))

            if relationships:
                lines = ["### 已知关系"]
                for rel in relationships[:rel_limit]:
                    lines.append(f"- {rel['a']} ↔ {rel['b']}: {rel['type']}")
                sections.append("\n".join(lines))

            # Scene focus — recent high-frequency locations with full chains
            focus_section = self._build_scene_focus_section(
                recent_facts, location_parents, locations,
            )
            if focus_section:
                sections.append(focus_section)

            # Hierarchy chains (before location list for context)
            hierarchy_section = self._format_hierarchy_chains(
                locations, location_parents,
            )
            if hierarchy_section:
                sections.append(hierarchy_section)

            if locations:
                lines = [
                    "### 已知地点",
                    '（如果本章出现"小城""那座山""此地"等泛称，'
                    '且上下文明确指代以下某个地名，请直接使用该地名，'
                    '不要将泛称作为独立地点提取）',
                ]
                for name, info in list(locations.items())[:loc_limit]:
                    desc = f"- {name} ({info['type']})"
                    parent = (
                        location_parents.get(name) if location_parents
                        else info.get("parent")
                    ) or info.get("parent")
                    if parent:
                        desc += f" ⊂ {parent}"
                    lines.append(desc)
                sections.append("\n".join(lines))

            if items:
                lines = ["### 已知物品"]
                for name, info in list(items.items())[:item_limit]:
                    holder = info.get("holder", "未知")
                    lines.append(
                        f"- {name} ({info['type']}) — 持有: {holder}"
                    )
                sections.append("\n".join(lines))

        # ── Always inject: world structure + entity dictionary ──
        # These are available from pre-scan and don't depend on preceding
        # chapter facts, so they must be injected even for early chapters.

        # World structure summary
        world_section = await self._build_world_structure_section(novel_id)
        if world_section:
            sections.append(world_section)

        # Entity dictionary injection (pre-scan results)
        dict_section = await self._build_dictionary_section(novel_id)
        if dict_section:
            sections.append(dict_section)

        if not sections:
            return ""

        summary = "\n\n".join(sections)

        max_chars = budget.context_max_chars
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "\n...(已截断)"

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
        """Aggregate ALL known locations with mention counts.

        Unlike characters/items which are filtered to the recent window,
        locations use the full history to enable coreference resolution
        (e.g., "小城" in chapter 50 → "青牛镇" from chapter 3).
        Sorted by mention count descending so the most important locations
        appear first within the token budget.
        """
        locs: dict[str, dict] = {}
        mention_counts: dict[str, int] = {}
        for fact in all_facts:
            for loc in fact.locations:
                mention_counts[loc.name] = mention_counts.get(loc.name, 0) + 1
                if loc.name not in locs:
                    locs[loc.name] = {"type": loc.type, "parent": loc.parent}
                elif loc.parent and not locs[loc.name].get("parent"):
                    locs[loc.name]["parent"] = loc.parent

        # Sort by mention count descending
        sorted_locs = dict(
            sorted(locs.items(), key=lambda x: mention_counts.get(x[0], 0), reverse=True)
        )
        return sorted_locs

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

    def _build_scene_focus_section(
        self,
        recent_facts: list[ChapterFact],
        location_parents: dict[str, str] | None,
        locations: dict[str, dict],
    ) -> str:
        """Build 当前场景焦点 section from the most recent 3 chapters.

        Identifies the most frequently visited locations and shows their
        complete hierarchy chains, giving the LLM context for parent assignment.
        """
        if not recent_facts:
            return ""

        from collections import Counter as _Counter

        # Use the last 3 chapter facts
        last_n = recent_facts[-3:]
        loc_freq: _Counter = _Counter()
        for fact in last_n:
            for char in fact.characters:
                for loc in char.locations_in_chapter:
                    if loc:
                        loc_freq[loc] += 1

        if not loc_freq:
            return ""

        focus_locs = [name for name, _ in loc_freq.most_common(3)]

        lines = [
            "### 当前场景焦点",
            "（以下是最近章节中角色最频繁出现的地点及其层级链。本章如出现新的建筑/房间名称，优先将其 parent 设为下方焦点地点）",
        ]

        for loc in focus_locs:
            chain = self._build_upward_chain(loc, location_parents)
            parts = []
            for name in chain:
                loc_type = locations.get(name, {}).get("type", "")
                parts.append(f"{name} ({loc_type})" if loc_type else name)
            lines.append(" > ".join(parts))

        return "\n".join(lines) if len(lines) > 2 else ""

    @staticmethod
    def _build_upward_chain(
        location: str,
        location_parents: dict[str, str] | None,
    ) -> list[str]:
        """Build hierarchy chain from root down to the given location."""
        if not location_parents:
            return [location]

        chain = [location]
        visited = {location}
        current = location
        while current in location_parents:
            parent = location_parents[current]
            if parent in visited:
                break
            chain.append(parent)
            visited.add(parent)
            current = parent

        chain.reverse()  # Root first
        return chain

    @staticmethod
    def _format_hierarchy_chains(
        locations: dict[str, dict],
        location_parents: dict[str, str] | None,
    ) -> str:
        """Build hierarchy chain text from authoritative location_parents.

        Returns a section like:
            ### 已知地点层级
            陕西 (省) > 铜州 (市) > 双水县 (县) > 石圪节公社 (公社) > 双水村 (村庄)
            双水村 (村庄) > 金家湾 (自然村)
        """
        if not location_parents:
            return ""

        # Build children map: parent → list of children
        children_map: dict[str, list[str]] = {}
        for child, parent in location_parents.items():
            children_map.setdefault(parent, []).append(child)

        # Find roots: nodes that are parents but not children
        all_children = set(location_parents.keys())
        all_parents = set(location_parents.values())
        roots = all_parents - all_children

        # Build chains from each root via DFS
        chains: list[list[str]] = []

        def _build_chain(node: str, current_chain: list[str]) -> None:
            current_chain.append(node)
            kids = children_map.get(node, [])
            if not kids:
                # Leaf — record chain if length >= 2
                if len(current_chain) >= 2:
                    chains.append(list(current_chain))
            else:
                for kid in kids:
                    _build_chain(kid, current_chain)
            current_chain.pop()

        for root in roots:
            _build_chain(root, [])

        if not chains:
            return ""

        # Sort by chain length descending, take top-N
        chains.sort(key=len, reverse=True)
        max_chains = get_budget().max_hierarchy_chains

        lines = [
            "### 已知地点层级",
            "（本章如出现新建筑/房间，请查看上述层级中是否有合适的 parent）",
        ]
        seen_chains: set[str] = set()
        for chain in chains[:max_chains]:
            parts = []
            for name in chain:
                loc_type = locations.get(name, {}).get("type", "")
                if loc_type:
                    parts.append(f"{name} ({loc_type})")
                else:
                    parts.append(name)
            chain_str = " > ".join(parts)
            if chain_str not in seen_chains:
                seen_chains.add(chain_str)
                lines.append(chain_str)

        return "\n".join(lines) if len(lines) > 1 else ""

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
        """Build entity dictionary section from pre-scan results.

        Pre-scan aliases (jieba + LLM classification) are tentative and can
        contain errors (e.g., different characters grouped together).  We limit
        injected aliases to at most 2 short ones and add a disclaimer so the
        extraction LLM treats them as hints rather than ground truth.
        """
        try:
            dictionary = await entity_dictionary_store.get_all(novel_id)
        except Exception:
            return ""
        if not dictionary:
            return ""

        # Separate naming-source entries (highest quality: explicit name
        # introductions like "叫作二愣子") from frequency-sorted entries.
        # Put naming entries FIRST so they're not buried at the bottom
        # of a long list where small local models might ignore them.
        naming_entries = [e for e in dictionary if e.source == "naming"]
        top = dictionary[:100]
        included = {e.name for e in top} | {e.name for e in naming_entries}
        # Fill remaining slots from top-100 (skip those already in naming)
        freq_entries = [e for e in top if e.name not in {n.name for n in naming_entries}]

        lines: list[str] = []

        # ── Naming entries first (with emphasis) ──
        if naming_entries:
            lines.append("### 文中明确命名的实体（必须使用完整名称提取）")
            lines.append(
                '以下名称在原文中通过"叫作/名叫/绰号"等方式明确引入，'
                "提取时务必使用完整名称，不要截断：",
            )
            for entry in naming_entries:
                line = f"- **{entry.name}**（{entry.entity_type}）"
                if entry.aliases:
                    safe = [a for a in entry.aliases if len(a) <= 4][:2]
                    if safe:
                        line += f" 可能别名：{'、'.join(safe)}（需文本确认）"
                lines.append(line)
            lines.append("")

        # ── High-frequency entries ──
        lines.append("### 本书高频实体参考")
        lines.append(
            "以下实体在全书中高频出现，提取时请特别注意不要遗漏"
            "（仅供参考，仍以原文为准）：",
        )
        lines.append(
            "⚠️ 下方「可能别名」仅为预扫描猜测，必须在本章原文中"
            "找到明确依据才能作为 new_aliases 输出。不同的人绝不能互为别名。",
        )
        for entry in freq_entries:
            line = f"- {entry.name}（{entry.entity_type}，出现{entry.frequency}次）"
            if entry.aliases:
                safe = [a for a in entry.aliases if len(a) <= 4][:2]
                if safe:
                    line += f" 可能别名：{'、'.join(safe)}（需文本确认）"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _format_world_structure(ws: WorldStructure) -> str:
        """Format WorldStructure as a concise summary.

        Cloud mode: ≤ 1500 chars; Local mode: ≤ 800 chars.
        Returns empty string if the structure is trivially default.
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
        ws_max = get_budget().world_summary_chars
        if len(result) > ws_max:
            result = result[:ws_max - 3] + "..."
        return result
