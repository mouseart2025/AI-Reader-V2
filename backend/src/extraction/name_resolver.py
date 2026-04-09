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

v0.70.3: All name decisions delegated to name_authority (single source of truth).
"""

from __future__ import annotations

import logging
from collections import Counter

from src.models.chapter_fact import ChapterFact
from src.services.name_authority import (
    alias_safety_level,
    is_blocked_name,
    pick_canonical,
)

logger = logging.getLogger(__name__)


class NameResolver:
    """Resolve character name variants to canonical forms in ChapterFact."""

    def __init__(self):
        self._canonical_map: dict[str, str] = {}  # alias → canonical
        self._freq: Counter = Counter()  # name → total mention count

    def load_from_entity_dictionary(self, entries: list) -> None:
        """Load alias mappings from entity_dictionary (pre-scan phase).

        Args:
            entries: list of EntityDictionaryEntry with .name, .aliases, .entity_type

        Canonical selection delegated to name_authority.pick_canonical(),
        ensuring consistency with AliasResolver's downstream canonical choice.
        """
        # Build frequency map: primary entry name → prescan frequency
        entry_freq: dict[str, int] = {}
        for entry in entries:
            if entry.entity_type == "person" and not is_blocked_name(entry.name):
                entry_freq[entry.name] = entry.frequency

        for entry in entries:
            if entry.entity_type != "person":
                continue
            if is_blocked_name(entry.name):
                continue

            # Collect dict-primary candidates for canonical selection
            candidates = [entry.name]
            freq_map = {entry.name: entry.frequency}
            dict_primaries = {entry.name}
            for alias in (entry.aliases or []):
                if alias in entry_freq and not is_blocked_name(alias):
                    candidates.append(alias)
                    freq_map[alias] = entry_freq[alias]
                    dict_primaries.add(alias)

            # Use shared canonical selection — same logic as AliasResolver
            canonical = pick_canonical(candidates, freq_map, dict_primaries)

            # Map all aliases → canonical
            all_names = {entry.name} | set(entry.aliases or [])
            for name in all_names:
                if name and name != canonical and not is_blocked_name(name):
                    if alias_safety_level(name) >= 1:  # not hard-blocked
                        # Don't overwrite existing mapping to a higher-freq canonical
                        existing = self._canonical_map.get(name)
                        if existing and entry_freq.get(existing, 0) > freq_map.get(canonical, 0):
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
                        and not is_blocked_name(alias)
                        and alias_safety_level(alias) >= 1):
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

        # 5. Clean new_aliases: remove blocked names
        for char in fact.characters:
            if char.new_aliases:
                char.new_aliases = [
                    a for a in char.new_aliases
                    if not is_blocked_name(a) and a != char.name
                ]

        if resolved_count > 0:
            logger.debug("NameResolver: resolved %d name references", resolved_count)

        return fact

    @property
    def mapping_count(self) -> int:
        return len(self._canonical_map)
