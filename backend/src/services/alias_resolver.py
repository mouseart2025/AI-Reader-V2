"""Build alias → canonical name mapping for entity deduplication.

Uses entity_dictionary (from pre-scan) as primary source, falling back to
ChapterFact.characters[].new_aliases when no dictionary is available.

IMPORTANT: Generic/contextual terms (大哥, 妈妈, 老人, etc.) must NEVER be used
as Union-Find keys because they can refer to different entities in different
chapters, creating false bridges that merge unrelated character groups.
See _is_unsafe_alias() for the filtering logic.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from src.db.sqlite_db import get_connection
from src.extraction.fact_validator import _normalize_char_variants
from src.services import name_authority

logger = logging.getLogger(__name__)

# ── Module-level cache ────────────────────────────

_alias_cache: dict[str, dict[str, str]] = {}  # novel_id -> alias_map
# novel_id -> set of aliases whose user override conflicts with the current
# automatic resolution (FR7: new chapters changed what the auto map would do).
_alias_conflicts: dict[str, set[str]] = {}
# novel_id -> {canonical: {aliases attributed to it by a user override}} — used
# to mark profiles/graph nodes as "edited" (FR6).
_alias_override_targets: dict[str, dict[str, set[str]]] = {}
# novel_id -> {source: {aliases the user split AWAY from it}} — so aggregation
# stops listing them under the source even when they detached to a new entity
# (alias_map alone can't express "removed from X" for to=None splits).
_alias_detached: dict[str, dict[str, set[str]]] = {}


def invalidate_alias_cache(novel_id: str) -> None:
    """Clear cached alias map for a novel (call after prescan or analysis completes)."""
    _alias_cache.pop(novel_id, None)
    _alias_conflicts.pop(novel_id, None)
    _alias_override_targets.pop(novel_id, None)
    _alias_detached.pop(novel_id, None)


def get_detached_aliases(novel_id: str) -> dict[str, set[str]]:
    """{source: aliases the user split away from it}. Reflects current overrides
    once build_alias_map has run for the novel."""
    return _alias_detached.get(novel_id, {})


def get_alias_conflicts(novel_id: str) -> set[str]:
    """Aliases whose user override conflicts with the current auto resolution.

    Populated as a side effect of build_alias_map → _apply_user_overrides.
    Returns an empty set if the map has not been built or there are no conflicts.
    """
    return _alias_conflicts.get(novel_id, set())


async def get_override_targets(novel_id: str) -> dict[str, set[str]]:
    """{canonical: {aliases attributed to it by a user override}} for a novel.

    Ensures the alias map (and thus override application) has been built first,
    so callers can rely on the result reflecting current overrides.
    """
    await build_alias_map(novel_id)
    return _alias_override_targets.get(novel_id, {})


# ── Union-Find ────────────────────────────────────


class _UnionFind:
    """Simple Union-Find to merge alias groups."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self._size: dict[str, int] = {}  # root -> group size

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            self._size[x] = 1
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Union by size — attach smaller to larger
            if self._size.get(ra, 1) < self._size.get(rb, 1):
                self.parent[ra] = rb
                self._size[rb] = self._size.get(rb, 1) + self._size.get(ra, 1)
            else:
                self.parent[rb] = ra
                self._size[ra] = self._size.get(ra, 1) + self._size.get(rb, 1)

    def group_size(self, x: str) -> int:
        """Return the size of the group containing x."""
        if x not in self.parent:
            return 0
        return self._size.get(self.find(x), 1)

    def groups(self) -> dict[str, list[str]]:
        """Return root -> list of members."""
        result: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            result[self.find(x)].append(x)
        return result


# ── Unsafe alias filter ───────────────────────────
# v0.72 Phase 1 (Story 1.3): the term lists formerly defined here
# (_KINSHIP_TERMS, _GENERIC_PERSON_ALIASES, _TITLE_PREFIXES,
# _TITLE_SUFFIXES_2) were dead duplicates — name_authority.py holds
# the authoritative copies (supersets) since v0.70.3. Deleted.


def _alias_safety_level(alias: str) -> int:
    """Return alias safety level: 0=hard-block, 1=soft-block(suspicious), 2=safe.

    Delegates to name_authority.alias_safety_level() — single source of truth.
    This wrapper is kept for backward compatibility with external callers.
    """
    return name_authority.alias_safety_level(alias)


def _is_unsafe_alias(alias: str) -> bool:
    """Check if an alias is unsafe to use as a Union-Find key."""
    return name_authority.is_unsafe_alias(alias)


# ── Core function ─────────────────────────────────


async def build_alias_map(novel_id: str) -> dict[str, str]:
    """Build alias -> canonical_name mapping.

    Merges alias information from BOTH sources:
    1. entity_dictionary (pre-scan LLM generated alias groups)
    2. ChapterFact.characters[].new_aliases (per-chapter extraction)

    Both sources are combined via Union-Find to produce comprehensive groups.
    Canonical name rule: the name with highest frequency in the group.
    Returns {alias: canonical, ...}. The canonical name does NOT map to itself.
    """
    if novel_id in _alias_cache:
        return _alias_cache[novel_id]

    alias_map = await _build_merged(novel_id)
    alias_map = _apply_known_hotfix_patches(alias_map)
    alias_map = await _apply_user_overrides(novel_id, alias_map)

    _alias_cache[novel_id] = alias_map
    if alias_map:
        logger.info("Built alias map for novel %s: %d aliases", novel_id, len(alias_map))
    return alias_map


# ── User override layer (manual merge/split) ──────────
#
# Applied AFTER automatic resolution + hotfix patches, so user edits are the
# last writer (D3) and survive rebuilds — every build_alias_map re-applies them
# on top of a freshly computed auto map (FR4/SC2). With no overrides stored this
# is a no-op and the output is byte-identical to the pure-automatic result, so
# the gold-standard baseline is unaffected.


async def _apply_user_overrides(novel_id: str, alias_map: dict[str, str]) -> dict[str, str]:
    """Overlay user alias merge/split overrides onto the automatic alias_map.

    Also records, into ``_alias_conflicts[novel_id]``, any alias whose automatic
    resolution now differs from the snapshot captured when the override was
    created — surfaced to the UI as a non-destructive "conflict" marker (FR7).
    The override still wins regardless; the marker is advisory only.
    """
    from src.db import entity_override_store

    overrides = await entity_override_store.load_overrides(novel_id)
    conflicts: set[str] = set()
    targets: dict[str, set[str]] = {}
    detached: dict[str, set[str]] = {}

    # FR-2.4 (Epic 2): LLM 决策(llm_merge)先于手动 override 应用 — 手动
    # merge/split/rename 始终是 last writer,优先级高于任何 LLM 决策。
    # 无 llm_merge 条目时应用顺序与改动前完全一致(逐字节不变量)。
    ordered = (
        [ov for ov in overrides if ov["override_type"] == "llm_merge"]
        + [ov for ov in overrides if ov["override_type"] != "llm_merge"]
    )

    for ov in ordered:
        j = ov.get("override_json") or {}
        snapshot = j.get("auto_snapshot") or {}

        if ov["override_type"] in ("alias_merge", "llm_merge"):
            canon = j.get("canonical")
            if not canon:
                continue
            for member in j.get("members", []):
                # FR-2.3: 防桥接约束在 LLM 路径同样生效 — 与 canonical 结构
                # 相似但不同名(阮小二≠阮小七类)的成员永不并入。手动 merge 是
                # 用户的显式意图,不做此拦截。
                if (
                    ov["override_type"] == "llm_merge"
                    and member != canon
                    and name_authority.similar_name_conflict(member, canon)
                ):
                    logger.info(
                        "Merge blocked by similar-name conflict: '%s' → '%s' (%s)",
                        member, canon, ov["override_type"],
                    )
                    continue
                # Detect drift vs. the auto result at override-creation time.
                cur_auto = alias_map.get(member, member)
                snap = snapshot.get(member)
                if snap is not None and cur_auto != snap:
                    conflicts.add(member)
                # Force the member onto the user-chosen canonical (D1 lock).
                if member == canon:
                    alias_map.pop(member, None)  # canonical must not map to itself
                else:
                    alias_map[member] = canon
                    targets.setdefault(canon, set()).add(member)

        elif ov["override_type"] == "alias_split":
            to = j.get("to")  # None => detach into a new independent entity
            source = j.get("source")
            split_aliases = j.get("aliases", [])
            for alias in split_aliases:
                cur_auto = alias_map.get(alias, alias)
                snap = snapshot.get(alias)
                if snap is not None and cur_auto != snap:
                    conflicts.add(alias)
                if to and to != alias:
                    alias_map[alias] = to
                    targets.setdefault(to, set()).add(alias)
                else:
                    # to is None, or equals the alias itself ("this name is its
                    # own entity") — detach without a self-map (invariant: a
                    # canonical never maps to itself).
                    alias_map.pop(alias, None)
                    targets.setdefault(alias, set()).add(alias)
            # The source entity was also edited (aliases removed) — mark it so
            # its card shows "已修正" and offers undo (FR6), and record the
            # detachment so aggregation drops these aliases from the source.
            if source and split_aliases:
                targets.setdefault(source, set()).update(split_aliases)
                detached.setdefault(source, set()).update(split_aliases)

        elif ov["override_type"] == "entity_rename":
            # Relabel an entity's canonical/display name to a user-typed name.
            # Everything currently resolving to `old` (and old itself) moves to
            # `new`. If `new` already exists as an entity this becomes a merge.
            old = ov["override_key"]
            new = j.get("to")
            if not new or new == old:
                continue
            cur_auto = alias_map.get(old, old)
            snap = snapshot.get(old)
            if snap is not None and cur_auto != snap:
                conflicts.add(old)
            for alias, canon in list(alias_map.items()):
                if canon == old:
                    alias_map[alias] = new
            alias_map[old] = new          # old name becomes an alias of new
            alias_map.pop(new, None)      # new is canonical, must not map to itself
            targets.setdefault(new, set()).add(old)

    _alias_conflicts[novel_id] = conflicts
    _alias_override_targets[novel_id] = targets
    _alias_detached[novel_id] = detached
    if conflicts:
        logger.info(
            "Alias overrides for novel %s: %d conflict(s) with auto resolution",
            novel_id,
            len(conflicts),
        )
    return alias_map


# ── Known-bug hotfix patches ───────────────────────
#
# These patches correct specific UF mis-merges caused by LLM-extracted compound
# entity names (e.g., "八戒沙僧" forcing 沙僧's aliases into 八戒's canonical
# group). Each patch detects its bug signature in the current map — if the
# signature is absent, the patch is a no-op. Once the root cause is fixed in
# _alias_safety_level (P1 work: down-weight compound names containing 2+ primary
# entities), these patches become inert and can be removed.

def _hotfix_xiyouji_sha_bajie(alias_map: dict[str, str]) -> dict[str, str]:
    """Split 沙僧's aliases wrongly merged into 八戒.

    Root cause: entity_dictionary contains compound name "八戒沙僧" (LLM
    mis-extraction). Its declared aliases pull both canonical identities into
    the same UF tree; canonical selection picks 八戒 (higher frequency), so
    every 沙-identity alias ends up pointing at 八戒.
    """
    if alias_map.get("沙悟净") != "八戒" and alias_map.get("卷帘大将") != "八戒":
        return alias_map
    sha_only_aliases = {
        "沙僧", "沙悟净", "沙和尚",
        "悟净",
        "卷帘大将", "卷帘将", "卷帘",
        "金身罗汉",
        "老沙", "小沙僧",
        "沙四官儿",
        "三徒弟",
    }
    patched = dict(alias_map)
    moved = 0
    for alias in list(patched.keys()):
        if alias in sha_only_aliases and patched[alias] == "八戒":
            patched[alias] = "沙僧"
            moved += 1
    # Canonical names must not map to themselves (alias_map contract).
    if patched.get("沙僧") == "沙僧":
        del patched["沙僧"]
    if moved:
        logger.info("hotfix_xiyouji_sha_bajie: rerouted %d aliases 八戒→沙僧", moved)
    return patched


def _apply_known_hotfix_patches(alias_map: dict[str, str]) -> dict[str, str]:
    """Apply all known-bug hotfix patches. No-op when bug signatures are absent."""
    alias_map = _hotfix_xiyouji_sha_bajie(alias_map)
    return alias_map


async def _build_merged(novel_id: str) -> dict[str, str]:
    """Build alias map by merging entity_dictionary AND chapter_facts sources."""
    conn = await get_connection()
    try:
        # Source 1: entity_dictionary
        cursor = await conn.execute(
            """
            SELECT name, frequency, aliases, entity_type
            FROM entity_dictionary
            WHERE novel_id = ?
            ORDER BY frequency DESC
            """,
            (novel_id,),
        )
        dict_rows = await cursor.fetchall()

        # Source 2: chapter_facts
        cursor = await conn.execute(
            """
            SELECT cf.fact_json
            FROM chapter_facts cf
            WHERE cf.novel_id = ?
            """,
            (novel_id,),
        )
        fact_rows = await cursor.fetchall()

        # Source 3 (v0.71.1): novel title → knowledge prior lookup
        cursor = await conn.execute(
            "SELECT title FROM novels WHERE id = ?", (novel_id,)
        )
        title_row = await cursor.fetchone()
        novel_title = (title_row["title"] if title_row else "") or ""
    finally:
        await conn.close()

    if not dict_rows and not fact_rows:
        return {}

    uf = _UnionFind()
    freq: dict[str, int] = defaultdict(int)

    # Collect all primary entity names from entity_dictionary.
    # These are known independent entities and should not be merged together.
    dict_primary_names: set[str] = set()

    _MAX_GROUP_SIZE = 20  # absolute cap — no character should have 20+ aliases
    # Track deferred merge evidence for large-group and primary-entity conflicts.
    # Pairs with >= _MIN_CHAPTER_EVIDENCE chapters are merged in a second pass.
    _primary_pair_evidence: dict[tuple[str, str], int] = defaultdict(int)
    _MIN_CHAPTER_EVIDENCE = 3
    # Map alias → dict_primary_name it belongs to (for short-alias disambiguation)
    _alias_to_dict_primary: dict[str, str] = {}

    def _similar_name_conflict(a: str, b: str) -> bool:
        """Detect structurally similar but distinct names (e.g., 阮小二 vs 阮小七).

        Delegates to name_authority.similar_name_conflict() — single source of
        truth, shared with the LLM entity-resolution path (Epic 2, FR-2.3).
        """
        return name_authority.similar_name_conflict(a, b)

    def _safe_union(name: str, alias: str, source: str) -> None:
        """Union name and alias with multi-layer conflict detection.

        Blocks merges when:
        0. Names are structurally similar but distinct (阮小二 vs 阮小七)
        1. Both are known primary entities in entity_dictionary
        2. EITHER group is already well-established (size >= 5)
        3. Combined group would exceed _MAX_GROUP_SIZE
        """
        # Layer 0: similar-name conflict — even dict stage can produce bad merges
        # (e.g., prescan LLM incorrectly groups 阮小二/阮小五/阮小七)
        if _similar_name_conflict(name, alias):
            logger.debug(
                "Similar-name conflict (%s): '%s' vs '%s', block merge",
                source, name, alias,
            )
            return

        # Layer 1: both are known primary entities → block in fact stage only.
        # Dictionary stage declares explicit alias groups (e.g., 行者↔孙行者↔大圣),
        # so merging primary entities from the same dict entry is intentional.
        if source != "dict" and name in dict_primary_names and alias in dict_primary_names:
            logger.debug(
                "Both primary entities (%s): '%s' ↔ '%s', skip union",
                source, name, alias,
            )
            return

        # Layer 0.5: Alias disambiguation — block merges when both sides
        # trace to different known primary entities.
        # Catches shared titles ("大刀", "天王", "杨制使", "水军头领") that
        # the LLM assigns as aliases to multiple different characters.
        #
        # v0.71.1 substring exception: allow merge when one name is a strict
        # suffix of the other with length difference 1 (i.e., surname + given
        # name vs given name alone). This fixes the 红楼梦 "贾X" split:
        #   贾宝玉 ⊃ 宝玉, 薛宝钗 ⊃ 宝钗, 贾探春 ⊃ 探春, 贾惜春 ⊃ 惜春, 贾迎春 ⊃ 迎春
        # Prefix relationships (阮小 ⊂ 阮小二) are still blocked upstream by
        # `_similar_name_conflict` (Layer 0).
        if source != "dict":
            name_primary = _alias_to_dict_primary.get(name)
            alias_primary = _alias_to_dict_primary.get(alias)
            if name_primary and alias_primary and name_primary != alias_primary:
                shorter, longer = (
                    (name, alias) if len(name) < len(alias) else (alias, name)
                )
                is_surname_suffix = (
                    len(shorter) >= 2
                    and len(longer) - len(shorter) == 1
                    and longer.endswith(shorter)
                )
                if is_surname_suffix:
                    logger.info(
                        "Substring exception (%s): '%s' ⊂ '%s', allow merge",
                        source, shorter, longer,
                    )
                    # Fall through to normal merge logic
                else:
                    logger.debug(
                        "Alias ownership conflict (%s): '%s' (→%s) vs '%s' (→%s), block",
                        source, name, name_primary, alias, alias_primary,
                    )
                    return

        if alias not in uf.parent:
            uf.union(name, alias)
            return
        alias_root = uf.find(alias)
        name_root = uf.find(name)
        if alias_root == name_root:
            return  # already in same group

        alias_size = uf.group_size(alias)
        name_size = uf.group_size(name)

        # Layer 2: either group already well-established → block (fact stage only).
        # Dictionary-declared aliases are authoritative and should merge even
        # into large groups (e.g., 孙悟空 has 7+ aliases in 西游记).
        # Blocked pairs are tracked in _primary_pair_evidence for deferred
        # merging when sufficient chapter evidence accumulates.
        if source != "dict" and (alias_size >= 5 or name_size >= 5):
            pair = (min(name, alias), max(name, alias))
            _primary_pair_evidence[pair] += 1
            logger.debug(
                "Group conflict (%s): '%s' (group=%d) vs '%s' (group=%d), "
                "deferred to evidence check",
                source, alias, alias_size, name, name_size,
            )
            return

        # Layer 3: combined size exceeds cap → block
        if alias_size + name_size > _MAX_GROUP_SIZE:
            logger.debug(
                "Group cap exceeded (%s): '%s' (%d) + '%s' (%d) > %d",
                source, name, name_size, alias, alias_size, _MAX_GROUP_SIZE,
            )
            return

        uf.union(name, alias)

    # ── Ingest entity_dictionary ──
    # First pass: collect all primary entity names for conflict detection.
    # Entity dictionary entries (from pre-scan) override the generic blocklist:
    # if the pre-scan LLM identified "三叔" as a specific person entity, it should
    # be treated as a named character, not a generic kinship term.
    #
    # v0.71.1: unknown-type entries are NO LONGER unconditionally skipped.
    # High-frequency unknown entries (e.g. "齐天大圣" freq=102 in 西游记) still
    # need their aliases rescued, even if we don't promote them to primaries.
    _UNKNOWN_RESCUE_MIN_FREQ = 30
    for row in dict_rows:
        entity_type = row["entity_type"] or "unknown"
        frequency = row["frequency"] or 0
        if entity_type == "unknown" and frequency < _UNKNOWN_RESCUE_MIN_FREQ:
            continue
        name = _normalize_char_variants(row["name"])
        level = _alias_safety_level(name)
        # Only promote to primary if type is person AND name is safe.
        # unknown-type entries pass through to the rescue branch below
        # (they union their aliases but don't become primaries themselves).
        if entity_type == "unknown":
            continue  # first pass: skip unknown, handled in second pass rescue
        if level >= 2:
            dict_primary_names.add(name)
        elif level == 0 and entity_type == "person" and frequency >= 10:
            # Pre-scan identified this as a high-frequency person entity — override
            # the generic blocklist. E.g., "三叔" in 凡人修仙传 is a specific character.
            dict_primary_names.add(name)
            logger.info("Dict override for blocked name '%s' (freq=%d, type=%s)",
                        name, frequency, entity_type)

    # Second pass: build Union-Find groups
    for row in dict_rows:
        entity_type = row["entity_type"] or "unknown"
        frequency = row["frequency"] or 0
        # v0.71.1: allow unknown-type entries through if freq is high enough,
        # so their alias groups (e.g. 齐天大圣 → {齐天大圣, 大圣, 猴王, 老孙})
        # get rescued via the blocked-name branch below.
        if entity_type == "unknown" and frequency < _UNKNOWN_RESCUE_MIN_FREQ:
            continue

        name = _normalize_char_variants(row["name"])
        frequency = row["frequency"] or 0
        aliases_raw = row["aliases"]
        aliases: list[str] = json.loads(aliases_raw) if aliases_raw else []

        # If name is a generic/contextual term (妖精, 那怪, 父王, 公主, etc.):
        # Don't register it as a UF node, but rescue its safe aliases by
        # union-ing them together. This preserves alias chains like
        # "太子" → {"哪吒", "三太子"} → group {"哪吒", "三太子"} without
        # using the blocked name as a bridge node.
        if name not in dict_primary_names:
            safe_aliases = [a for a in aliases
                            if a and a != name and _alias_safety_level(a) >= 2]
            if len(safe_aliases) >= 2:
                first = safe_aliases[0]
                freq.setdefault(first, 0)
                uf.find(first)
                for other in safe_aliases[1:]:
                    freq.setdefault(other, 0)
                    _safe_union(first, other, "dict")
                logger.debug(
                    "Rescued %d aliases from blocked name '%s': %s",
                    len(safe_aliases), name, safe_aliases,
                )
            continue

        freq[name] = max(freq.get(name, 0), frequency)
        uf.find(name)  # ensure registered
        _alias_to_dict_primary[name] = name  # primary maps to itself

        for raw_alias in aliases:
            alias = _normalize_char_variants(raw_alias) if raw_alias else ""
            if alias and alias != name:
                level = _alias_safety_level(alias)
                if level < 2:
                    logger.debug("Alias blocked (L%d) from dict: %s → %s", level, name, alias)
                    continue
                freq[alias] = max(freq.get(alias, 0), 0)
                _alias_to_dict_primary.setdefault(alias, name)  # track which primary this alias belongs to
                _safe_union(name, alias, "dict")

    # ── Ingest chapter_facts new_aliases ──
    for row in fact_rows:
        data = json.loads(row["fact_json"])
        for char in data.get("characters", []):
            name = _normalize_char_variants(char.get("name", ""))
            if not name:
                continue

            # If name is an unsafe generic (大汉, 后生, 和尚, 妖精, etc.):
            # Skip entirely — don't register the name OR its aliases.
            # Rationale: when the LLM extracts a character with a generic
            # name, the alias assignments are unreliable and create false
            # bridges (e.g., "大汉" → ["李大哥", "李俊"] merges two
            # unrelated characters).
            if _is_unsafe_alias(name):
                logger.debug("Skip generic character name: %s (aliases: %s)",
                             name, char.get("new_aliases", []))
                continue

            freq[name] += 1
            uf.find(name)
            # Register character name ownership for conflict detection
            _alias_to_dict_primary.setdefault(name, name)

            for raw_alias in char.get("new_aliases", []):
                alias = _normalize_char_variants(raw_alias) if raw_alias else ""
                if alias and alias != name:
                    level = _alias_safety_level(alias)
                    if level < 2:
                        logger.debug("Alias blocked (L%d) from fact: %s → %s", level, name, alias)
                        continue
                    # Track evidence for primary-entity pairs instead of blocking
                    if alias in dict_primary_names and name in dict_primary_names:
                        pair = (min(name, alias), max(name, alias))
                        _primary_pair_evidence[pair] += 1
                        continue
                    freq.setdefault(alias, 0)
                    # Track alias ownership for disambiguation (any length, any source)
                    # First character to claim an alias "owns" it — later conflicts are blocked
                    _alias_to_dict_primary.setdefault(alias, name)
                    _safe_union(name, alias, "fact")

    # v0.71.1 knowledge prior merge — authoritative alias groups for well-known
    # classical novels (西游记/红楼梦/水浒传/三国演义). Bypasses all Union-Find
    # safety layers because these groups are curated by hand. Fixes cases where
    # Pre-scan LLM creates SEPARATE primary entries for the same character that
    # never share an alias (e.g. 孙悟空 / 石猴 / 猴精 in 西游记).
    #
    # Proactively adds ALL group members to UF, even names that would normally
    # be filtered as unsafe (e.g. "观音菩萨" ends with title suffix 菩萨; "薛姨妈"
    # ends with tail blocklist 姨妈). For these classical novels they are the
    # canonical forms used throughout the text.
    from src.services.person_knowledge_prior import get_person_priors
    priors = get_person_priors(novel_title)
    if priors:
        prior_merges = 0
        for group in priors:
            if len(group) < 2:
                continue
            anchor = group[0]
            freq.setdefault(anchor, 0)
            uf.find(anchor)  # force-register anchor
            # Also register anchor as its own dict_primary so canonical
            # selection trusts it.
            dict_primary_names.add(anchor)
            _alias_to_dict_primary.setdefault(anchor, anchor)
            for other in group[1:]:
                freq.setdefault(other, 0)
                uf.find(other)
                if uf.find(anchor) != uf.find(other):
                    uf.union(anchor, other)
                    prior_merges += 1
                # Register alias ownership pointing to anchor
                _alias_to_dict_primary[other] = anchor
        logger.info(
            "Knowledge prior (%s): merged %d alias pairs across %d groups",
            novel_title, prior_merges, len(priors),
        )

    # Second pass: merge deferred pairs with strong chapter evidence.
    # Direct uf.union() bypasses all layers — the chapter evidence threshold
    # is sufficient quality control. Sort by evidence count (descending) so
    # the strongest pairs merge first and establish canonical names early.
    for (a, b), count in sorted(
        _primary_pair_evidence.items(), key=lambda x: -x[1]
    ):
        if count >= _MIN_CHAPTER_EVIDENCE:
            a_root = uf.find(a) if a in uf.parent else a
            b_root = uf.find(b) if b in uf.parent else b
            if a_root == b_root:
                continue  # already merged
            combined = uf.group_size(a) + uf.group_size(b)
            if combined > 50:  # generous cap for evidence-backed merges
                logger.debug(
                    "Evidence merge skipped (combined=%d > 50): '%s' ↔ '%s'",
                    combined, a, b,
                )
                continue
            logger.info(
                "Evidence merge (evidence=%d chapters): '%s' ↔ '%s'",
                count, a, b,
            )
            freq.setdefault(a, 0)
            freq.setdefault(b, 0)
            uf.find(a)  # ensure registered
            uf.find(b)
            uf.union(a, b)

    return _groups_to_map(uf, freq, dict_primary_names)


# v0.72 Phase 1 (Story 1.3): _CANONICAL_BLOCKLIST, _TITLE_SUFFIXES,
# _NICKNAME_PATTERNS, _NICKNAME_SUFFIXES, _NICKNAME_PREFIXES deleted —
# dead duplicates; name_authority.py holds the authoritative copies.




def _is_nickname_or_title(name: str) -> bool:
    """Check if a name looks like a nickname, courtesy name, or title form.

    Delegates to name_authority — single source of truth.
    """
    return name_authority.is_nickname_or_title(name)


def _pick_canonical(members: list[str], freq: dict[str, int],
                    dict_primary_names: set[str] | None = None) -> str:
    """Pick the best canonical name from an alias group.

    Delegates to name_authority.pick_canonical() — single source of truth.
    This wrapper is kept for backward compatibility with _groups_to_map().
    """
    return name_authority.pick_canonical(members, freq, dict_primary_names)


def _groups_to_map(uf: _UnionFind, freq: dict[str, int],
                   dict_primary_names: set[str] | None = None) -> dict[str, str]:
    """Convert Union-Find groups into alias -> canonical mapping."""
    alias_map: dict[str, str] = {}

    for _root, members in uf.groups().items():
        if len(members) <= 1:
            continue
        canonical = _pick_canonical(members, freq, dict_primary_names)
        for member in members:
            if member != canonical:
                alias_map[member] = canonical

    return alias_map
