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

logger = logging.getLogger(__name__)

# ── Module-level cache ────────────────────────────

_alias_cache: dict[str, dict[str, str]] = {}  # novel_id -> alias_map


def invalidate_alias_cache(novel_id: str) -> None:
    """Clear cached alias map for a novel (call after prescan or analysis completes)."""
    _alias_cache.pop(novel_id, None)


# ── Union-Find ────────────────────────────────────


class _UnionFind:
    """Simple Union-Find to merge alias groups."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        """Return root -> list of members."""
        result: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            result[self.find(x)].append(x)
        return result


# ── Unsafe alias filter ───────────────────────────
# These terms are contextual — they refer to different people depending on
# who is speaking or which chapter we're in. Using them as Union-Find keys
# creates false bridges that merge unrelated character groups.

_KINSHIP_TERMS = frozenset({
    # Direct family
    "哥哥", "弟弟", "姐姐", "妹妹", "妈妈", "爸爸", "爸", "妈",
    "父亲", "母亲", "儿子", "女儿", "妻子", "丈夫", "老婆", "老公",
    "媳妇", "婆婆", "公公", "岳父", "岳母", "丈人", "老丈人",
    "嫂子", "弟媳", "弟媳妇", "姐夫", "妹夫",
    "爷爷", "奶奶", "外公", "外婆", "外爷", "祖母", "老祖母",
    "孙子", "孙女", "外孙", "外孙女", "小外孙",
    "侄子", "侄女", "侄儿", "外甥", "女婿", "侄女婿",
    "老伴", "新郎", "新娘",
    # Ranked kinship
    "大哥", "二哥", "三哥", "大姐", "二姐", "三姐",
    "大嫂", "二嫂", "大叔", "二叔", "三叔",
    "大婶", "二婶",
    # Informal kinship
    "哥", "弟", "姐", "妹",
    "他哥", "他弟", "他姐", "他妹", "他妈", "他爸",
    "她哥", "她弟", "她姐", "她妹", "她妈", "她爸",
    "你哥", "你弟", "你姐", "你妹", "你妈", "你爸",
    "我哥", "我弟", "我姐", "我妹", "我妈", "我爸", "我嫂",
    "他奶", "她奶", "少安他奶",
})

_GENERIC_PERSON_ALIASES = frozenset({
    # Age/gender generics
    "老人", "老汉", "老人家", "老太太", "老奶奶", "老将", "老首长",
    "青年", "少年", "小子", "大小子", "二小子", "男人", "女人",
    "小家伙", "小伙子", "胖小子", "男娃娃", "女娃娃",
    # Role/title generics
    "队长", "副书记", "副主任", "主任", "专员", "助手", "老师傅",
    "饲养员", "公派教师", "县领导", "高参",
    # Collective/vague
    "众人", "其他人", "旁人", "大家", "孩子", "孩子们", "娃娃",
    "老干部", "妇女主任",
})

_TITLE_PREFIXES = frozenset({
    "堂主", "长老", "弟子", "护法", "掌门", "帮主", "教主",
    "师父", "师兄", "师弟", "师姐", "师妹",
})


def _is_unsafe_alias(alias: str) -> bool:
    """Check if an alias is unsafe to use as a Union-Find key.

    Unsafe aliases are contextual terms that can refer to different entities
    in different chapters. Using them in Union-Find creates false bridges.
    """
    if not alias or len(alias) < 1:
        return True

    # Kinship terms — always contextual
    if alias in _KINSHIP_TERMS:
        return True

    # Generic person references
    if alias in _GENERIC_PERSON_ALIASES:
        return True

    # Pure title words
    if alias in _TITLE_PREFIXES:
        return True

    # Contains 的 — possessive/descriptive (e.g., "孙少平的母亲", "地主家的儿媳妇")
    if "的" in alias:
        return True

    # Too long (>8 chars) — likely a description, not a name
    if len(alias) > 8:
        return True

    # Ends with kinship suffix — "X他妈", "X她爸", "X他姐"
    if len(alias) >= 3:
        tail2 = alias[-2:]
        if tail2 in {"他妈", "她妈", "他爸", "她爸", "他姐", "她姐",
                      "他哥", "她哥", "他弟", "她弟", "他奶", "她奶",
                      "妈妈", "爸爸"}:
            return True
        # "X夫妇", "X两口", "X老婆"
        if tail2 in {"夫妇", "两口", "老婆"}:
            return True

    return False


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

    _alias_cache[novel_id] = alias_map
    if alias_map:
        logger.info("Built alias map for novel %s: %d aliases", novel_id, len(alias_map))
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
    finally:
        await conn.close()

    if not dict_rows and not fact_rows:
        return {}

    uf = _UnionFind()
    freq: dict[str, int] = defaultdict(int)

    # ── Ingest entity_dictionary ──
    # Only use entries with a real entity_type (skip 'unknown' noise like "行者笑", "者道")
    for row in dict_rows:
        entity_type = row["entity_type"] or "unknown"
        if entity_type == "unknown":
            continue

        name = row["name"]
        frequency = row["frequency"] or 0
        aliases_raw = row["aliases"]
        aliases: list[str] = json.loads(aliases_raw) if aliases_raw else []

        freq[name] = max(freq.get(name, 0), frequency)
        uf.find(name)  # ensure registered

        for alias in aliases:
            if alias and alias != name:
                if _is_unsafe_alias(alias):
                    logger.debug("Skipping unsafe alias from dict: %s → %s", name, alias)
                    continue
                freq[alias] = max(freq.get(alias, 0), 0)
                uf.union(name, alias)

    # ── Ingest chapter_facts new_aliases ──
    for row in fact_rows:
        data = json.loads(row["fact_json"])
        for char in data.get("characters", []):
            name = char.get("name", "")
            if not name:
                continue
            # Skip characters whose name is itself an unsafe alias
            # (they should not participate in alias linking at all)
            if _is_unsafe_alias(name):
                continue
            freq[name] += 1
            uf.find(name)

            for alias in char.get("new_aliases", []):
                if alias and alias != name:
                    if _is_unsafe_alias(alias):
                        logger.debug("Skipping unsafe alias from fact: %s → %s", name, alias)
                        continue
                    freq.setdefault(alias, 0)
                    uf.union(name, alias)

    return _groups_to_map(uf, freq)


def _groups_to_map(uf: _UnionFind, freq: dict[str, int]) -> dict[str, str]:
    """Convert Union-Find groups into alias -> canonical mapping."""
    alias_map: dict[str, str] = {}

    for _root, members in uf.groups().items():
        if len(members) <= 1:
            continue
        # Pick canonical = highest frequency in group
        canonical = max(members, key=lambda m: freq.get(m, 0))
        for member in members:
            if member != canonical:
                alias_map[member] = canonical

    return alias_map
