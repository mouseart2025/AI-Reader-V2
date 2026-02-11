"""Lightweight post-validation and cleaning for ChapterFact."""

import logging

from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    ItemEventFact,
    OrgEventFact,
)

logger = logging.getLogger(__name__)

_VALID_ITEM_ACTIONS = {"出现", "获得", "使用", "赠予", "消耗", "丢失", "损毁"}
_VALID_ORG_ACTIONS = {"加入", "离开", "晋升", "阵亡", "叛出", "逐出"}
_VALID_EVENT_TYPES = {"战斗", "成长", "社交", "旅行", "其他"}
_VALID_IMPORTANCE = {"high", "medium", "low"}

_NAME_MIN_LEN = 1
_NAME_MAX_LEN = 20


def _clamp_name(name: str) -> str:
    """Truncate name to max length."""
    name = name.strip()
    if len(name) > _NAME_MAX_LEN:
        return name[:_NAME_MAX_LEN]
    return name


class FactValidator:
    """Validate and clean a ChapterFact instance."""

    def validate(self, fact: ChapterFact) -> ChapterFact:
        """Return a cleaned copy of the ChapterFact."""
        characters = self._validate_characters(fact.characters)
        relationships = self._validate_relationships(fact.relationships, characters)
        locations = self._validate_locations(fact.locations)
        item_events = self._validate_item_events(fact.item_events)
        org_events = self._validate_org_events(fact.org_events)
        events = self._validate_events(fact.events)
        new_concepts = self._validate_concepts(fact.new_concepts)

        return ChapterFact(
            chapter_id=fact.chapter_id,
            novel_id=fact.novel_id,
            characters=characters,
            relationships=relationships,
            locations=locations,
            item_events=item_events,
            org_events=org_events,
            events=events,
            new_concepts=new_concepts,
        )

    def _validate_characters(
        self, chars: list[CharacterFact]
    ) -> list[CharacterFact]:
        """Remove empty names, deduplicate by name, clamp name length."""
        seen: dict[str, CharacterFact] = {}
        for ch in chars:
            name = _clamp_name(ch.name)
            if len(name) < _NAME_MIN_LEN:
                continue
            if name in seen:
                # Merge: combine aliases and locations
                existing = seen[name]
                merged_aliases = list(
                    dict.fromkeys(existing.new_aliases + ch.new_aliases)
                )
                merged_locations = list(
                    dict.fromkeys(
                        existing.locations_in_chapter + ch.locations_in_chapter
                    )
                )
                merged_abilities = existing.abilities_gained + ch.abilities_gained
                seen[name] = CharacterFact(
                    name=name,
                    new_aliases=merged_aliases,
                    appearance=existing.appearance or ch.appearance,
                    abilities_gained=merged_abilities,
                    locations_in_chapter=merged_locations,
                )
            else:
                seen[name] = ch.model_copy(update={"name": name})
        return list(seen.values())

    def _validate_relationships(self, rels, characters):
        """Validate relationships; keep only those referencing known characters."""
        char_names = {ch.name for ch in characters}
        # Also collect aliases
        for ch in characters:
            char_names.update(ch.new_aliases)

        valid = []
        for rel in rels:
            a = _clamp_name(rel.person_a)
            b = _clamp_name(rel.person_b)
            if len(a) < _NAME_MIN_LEN or len(b) < _NAME_MIN_LEN:
                continue
            if a not in char_names or b not in char_names:
                logger.debug(
                    "Dropping relationship %s-%s: person not in characters", a, b
                )
                continue
            valid.append(rel.model_copy(update={"person_a": a, "person_b": b}))
        return valid

    def _validate_locations(self, locs):
        valid = []
        for loc in locs:
            name = _clamp_name(loc.name)
            if len(name) < _NAME_MIN_LEN:
                continue
            valid.append(loc.model_copy(update={"name": name}))
        return valid

    def _validate_item_events(
        self, items: list[ItemEventFact]
    ) -> list[ItemEventFact]:
        valid = []
        for item in items:
            name = _clamp_name(item.item_name)
            if len(name) < _NAME_MIN_LEN:
                continue
            action = item.action
            if action not in _VALID_ITEM_ACTIONS:
                action = "出现"
            valid.append(
                item.model_copy(update={"item_name": name, "action": action})
            )
        return valid

    def _validate_org_events(
        self, orgs: list[OrgEventFact]
    ) -> list[OrgEventFact]:
        valid = []
        for org in orgs:
            name = _clamp_name(org.org_name)
            if len(name) < _NAME_MIN_LEN:
                continue
            action = org.action
            if action not in _VALID_ORG_ACTIONS:
                action = "加入"
            valid.append(
                org.model_copy(update={"org_name": name, "action": action})
            )
        return valid

    def _validate_events(self, events: list[EventFact]) -> list[EventFact]:
        valid = []
        for ev in events:
            if not ev.summary or not ev.summary.strip():
                continue
            etype = ev.type if ev.type in _VALID_EVENT_TYPES else "其他"
            importance = ev.importance if ev.importance in _VALID_IMPORTANCE else "medium"
            valid.append(
                ev.model_copy(update={"type": etype, "importance": importance})
            )
        return valid

    def _validate_concepts(self, concepts):
        valid = []
        for c in concepts:
            name = _clamp_name(c.name)
            if len(name) < _NAME_MIN_LEN:
                continue
            valid.append(c.model_copy(update={"name": name}))
        return valid
