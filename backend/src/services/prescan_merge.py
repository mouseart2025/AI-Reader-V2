"""Pre-scan candidate merges (issue #18).

Why: the entity pre-scan (jieba frequency + LLM classification) splits the same
person into several candidates when a translation renders the name differently
("罗切斯特" / "罗彻斯特"), or when a nickname shows up as a separate headword.
Those candidates then seed the extraction prompt as *different* people, so the
whole analysis treats them as different characters.

Design: users merge candidates **before** analysis starts. Merges live in
`entity_overrides` (`override_type="prescan_merge"`, `override_key=<absorbed
name>`, `override_json={"target": <canonical>}`), exactly like the existing
alias/entity overrides: the raw `entity_dictionary` rows stay untouched, the
merged view is computed on read, and deleting the override restores the
automatic result.

Apply order matters: a merge is applied against the *current* dictionary, so
stale overrides (referring to names a re-scan no longer produces) are ignored
rather than resurrecting phantom entries.
"""

from __future__ import annotations

from collections import Counter

from src.db import entity_dictionary_store, entity_override_store
from src.models.entity_dict import EntityDictEntry

OVERRIDE_TYPE = "prescan_merge"
_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def merge_entries(
    entries: list[EntityDictEntry],
    merges: list[dict[str, str]],
) -> tuple[list[EntityDictEntry], dict[str, list[str]]]:
    """Return (merged entries, {canonical: [absorbed names]}).

    Pure function: no DB access, so the merge algebra is testable on its own.
    Unknown names in `merges` are skipped (the dictionary can be re-scanned).
    Chains (A→B, B→C) collapse to one canonical.
    """
    if not entries:
        return [], {}
    by_name = {e.name: e for e in entries}
    if not merges:
        return list(entries), {}

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Only merges whose both ends exist right now participate.
    valid = [
        (m["source"], m["target"])
        for m in merges
        if m.get("source") in by_name
        and m.get("target") in by_name
        and m["source"] != m["target"]
    ]
    if not valid:
        return list(entries), {}
    for source, target in valid:
        union(source, target)

    groups: dict[str, list[str]] = {}
    for name in parent:
        groups.setdefault(find(name), []).append(name)

    absorbed_by_canonical: dict[str, list[str]] = {}
    merged: list[EntityDictEntry] = []

    for names in groups.values():
        if len(names) < 2:
            continue
        sources = {s for s, t in valid if s in names and t in names}
        # Canonical: prefer a member that is never an absorbed source; break
        # ties by higher frequency, then by shorter name (canonical names in
        # this domain tend to be the short, common form).
        candidates = [n for n in names if n not in sources] or names
        canonical = max(
            candidates,
            key=lambda n: (by_name[n].frequency, -len(n), n),
        )
        members = [by_name[n] for n in names]
        base = by_name[canonical]

        aliases: list[str] = list(base.aliases)
        for name in names:
            if name == canonical:
                continue
            for alias in [name, *by_name[name].aliases]:
                if alias != canonical and alias not in aliases:
                    aliases.append(alias)

        entity_type = base.entity_type
        if entity_type in ("unknown", "", None):
            typed = Counter(m.entity_type for m in members if m.entity_type not in ("unknown", "", None))
            if typed:
                entity_type = typed.most_common(1)[0][0]

        source = base.source
        if any(m.source == "naming" for m in members):
            # Keep the naming-source priority: `_build_dictionary_section`
            # surfaces naming entries first and trusts them more.
            source = "naming"

        merged.append(
            EntityDictEntry(
                name=canonical,
                entity_type=entity_type,
                frequency=sum(m.frequency for m in members),
                confidence=max(members, key=lambda m: _CONF_RANK.get(m.confidence, 1)).confidence,
                aliases=aliases,
                source=source,
                sample_context=base.sample_context,
            )
        )
        absorbed_by_canonical[canonical] = sorted(n for n in names if n != canonical)

    # Entries outside every merge group (find() is identity for untouched
    # names, and groups only contains names that were union-ed).
    untouched = [e for e in entries if len(groups.get(find(e.name), [])) < 2]
    result = [*untouched, *merged]
    result.sort(key=lambda e: (-e.frequency, e.name))
    return result, absorbed_by_canonical


async def list_merges(novel_id: str) -> list[dict[str, str]]:
    """Load the user's pre-scan merges, oldest first (deterministic apply order)."""
    overrides = await entity_override_store.load_overrides(novel_id)
    return [
        {"source": o["override_key"], "target": (o.get("override_json") or {}).get("target", "")}
        for o in overrides
        if o["override_type"] == OVERRIDE_TYPE and (o.get("override_json") or {}).get("target")
    ]


async def save_merge(novel_id: str, source: str, target: str) -> None:
    """Merge `source` into `target` (idempotent; re-merging updates the target)."""
    await entity_override_store.save_override(
        novel_id, OVERRIDE_TYPE, source, {"target": target}
    )


async def delete_merge(novel_id: str, source: str) -> bool:
    """Undo one merge. Returns False if no such override exists."""
    overrides = await entity_override_store.load_overrides(novel_id)
    for o in overrides:
        if o["override_type"] == OVERRIDE_TYPE and o["override_key"] == source:
            return await entity_override_store.delete_override(novel_id, o["id"])
    return False


async def get_merged_dictionary(
    novel_id: str,
) -> tuple[list[EntityDictEntry], dict[str, list[str]], list[dict[str, str]]]:
    """The dictionary every consumer should read.

    Returns (merged entries, {canonical: absorbed names}, merges in apply order).
    Raw rows are never rewritten — deleting an override restores the auto view.
    """
    entries = await entity_dictionary_store.get_all(novel_id)
    merges = await list_merges(novel_id)
    merged, absorbed = merge_entries(entries, merges)
    return merged, absorbed, merges
