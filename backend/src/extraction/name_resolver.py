"""NameResolver — resolves character name variants to canonical forms.

Runs after FactValidator, before DB save. Ensures that within each ChapterFact,
all character references use canonical names (e.g., "行者" → "孙悟空").

This is the upstream fix for the alias fragmentation problem: by unifying names
at extraction time, downstream relation edges and alias groups are automatically
deduplicated.

Sources for canonical mapping (in priority order):
1. entity_dictionary aliases (from pre-scan phase)
2. Accumulated new_aliases from prior chapters
3. Current chapter's own new_aliases

Safety: only merges explicit alias mappings. No fuzzy matching.
"""

from __future__ import annotations

import logging
from collections import Counter

from src.models.chapter_fact import ChapterFact
from src.services.alias_resolver import _alias_safety_level

logger = logging.getLogger(__name__)

# Generic terms that should NEVER become canonical names or be used as mapping keys
_GENERIC_BLOCK = frozenset({
    "哥哥", "弟弟", "姐姐", "妹妹", "外公", "师父", "师傅", "徒弟",
    "师兄", "师弟", "师姐", "师妹", "大哥", "大王", "大爷", "二爷",
    "老爷", "贤弟", "兄弟", "长老", "贫僧", "法师", "和尚", "陛下",
    "万岁", "圣上", "菩萨", "老师", "那厮", "泼猴", "呆子", "那长老",
    "老和尚", "小妖", "妖精", "妖怪", "那怪", "客官", "官人",
    "前辈", "晚辈", "道友", "仙子", "主人", "夫君",
})


class NameResolver:
    """Resolve character name variants to canonical forms in ChapterFact."""

    def __init__(self):
        self._canonical_map: dict[str, str] = {}  # alias → canonical
        self._freq: Counter = Counter()  # name → total mention count

    def load_from_entity_dictionary(self, entries: list) -> None:
        """Load alias mappings from entity_dictionary (pre-scan phase).

        Args:
            entries: list of EntityDictionaryEntry with .name, .aliases, .entity_type

        Canonical selection: when an entry's alias is also a primary dict entry
        with higher prescan frequency, use the higher-frequency name as canonical.
        This prevents rare formal names (陈玄奘 freq=14) from overriding common
        names (唐僧 freq=829).
        """
        # Build frequency map: primary entry name → prescan frequency
        entry_freq: dict[str, int] = {}
        for entry in entries:
            if entry.entity_type == "person" and entry.name not in _GENERIC_BLOCK:
                entry_freq[entry.name] = entry.frequency

        for entry in entries:
            if entry.entity_type != "person":
                continue
            if entry.name in _GENERIC_BLOCK:
                continue

            # Pick canonical: among entry.name + aliases that are also primary
            # entries, prefer the highest prescan frequency.
            canonical = entry.name
            best_freq = entry.frequency
            for alias in (entry.aliases or []):
                alias_freq = entry_freq.get(alias, 0)
                if alias_freq > best_freq and alias not in _GENERIC_BLOCK:
                    canonical = alias
                    best_freq = alias_freq

            # Map all aliases → canonical
            all_names = {entry.name} | set(entry.aliases or [])
            for name in all_names:
                if name and name != canonical and name not in _GENERIC_BLOCK:
                    if _alias_safety_level(name) >= 1:  # not hard-blocked
                        # Don't overwrite existing mapping to a higher-freq canonical
                        existing = self._canonical_map.get(name)
                        if existing and entry_freq.get(existing, 0) > best_freq:
                            continue
                        self._canonical_map[name] = canonical

        logger.info("NameResolver loaded %d mappings from entity_dictionary",
                     len(self._canonical_map))

    def accumulate_from_chapter(self, fact: ChapterFact) -> None:
        """Accumulate alias mappings from a chapter's new_aliases fields.

        Called AFTER resolve() so canonical names are already applied.
        Builds up the mapping for subsequent chapters.
        """
        for char in fact.characters:
            name = char.name
            self._freq[name] += 1
            for alias in (char.new_aliases or []):
                if (alias and alias != name
                        and alias not in _GENERIC_BLOCK
                        and _alias_safety_level(alias) >= 1):
                    existing = self._canonical_map.get(alias)
                    if existing and existing != name:
                        # Conflict: alias points to two different canonicals.
                        # Keep the one with higher frequency.
                        if self._freq.get(name, 0) >= self._freq.get(existing, 0):
                            self._canonical_map[alias] = name
                        # else: keep existing
                    else:
                        self._canonical_map[alias] = name

    def resolve(self, fact: ChapterFact) -> ChapterFact:
        """Apply canonical name mappings to all fields in a ChapterFact.

        Modifies fact in-place and returns it.
        """
        if not self._canonical_map:
            return fact

        mapped = self._canonical_map
        resolved_count = 0

        # 1. Resolve character names
        for char in fact.characters:
            canonical = mapped.get(char.name)
            if canonical:
                char.name = canonical
                resolved_count += 1

        # 2. Resolve relationship person_a / person_b
        for rel in fact.relationships:
            ca = mapped.get(rel.person_a)
            if ca:
                rel.person_a = ca
                resolved_count += 1
            cb = mapped.get(rel.person_b)
            if cb:
                rel.person_b = cb
                resolved_count += 1

        # 3. Resolve event participants
        for evt in fact.events:
            evt.participants = [mapped.get(p, p) for p in evt.participants]

        # 4. Resolve item_events / org_events character references
        for ie in fact.item_events:
            if hasattr(ie, 'character') and ie.character:
                c = mapped.get(ie.character)
                if c:
                    ie.character = c
        for oe in fact.org_events:
            if hasattr(oe, 'character') and oe.character:
                c = mapped.get(oe.character)
                if c:
                    oe.character = c

        # 5. Resolve new_aliases: ensure aliases point to canonical
        for char in fact.characters:
            if char.new_aliases:
                # Remove aliases that are themselves canonical names
                char.new_aliases = [
                    a for a in char.new_aliases
                    if a not in _GENERIC_BLOCK and a != char.name
                ]

        if resolved_count > 0:
            logger.debug("NameResolver: resolved %d name references", resolved_count)

        return fact

    @property
    def mapping_count(self) -> int:
        return len(self._canonical_map)
