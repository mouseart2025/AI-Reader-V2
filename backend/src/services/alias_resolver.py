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
    "大哥", "二哥", "三哥", "四哥", "五哥", "大姐", "二姐", "三姐",
    "大嫂", "二嫂", "三嫂", "大叔", "二叔", "三叔",
    "大婶", "二婶",
    # Informal kinship
    "哥", "弟", "姐", "妹",
    "他哥", "他弟", "他姐", "他妹", "他妈", "他爸",
    "她哥", "她弟", "她姐", "她妹", "她妈", "她爸",
    "你哥", "你弟", "你姐", "你妹", "你妈", "你爸",
    "我哥", "我弟", "我姐", "我妹", "我妈", "我爸", "我嫂",
    "他奶", "她奶", "少安他奶",
    # Classical Chinese kinship/address — shared across characters, create false bridges
    "兄弟", "兄长", "贤弟", "贤侄", "贤妹", "贤婿",
    "嫂嫂", "娘子", "婆娘", "夫人", "小姐", "姑娘", "娘",
    "叔叔", "伯伯", "伯父", "叔父", "舅舅", "舅父",
    "爹爹", "爹", "老爹", "老娘", "亲娘", "干爹", "干娘",
    "义兄", "义弟", "义父", "义母", "义子", "义女",
    "恩人", "恩公", "恩师",
    # Generic address terms used for multiple people — major bridge causes
    "阿哥", "阿弟", "阿妹", "阿姐",
    "大郎", "二郎", "三郎", "四郎", "五郎", "六郎", "七郎",
    "浑家", "老母", "老身", "婆子", "老婆子",
    "太公", "老太公",
})

_GENERIC_PERSON_ALIASES = frozenset({
    # Age/gender generics
    "老人", "老汉", "老人家", "老太太", "老奶奶", "老将", "老首长",
    "老儿", "老者", "老翁", "老丈", "老官", "老先生",
    "青年", "少年", "小子", "大小子", "二小子", "男人", "女人",
    "小家伙", "小伙子", "胖小子", "男娃娃", "女娃娃",
    "妇人", "妇女", "女子", "那女子", "那妇人", "那女人",
    "汉子", "大汉", "壮汉", "那汉", "那大汉", "黑大汉",
    "少女", "丫头", "丫鬟", "侍女", "侍儿", "婢女",
    "小的", "小人", "在下", "晚辈", "小生",
    "那人", "此人", "其人", "何人", "某人",
    "来人", "路人", "行人", "过客", "客人", "客官",
    # Role/title generics — shared across many characters
    "队长", "副书记", "副主任", "主任", "专员", "助手", "老师傅",
    "饲养员", "公派教师", "县领导", "高参",
    "好汉", "壮士", "英雄", "义士", "豪杰", "勇士",
    "军士", "军汉", "军校", "士兵", "兵丁", "喽啰", "小喽啰",
    "差人", "差役", "官差", "公差", "衙役", "捕快",
    "和尚", "僧人", "道士", "道人", "先生", "秀才", "书生",
    "大官人", "官人", "相公", "员外", "财主", "大户",
    "头领", "头目", "首领", "寨主", "山大王",
    "店家", "店主", "小二", "店小二", "酒保",
    "庄主", "庄客", "农夫", "猎户", "渔夫", "樵夫",
    "使者", "信使", "探子", "细作",
    # Classical Chinese deictics — refer to different people per chapter
    "那厮", "这厮", "那泼贼", "那贼", "泼贼", "贼人", "贼子",
    "那泼怪", "那泼物", "泼才",
    "这位", "那位", "此人", "这人", "那人",
    # Collective/vague
    "众人", "其他人", "旁人", "大家", "孩子", "孩子们", "娃娃",
    "老干部", "妇女主任",
    "众好汉", "众兄弟", "众将", "众军", "众头领",
    "众位", "诸位", "各位", "列位",
    # Fantasy/wuxia/xianxia contextual generics — refer to different entities per chapter
    "妖精", "妖怪", "妖魔", "妖王", "妖邪", "妖仙", "妖",
    "那怪", "泼怪", "泼物", "泼猴", "怪物", "老妖",
    "大王", "洞主", "小妖", "众妖", "众怪", "群怪",
    "女婿", "上仙", "大仙", "仙长", "真人",
    "孽畜", "畜生",
    # Pronouns / deictics — can refer to anyone
    "我们", "我等", "他们", "她们",
    # Collective kinship — refer to groups, not individuals
    "儿孙", "子侄",
    # Insults/pejoratives — used for many different characters
    "淫妇", "贱人", "贼配军", "奸夫", "奸贼", "逆贼", "反贼",
    # Generic self-references (classical Chinese "I/me")
    "老身", "寡人", "酒家", "洒家", "老子", "小可",
    # Generic role terms shared across many officials/characters
    "公人", "统制官", "太守", "府尹",
    "天子", "圣上", "皇帝", "皇上", "官家", "万岁",
    "使女", "伴当", "店主人", "小二哥",
    "军师", "国师", "院长", "副先锋", "节度使", "小将军",
    "泰山",  # means "father-in-law" in classical Chinese, bridges unrelated chars
    # More generic terms found in 水浒传 analysis
    "童子", "道童", "仙童", "仙女", "渔人",
    "囚徒", "罪犯", "犯人", "配军",
    "长汉", "黑汉", "黑汉子", "黑厮", "黑杀才",
    # Age-based generics
    "后生", "後生", "少年人", "年轻人", "小后生",
    # More generic role terms
    "节级", "都头", "提辖", "制使", "管营", "知寨",
})

_TITLE_PREFIXES = frozenset({
    "堂主", "长老", "弟子", "护法", "掌门", "帮主", "教主",
    "师父", "师兄", "师弟", "师姐", "师妹", "师傅",
    # Official ranks — shared across many characters in classical novels
    "太尉", "知府", "知县", "县令", "提辖", "都监", "团练",
    "总管", "管营", "差拨", "节级", "牢头", "押司",
    "教头", "教师", "都头", "虞候", "制使",
    "将军", "元帅", "统制", "统领", "指挥",
    "丞相", "宰相", "太师", "太保", "枢密",
    "知寨", "巡检", "经略", "经略相公",
    "恩相", "大人", "老爷", "相公",
})


def _alias_safety_level(alias: str) -> int:
    """Return alias safety level: 0=hard-block, 1=soft-block(suspicious), 2=safe."""
    if not alias or len(alias) < 1:
        return 0

    n = len(alias)

    # Level 0: absolute block — kinship terms, 的 phrases, trailing kinship suffixes
    if alias in _KINSHIP_TERMS:
        return 0
    if "的" in alias:
        return 0
    if n >= 3:
        tail2 = alias[-2:]
        if tail2 in {"他妈", "她妈", "他爸", "她爸", "他姐", "她姐",
                      "他哥", "她哥", "他弟", "她弟", "他奶", "她奶",
                      "妈妈", "爸爸", "哥哥", "弟弟", "姐姐", "妹妹",
                      "夫妇", "两口", "老婆", "师父", "师傅"}:
            return 0

    # Level 0: generic person references — these are the #1 cause of false bridges
    if alias in _GENERIC_PERSON_ALIASES:
        return 0

    # Level 0: pure title/rank words
    if alias in _TITLE_PREFIXES:
        return 0

    # Level 0: structural patterns — address/role terms that bridge unrelated people
    # Pattern: 那+role (那贼, 那厮, 那汉) — deictics
    if n >= 2 and alias[0] in "那这" and n <= 4:
        return 0
    # Pattern: 老/小+role (老兄, 小弟, 老爷, 小人) when in generic sets
    if n == 2 and alias[0] in "老小" and alias[1] in "兄弟爷娘人的儿":
        return 0

    # Level 1: suspicious — overly long, collectives, numeric prefixes
    if n > 8:
        return 1
    # Collective markers — "众猴", "群妖", "孩儿们", "小的们"
    if alias[0] in "众群各" or alias.endswith("们"):
        return 1
    # Single-char aliases — too ambiguous to be reliable
    if n == 1:
        return 1
    # Numeric prefix — "两个仙女", "七八十渔人", "三个人" — not person names
    # Only block when 2nd char is a measure word or another digit, confirming
    # quantity phrase. Legitimate names like "二愣子", "一灯大师" pass through.
    _NUM_CHARS = "一二三四五六七八九十两百千万几数"
    _MEASURE_WORDS = "个位名条只头群队批把道尊座对双副件匹株棵颗朵阵帮伙"
    if alias[0] in _NUM_CHARS and n >= 3:
        # "百/千/万/几/数" almost never start legitimate names → always block
        if alias[0] in "百千万几数":
            return 1
        # For 一-十/两: block only if followed by measure word or another digit
        if alias[1] in _MEASURE_WORDS or alias[1] in _NUM_CHARS:
            return 1

    # Level 2: safe
    return 2


def _is_unsafe_alias(alias: str) -> bool:
    """Check if an alias is unsafe to use as a Union-Find key.

    Backward-compatible wrapper around _alias_safety_level().
    """
    return _alias_safety_level(alias) < 2


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

    def _safe_union(name: str, alias: str, source: str) -> None:
        """Union name and alias with conflict detection.

        If both name and alias already belong to well-formed groups
        (size >= 3), they're likely independent characters with their
        own alias networks — skip the union to prevent false merges.

        Threshold is 3 because a group of size 1-2 is barely established
        and could reasonably be absorbed into another group.
        """
        if alias not in uf.parent:
            uf.union(name, alias)
            return
        alias_root = uf.find(alias)
        name_root = uf.find(name)
        if alias_root == name_root:
            return  # already in same group

        alias_size = uf.group_size(alias)
        name_size = uf.group_size(name)
        if alias_size >= 3 and name_size >= 3:
            logger.debug(
                "Group conflict (%s): '%s' (group=%d) vs '%s' (group=%d), "
                "skip union — both groups established",
                source, alias, alias_size, name, name_size,
            )
            return
        uf.union(name, alias)

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

        # If name is a generic/contextual term (妖精, 那怪, etc.):
        # Skip entirely — don't register it or its aliases.
        name_unsafe = _is_unsafe_alias(name)
        if name_unsafe:
            logger.debug("Dict entry unsafe name (skipped): %s", name)
            continue

        freq[name] = max(freq.get(name, 0), frequency)
        uf.find(name)  # ensure registered

        for alias in aliases:
            if alias and alias != name:
                level = _alias_safety_level(alias)
                if level < 2:
                    logger.debug("Alias blocked (L%d) from dict: %s → %s", level, name, alias)
                    continue
                freq[alias] = max(freq.get(alias, 0), 0)
                _safe_union(name, alias, "dict")

    # ── Ingest chapter_facts new_aliases ──
    for row in fact_rows:
        data = json.loads(row["fact_json"])
        for char in data.get("characters", []):
            name = char.get("name", "")
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

            for alias in char.get("new_aliases", []):
                if alias and alias != name:
                    level = _alias_safety_level(alias)
                    if level < 2:
                        logger.debug("Alias blocked (L%d) from fact: %s → %s", level, name, alias)
                        continue
                    freq.setdefault(alias, 0)
                    _safe_union(name, alias, "fact")

    return _groups_to_map(uf, freq)


def _pick_canonical(members: list[str], freq: dict[str, int]) -> str:
    """Pick the best canonical name from an alias group.

    Strategy: among candidates with frequency >= 50% of the max, prefer names
    that look like proper Chinese names (2-4 chars, not starting with common
    nicknames/titles). This avoids picking abbreviated forms like "智深" over
    the full "鲁智深".
    """
    max_freq = max((freq.get(m, 0) for m in members), default=0)
    if max_freq == 0:
        return min(members, key=len)
    threshold = max_freq * 0.5
    candidates = [m for m in members if freq.get(m, 0) >= threshold]
    if not candidates:
        candidates = members

    def _name_quality(m: str) -> tuple:
        """Lower is better. Prefer 2-4 char names with high frequency."""
        n = len(m)
        # Ideal name length is 2-3 chars; 4 is ok, beyond that penalize
        if 2 <= n <= 3:
            len_score = 0
        elif n == 4:
            len_score = 1
        elif n == 1:
            len_score = 3  # Single char is too short to be canonical
        else:
            len_score = 2
        return (len_score, -freq.get(m, 0))

    return min(candidates, key=_name_quality)


def _groups_to_map(uf: _UnionFind, freq: dict[str, int]) -> dict[str, str]:
    """Convert Union-Find groups into alias -> canonical mapping."""
    alias_map: dict[str, str] = {}

    for _root, members in uf.groups().items():
        if len(members) <= 1:
            continue
        canonical = _pick_canonical(members, freq)
        for member in members:
            if member != canonical:
                alias_map[member] = canonical

    return alias_map
