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
    # Royal/imperial kinship — different kings/queens across chapters
    "父王", "母后", "王后", "太后", "皇后", "王母", "太子",
    "王爷", "王妃", "驸马", "公主", "殿下", "陛下",
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
    # Xianxia address terms — used for ANY cultivator, major bridge cause
    "前辈", "晚辈", "小友", "道友", "仙子", "仙师",
    "公子", "少爷", "大爷", "老大",
    "老夫", "妾身", "本人", "在下", "小生", "老奴",
    "仁兄", "兄台", "阁下", "对方",
    "此子", "此女", "此人", "那人",
    "逆徒", "小徒", "弟子", "记名弟子",
    "主人", "夫君", "圣子",
    "师叔", "师侄", "师伯",
    # Pronouns / deictics — can refer to anyone
    "我们", "我等", "他们", "她们", "他", "她",
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
    # Honorific address shared across many characters
    "令尊", "令堂", "令兄", "令弟", "令妹", "令郎", "令爱",
    # Buddhist/Daoist titles — shared across many deities/monks
    "菩萨", "天王", "金星", "真君", "元帅", "星君", "星官",
    "罗汉", "尊者", "法师", "禅师", "国师",
    # More generic terms found in 水浒传 analysis
    "童子", "道童", "仙童", "仙女", "渔人",
    "囚徒", "罪犯", "犯人", "配军",
    "长汉", "黑汉", "黑汉子", "黑厮", "黑杀才",
    # Age-based generics
    "后生", "後生", "少年人", "年轻人", "小后生",
    # More generic role terms
    "节级", "都头", "提辖", "制使", "管营", "知寨",
    # Vague descriptive references — used for different characters per chapter
    "掌柜", "店家", "店主",
    "煞星", "神医",
    "小子", "毛头小子", "黄毛小子", "小兄弟",
    "乡巴佬", "土包子", "土小子",
    "傀儡", "巨猿", "骷髅", "鬼头",
})

_TITLE_PREFIXES = frozenset({
    "堂主", "长老", "弟子", "护法", "掌门", "帮主", "教主",
    "师父", "师兄", "师弟", "师姐", "师妹", "师傅",
    "师叔", "师侄", "师伯", "师叔祖",
    # Official ranks — shared across many characters in classical novels
    "太尉", "知府", "知县", "县令", "提辖", "都监", "团练",
    "总管", "管营", "差拨", "节级", "牢头", "押司",
    "教头", "教师", "都头", "虞候", "制使",
    "将军", "元帅", "统制", "统领", "指挥",
    "丞相", "宰相", "太师", "太保", "枢密",
    "知寨", "巡检", "经略", "经略相公",
    "恩相", "大人", "老爷", "相公",
})


_TITLE_SUFFIXES_2 = frozenset({
    "前辈", "道友", "师兄", "师弟", "师姐", "师妹", "师叔", "师侄",
    "师伯", "仙师", "仙子", "公子", "姑娘", "小姐", "夫人",
    "大人", "老爷", "长老", "掌门", "帮主", "教主", "堂主",
    "将军", "统领", "元帅", "大哥", "老弟", "兄弟", "先生",
    "菩萨", "佛祖", "真君", "星君", "天王", "天尊", "娘娘",
    "天尊", "老祖", "大长老", "世兄", "世侄", "贤弟", "贤侄",
    "施主", "领队",
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
                      "夫妇", "两口", "老婆", "师父", "师傅",
                      "奶奶", "嫂子", "媳妇", "姨妈", "姨娘", "姑妈",
                      "大爷", "二爷", "三爷", "丫头", "姑娘"}:
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
    # Pattern: surname+兄/弟/妹 (陇兄, 曲兄, 蜂兄, 鱼兄) — xianxia courtesy address
    if n == 2 and alias[1] in "兄弟妹姐":
        return 0

    # Level 0: surname + generic title pattern (e.g., "韩前辈", "林道友", "王师兄")
    # These are contextual address forms, not stable aliases.
    if 3 <= n <= 5:
        for suffix in _TITLE_SUFFIXES_2:
            if n > len(suffix) and alias.endswith(suffix):
                return 0

    # Level 1: suspicious — overly long, collectives, numeric prefixes
    _NUM_CHARS = "一二三四五六七八九十两百千万几数"
    _MEASURE_WORDS = "个位名条只头群队批把道尊座对双副件匹株棵颗朵阵帮伙"
    if n > 8:
        return 1
    # Collective markers — "众猴", "群妖", "孩儿们", "小的们", "三阮", "阮氏三雄"
    if alias[0] in "众群各" or alias.endswith("们"):
        return 1
    # Numeric collective — "三X" when referring to groups (三阮, 五虎)
    # But allow known names like "三藏", "八戒", "九叔" — these have non-surname 2nd chars
    # that are clearly name characters, not family surnames used in group refs.
    _COMMON_SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤阮"
    if n == 2 and alias[0] in _NUM_CHARS and alias[0] not in "一":
        if alias[1] in _COMMON_SURNAMES:
            return 1  # "三阮", "二张" etc. — surname-based group ref
    # Collective group references — "X氏三雄", "X家三昆仲", "X氏兄弟"
    if "三雄" in alias or "三昆仲" in alias or "兄弟" in alias[-2:]:
        return 1
    # Single-char aliases — too ambiguous to be reliable
    if n == 1:
        return 1
    # Numeric prefix — "两个仙女", "七八十渔人", "三个人" — not person names
    # Only block when 2nd char is a measure word or another digit, confirming
    # quantity phrase. Legitimate names like "二愣子", "一灯大师" pass through.
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

        Returns True if the names share a prefix but differ in the last character,
        suggesting they are different characters with parallel naming patterns.
        Also detects prefix-suffix relationships (阮小 vs 阮小二).
        """
        la, lb = len(a), len(b)
        # Same length, same prefix, different last char
        # (阮小二 vs 阮小七, 解珍 vs 解宝)
        if la == lb and la >= 2 and a[:-1] == b[:-1] and a[-1] != b[-1]:
            return True
        # Prefix relationship: shorter is strict prefix of longer
        # (阮小 vs 阮小二 — "阮小" is a truncated form)
        short, long = (a, b) if la < lb else (b, a)
        if len(short) >= 2 and long.startswith(short) and len(long) - len(short) == 1:
            return True
        return False

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
        if source != "dict":
            name_primary = _alias_to_dict_primary.get(name)
            alias_primary = _alias_to_dict_primary.get(alias)
            if name_primary and alias_primary and name_primary != alias_primary:
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
    # Only use entries with a real entity_type (skip 'unknown' noise like "行者笑", "者道")
    # First pass: collect all primary entity names for conflict detection.
    # Entity dictionary entries (from pre-scan) override the generic blocklist:
    # if the pre-scan LLM identified "三叔" as a specific person entity, it should
    # be treated as a named character, not a generic kinship term.
    for row in dict_rows:
        entity_type = row["entity_type"] or "unknown"
        if entity_type == "unknown":
            continue
        name = _normalize_char_variants(row["name"])
        level = _alias_safety_level(name)
        if level >= 2:
            dict_primary_names.add(name)
        elif level == 0 and entity_type == "person" and (row["frequency"] or 0) >= 10:
            # Pre-scan identified this as a high-frequency person entity — override
            # the generic blocklist. E.g., "三叔" in 凡人修仙传 is a specific character.
            dict_primary_names.add(name)
            logger.info("Dict override for blocked name '%s' (freq=%d, type=%s)",
                        name, row["frequency"] or 0, entity_type)

    # Second pass: build Union-Find groups
    for row in dict_rows:
        entity_type = row["entity_type"] or "unknown"
        if entity_type == "unknown":
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
                    # Track alias ownership for disambiguation (any length)
                    if name in dict_primary_names:
                        _alias_to_dict_primary.setdefault(alias, name)
                    _safe_union(name, alias, "fact")

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


_CANONICAL_BLOCKLIST = frozenset({
    # Generic pronouns/references — should never be canonical names
    "他", "她", "此人", "对方", "那人", "此子", "本人", "在下", "老夫", "老奴",
    "男子", "女子", "年轻人", "年轻男子", "青年", "青年男子", "中年人",
    # Generic titles — refer to different people in different contexts
    "前辈", "道友", "小友", "阁下", "大人", "主人", "夫君", "师傅", "为师",
    "弟子", "师兄", "师弟", "师姐", "师妹", "晚辈", "小徒",
    "小子", "公子", "少爷", "大爷", "仁兄", "兄台", "大哥", "小兄弟",
    "神医", "仙师", "圣子", "长老", "大长老", "队长", "领队",
    # Generic descriptions — describe appearance, not identity
    "异族人", "外族人", "人族修士", "人族小子", "人族男修",
    "青袍人", "青袍男子", "青袍修士", "青袍青年", "青袍年轻人",
    "蓝衣青年", "黑脸大汉", "青衫人", "青衫青年", "青衫男子", "青衫儒生",
    "青袍化身", "青色人影", "带翅男子", "金色人影", "银色巨鹏",
    "煞星", "穷亲戚", "土包子", "乡巴佬", "毛头小子", "黄毛小子",
    "救命恩人", "分魂", "本体", "化身", "人形",
})

# Surname + title suffixes that form address terms, not actual names.
# E.g., 韩大夫 = "Doctor Han" is a title, not a real name like 韩立.
_TITLE_SUFFIXES = frozenset({
    "大夫", "神医", "仙师", "大人", "长老", "大长老", "前辈", "道友",
    "小友", "师弟", "师兄", "师叔", "师伯", "师侄", "天尊", "老祖",
    "兄弟", "老弟", "公子", "少爷", "施主", "先生", "世侄", "世兄",
    "贤侄", "贤弟", "领队", "大哥", "小子", "某", "小哥", "小贼",
    "小大夫", "兄", "姐", "妹",
})


# Patterns that indicate a nickname (绰号), courtesy name (字), or title — not a real name.
# These get a demotion factor in canonical scoring so real names win.
_NICKNAME_PATTERNS = frozenset({
    # Animal-based nicknames (水浒 style): X虎/X龙/X豹 etc.
    "虎", "龙", "豹", "蛇", "鹰", "马", "猿", "鹏", "凤", "鸠", "雕", "犬", "狼",
})

_NICKNAME_SUFFIXES = frozenset({
    # Descriptive nickname endings
    "子头", "大圣", "太保", "大王", "魔王", "旋风", "面兽",
    "天王", "太岁", "阎罗", "金刚", "罗汉", "菩萨",
    # Courtesy name / style name patterns
    "公明", "学究", "俊义",
    # Religious name forms
    "行者", "头陀", "道人", "和尚", "禅师",
})

_NICKNAME_PREFIXES = frozenset({
    # Descriptive nickname openings (水浒 style)
    "豹子", "黑旋", "没羽", "花和", "没遮", "急先", "玉麒", "小李",
    "九纹", "双鞭", "双枪", "青面", "插翅", "混江", "活阎",
    "小旋", "铁笛", "黑旋", "浪子", "拼命", "神行",
})


def _is_nickname_or_title(name: str) -> bool:
    """Check if a name looks like a nickname (绰号), courtesy name, or title form."""
    n = len(name)
    if n < 2:
        return False
    # Surname + title suffix (韩大夫, 林教头, 宋押司)
    if any(name.endswith(t) for t in _TITLE_SUFFIXES):
        return True
    # Known nickname suffix patterns
    if any(name.endswith(s) for s in _NICKNAME_SUFFIXES):
        return True
    # Known nickname prefix patterns
    if any(name.startswith(p) for p in _NICKNAME_PREFIXES):
        return True
    # X+称谓 pattern for 红楼梦 (宝二爷, 琏二爷, 珠大嫂子)
    if n >= 3 and name[-1] in "爷奶" and name[-2] in "大二三四五六七八九":
        return True
    if n >= 4 and name.endswith(("奶奶", "姑娘", "丫头")):
        return True
    # "老X" / "X老" patterns for honorifics (老祖宗, 老太太)
    if name.startswith("老") and n >= 3 and name not in ("老子",):
        return True
    return False


def _pick_canonical(members: list[str], freq: dict[str, int],
                    dict_primary_names: set[str] | None = None) -> str:
    """Pick the best canonical name from an alias group.

    Strategy (v0.66): frequency-first with nickname/title demotion.
    1. dict_primary single member → return directly
    2. Multiple candidates → frequency × safety_weight, with length tier as tiebreaker
    3. Nicknames, courtesy names, and titles get demoted via weight factor

    This replaces the v0.65 tier-first approach that incorrectly preferred
    3-char names (宋公明) over high-frequency 2-char names (宋江).
    """
    # Priority 1: If a member is a known entity from pre-scan dictionary, prefer it
    if dict_primary_names:
        dict_members = [m for m in members if m in dict_primary_names
                        and m not in _CANONICAL_BLOCKLIST
                        and not _is_nickname_or_title(m)]
        if len(dict_members) == 1:
            return dict_members[0]
        if not dict_members:
            # All dict members are titles/nicknames — try without title filter
            dict_members = [m for m in members if m in dict_primary_names
                            and m not in _CANONICAL_BLOCKLIST]
        if dict_members:
            # Multiple dict names in same group — pick by frequency (not tier)
            members = dict_members

    # Filter out generic terms that should never be canonical
    candidates = [m for m in members if m not in _CANONICAL_BLOCKLIST]
    if not candidates:
        candidates = members

    # Filter out nicknames/titles for cleaner candidate pool
    clean = [m for m in candidates if not _is_nickname_or_title(m) and len(m) >= 2]
    if not clean:
        clean = [m for m in candidates if len(m) >= 2]
    if not clean:
        clean = candidates

    # Strategy: prefer 3-char full names (surname+given) if they have
    # meaningful frequency (≥50), indicating the text actually uses the
    # full name. This handles:
    #   孙悟空(152) > 悟空(374)  — full name used enough → prefer it
    #   陈玄奘(14) < 唐僧(829)  — full name barely used → skip it
    #   猪八戒(182) > 八戒(1700) — full name used enough → prefer it
    _FULL_NAME_MIN_FREQ = 50
    three_char = [m for m in clean if len(m) == 3
                  and freq.get(m, 0) >= _FULL_NAME_MIN_FREQ]
    if len(three_char) == 1:
        return three_char[0]
    if three_char:
        return max(three_char, key=lambda m: freq.get(m, 0))

    # No qualifying 3-char full name — pick highest frequency among clean names
    return max(clean, key=lambda m: freq.get(m, 0))


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
