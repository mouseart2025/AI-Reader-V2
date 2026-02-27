"""Lightweight post-validation and cleaning for ChapterFact.

Location filtering uses a 3-layer approach based on Chinese place name morphology
(专名 + 通名 structure). See _bmad-output/spatial-entity-quality-research.md.
"""

import logging

from src.utils.location_names import is_homonym_prone

from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    ItemEventFact,
    OrgEventFact,
    SpatialRelationship,
    WorldDeclaration,
)

logger = logging.getLogger(__name__)

_VALID_ITEM_ACTIONS = {"出现", "获得", "使用", "赠予", "消耗", "丢失", "损毁"}
_VALID_ORG_ACTIONS = {"加入", "离开", "晋升", "阵亡", "叛出", "逐出"}
_VALID_EVENT_TYPES = {"战斗", "成长", "社交", "旅行", "其他"}
_VALID_IMPORTANCE = {"high", "medium", "low"}
_VALID_SPATIAL_RELATION_TYPES = {
    "direction", "distance", "contains", "adjacent", "separated_by", "terrain",
    "in_between",
}
_VALID_CONFIDENCE = {"high", "medium", "low"}

_NAME_MIN_LEN = 1       # persons: keep single-char (handled by aggregator)
_NAME_MIN_LEN_OTHER = 2  # items, concepts, orgs: require ≥2 chars
_NAME_MAX_LEN = 20

# ── Location morphological validation ─────────────────────────────────
# Chinese place names follow 专名(specific) + 通名(generic suffix) pattern.
# E.g., 花果山 = 花果(specific) + 山(generic). Without a specific part, it's not a name.

# Generic suffix characters (通名) — types of geographic features
_GEO_GENERIC_SUFFIXES = frozenset(
    "山峰岭崖谷坡"  # mountain
    "河江湖海溪泉潭洋"  # water
    "林森丛"  # forest
    "城楼殿宫庙寺塔洞关门桥台阁堂院府庄园"  # built structures
    "村镇县省国邦州"  # administrative
    "界域洲宗派教"  # fantasy
    "原地坪滩沙漠岛"  # terrain
    "路街道"  # roads
    "屋房舍"  # buildings
)

# Positional suffixes — when appended to a generic word, form relative positions
_POSITIONAL_SUFFIXES = frozenset(
    "上下里内外中前后边旁畔口头脚顶"
)

# Generic modifiers — adjectives/demonstratives that don't form a specific name
_GENERIC_MODIFIERS = frozenset({
    "小", "大", "老", "新", "旧", "那", "这", "某", "一个", "一座", "一片",
    "一条", "一处", "那个", "这个", "那座", "这座",
    "某条", "某个", "某座", "某处", "某片",
})

# Abstract/conceptual spatial terms — never physical locations
_CONCEPTUAL_GEO_WORDS = frozenset({
    "江湖", "天下", "世界", "人间", "凡间", "尘世", "世间",
    "世俗界", "修仙界", "仙界", "魔界",
    # 抽象地理概念 — LLM 从中文训练数据幻觉出
    "地球", "全球", "全世界",
    "中国大陆", "中国", "大陆",
    "外国", "国外", "海外", "世界各地",
})

# Vehicle/object words that are not locations
_VEHICLE_WORDS = frozenset({
    "小舟", "大船", "船只", "马车", "轿子", "飞剑", "法宝",
    "车厢", "船舱", "轿内",
})

# Furniture / object names — these are never locations
_FURNITURE_OBJECT_NAMES = frozenset({
    # 家具
    "炕", "炕上", "炕桌", "板床", "板床上", "榻上", "床上",
    "桌上", "桌下", "书桌", "书案", "案上",
    "椅上", "凳上", "柜中", "柜内", "箱中", "箱内",
    "抽屉", "抽屉内", "小匣", "匣内",
    # 陈设/器物
    "火盆", "炉内", "灯下", "烛下",
    "屏风", "帘子", "帘内", "帘外",
    "镜壁", "镜前",
    # 建筑微构件
    "门槛", "窗下", "窗前", "窗外",
    "石碣", "碑前",
    "墙角", "墙根",
    "台阶", "阶上",
})

# Generic facility/building names — shared across many chapters, not specific places
_GENERIC_FACILITY_NAMES = frozenset({
    # Lodging
    "酒店", "客店", "客栈", "旅店", "饭店", "酒楼", "酒馆", "酒肆",
    "茶坊", "茶馆", "茶楼", "茶肆", "茶铺",
    # Commerce
    "店铺", "铺子", "当铺", "药铺", "药店", "米铺", "布店",
    "集市", "市场", "市集", "庙会",
    # Government/official
    "衙门", "公堂", "大堂", "牢房", "牢城", "监牢", "死牢",
    "法场", "刑场", "校场",
    # Religious
    "寺庙", "道观", "庵堂", "祠堂",
    # Functional rooms — interior spaces, not named locations
    "后堂", "前厅", "正厅", "大厅", "中堂", "花厅",
    "书房", "卧房", "卧室", "厨房", "柴房", "仓库",
    "内室", "内房", "内堂", "后房", "后院", "前院",
    "偏厅", "偏房", "厢房", "耳房",
    "马厩", "马棚", "草料场",
    # Generic structures
    "山寨", "营寨", "大寨", "寨子",
    "码头", "渡口", "津渡",
    "驿站", "驿馆",
})

# Hardcoded fallback blocklist — catches common cases the rules might miss
_FALLBACK_GEO_BLOCKLIST = frozenset({
    "外面", "里面", "前方", "后方", "旁边", "附近", "远处", "近处",
    "对面", "身边", "身旁", "眼前", "面前", "脚下", "头顶", "上方", "下方",
    "半山腰", "水面", "地面", "天空", "空中",
    "家里", "家中", "家门", "家内",
    "这边", "那边", "这里", "那里", "此地", "此处", "彼处",
    # Relative positions with building parts
    "厅上", "厅前", "厅下", "堂上", "堂前", "堂下",
    "门前", "门外", "门口", "门内", "门下",
    "阶下", "阶前", "廊下", "檐下", "墙外", "墙内",
    "屏风后", "帘后", "帘内",
    "桥头", "桥上", "桥下", "路口", "路上", "路旁", "路边",
    "岸上", "岸边", "水边", "河边", "湖边", "溪边",
    "山上", "山下", "山前", "山后", "山中", "山脚", "山脚下",
    "林中", "林内", "树下", "树林", "草丛",
    "城内", "城外", "城中", "城上", "城下", "城头",
    "村口", "村外", "村中", "村里", "镇上",
    "庄上", "庄前", "庄后", "庄内", "庄外",
    "寨内", "寨外", "寨前", "寨中",
    "店中", "店内", "店外", "店里",
    "房中", "房内", "房里", "屋里", "屋内", "屋中",
    "楼上", "楼下", "楼中",
    "院中", "院内", "院外", "院子",
    "园中", "园内",
    "船上", "船头", "船中",
    "马上", "车上",
    "战场", "阵前", "阵中", "阵后",
    # Route/journey
    "半路", "途中", "路途", "沿途",
    # Descriptive nature compounds
    "深山", "深山老林", "荒山野岭", "穷山恶水",
    "密林深处", "荒野", "旷野", "原野", "野外",
    # On-object
    "树上", "石上", "岩上", "岩石上", "岩石边", "岩石下",
    "石壁", "崖壁", "绝壁",
    # Vague "place" terms
    "偏僻地方", "偏僻之地", "偏僻之处",
    "神秘之处", "神秘地方", "秘密之处", "隐秘之处",
    "安全之处", "安全地方", "隐蔽之处",
})

# ── Person generic references ─────────────────────────────────────────

# Generic person references that should never be extracted as character names
_GENERIC_PERSON_WORDS = frozenset({
    "众人", "其他人", "旁人", "来人", "对方", "大家", "所有人",
    "那人", "此人", "其人", "何人", "某人", "外人", "路人",
    "他们", "她们", "我们", "诸位", "各位", "在场众人",
    # Classical Chinese generics — refer to different people per chapter
    "妇人", "女子", "汉子", "大汉", "壮汉", "好汉",
    "老儿", "老者", "老翁", "少女", "丫头",
    "军士", "军汉", "兵丁", "喽啰", "小喽啰",
    "差人", "差役", "官差", "公差", "衙役",
    "和尚", "僧人", "道士", "先生", "秀才",
    "店家", "店主", "小二", "店小二", "酒保",
    "庄客", "农夫", "猎户", "渔夫", "樵夫",
    "使者", "信使", "探子", "细作",
    "客人", "客官", "过客", "行人",
})

# Pure title words — when used alone (no surname prefix), not a valid character name
_PURE_TITLE_WORDS = frozenset({
    "堂主", "长老", "弟子", "护法", "掌门", "帮主", "教主",
    "师父", "师兄", "师弟", "师姐", "师妹", "师傅",
    "大哥", "二哥", "三哥", "大姐", "二姐",
    "侍卫", "仆人", "丫鬟", "小厮",
    # Official ranks used as address
    "太尉", "知府", "知县", "提辖", "都监", "教头", "都头",
    "将军", "元帅", "丞相", "太师",
    "头领", "寨主", "大王", "员外",
    "恩相", "大人", "老爷", "相公",
})


def _is_generic_location(name: str) -> str | None:
    """Check if a location name is generic/invalid using morphological rules.

    Returns a reason string if the name should be filtered, or None if it should be kept.
    """
    n = len(name)

    # Rule 1: Single-char generic suffix alone (山, 河, 城, ...)
    if n == 1 and name in _GEO_GENERIC_SUFFIXES:
        return "single-char generic suffix"

    # Rule 2: Abstract/conceptual spatial terms
    if name in _CONCEPTUAL_GEO_WORDS:
        return "conceptual geo word"

    # Rule 3: Vehicle/object words
    if name in _VEHICLE_WORDS:
        return "vehicle/object"

    # Rule 17: Furniture / object names — never locations
    if name in _FURNITURE_OBJECT_NAMES:
        return "furniture/object"

    # Rule 4: Generic facility/building names (酒店, 客店, 后堂, 书房, ...)
    if name in _GENERIC_FACILITY_NAMES:
        return "generic facility name"

    # Rule 4b: Hardcoded fallback blocklist
    if name in _FALLBACK_GEO_BLOCKLIST:
        return "fallback blocklist"

    # Rule 5: Contains 的 → descriptive phrase ("自己的地界", "最高的屋子")
    if "的" in name:
        return "descriptive phrase (contains 的)"

    # Rule 6: Too long → likely a descriptive phrase, not a name
    if n > 7:
        return "too long for a place name"

    # Rule 7: Relative position pattern — [generic word(s)] + [positional suffix]
    # E.g., 山上, 村外, 城中, 门口, 场外, 洞口
    if n >= 2 and name[-1] in _POSITIONAL_SUFFIXES:
        prefix = name[:-1]
        # Check if prefix is purely generic (all chars are generic suffixes or common words)
        if all(c in _GEO_GENERIC_SUFFIXES or c in "场水地天石岩土沙草木树竹" for c in prefix):
            return f"relative position ({prefix}+{name[-1]})"

    # Rule 8: Generic modifier + generic suffix — no specific name part
    # E.g., 小城, 大山, 一个村子, 小路, 石屋
    if n >= 2:
        for mod in _GENERIC_MODIFIERS:
            if name.startswith(mod):
                rest = name[len(mod):]
                # Rest is purely generic chars (or generic + 子/儿 diminutive)
                rest_clean = rest.rstrip("子儿")
                if rest_clean and all(c in _GEO_GENERIC_SUFFIXES for c in rest_clean):
                    return f"generic modifier + suffix ({mod}+{rest})"
                break  # Only check first matching modifier

    # Rule 9: 2-char with both chars being generic — e.g., 村落, 山林, 水面
    # These lack a specific name part. BUT exclude X+州/城/镇/县/国 combos
    # because they are often real place names (江州, 海州, 青州, 沧州, etc.)
    if n == 2:
        # Don't filter X+administrative_suffix — these are typically real place names
        if name[1] not in "州城镇县国省郡府":
            if name[0] in _GEO_GENERIC_SUFFIXES | frozenset("水天地场石土半荒深远近") and name[1] in _GEO_GENERIC_SUFFIXES | frozenset("面子落处口边旁"):
                return "two-char generic compound"

    # Rule 10: Starts with demonstrative/direction + 边/里/面/处
    # E.g., "七玄门这边" would be caught if LLM extracts it
    if n >= 3 and name[-1] in "边里面处" and name[-2] in "这那":
        return "demonstrative + directional"

    # Rule 11: Ends with 家里/家中/那里/这里 — person + location suffix
    # E.g., "王婆家里", "武大家中", "林冲那里"
    for suf in ("家里", "家中", "那里", "这里", "府上", "住处", "门前", "屋里"):
        if n > len(suf) and name.endswith(suf):
            return f"person + location suffix ({suf})"

    # Rule 12: Single char that is a building part (not geo feature)
    # 厅/堂/楼/阁/殿 alone are not specific place names
    if n == 1 and name in "厅堂楼阁殿亭阶廊柜":
        return "single-char building part"

    # Rule 13: 2-char ending with 里/中/内/外/上/下 where first char is a facility word
    # E.g., 店里, 牢中, 庙内, 帐中
    if n == 2 and name[1] in "里中内外上下" and name[0] in "店牢庙帐棚洞窑库坑井":
        return "facility + positional"

    # Rule 14: Compound positional phrase — generic area/structure + 里/中/内/外/上/下
    # E.g., 后花园中, 冈子下, 前门外, 书案边, 草堂上
    # Pattern: 3-4 char name ending with positional suffix where the base is a generic term
    if n >= 3 and name[-1] in "里中内外上下前后边旁处":
        base = name[:-1]
        _GENERIC_BASES = frozenset({
            "后花园", "前花园", "后院子", "前院子", "大门", "后门", "前门", "侧门",
            "冈子", "山坡", "岭上", "坡下", "崖下", "岸边", "河畔",
            "书案", "桌案", "床头", "窗前", "屏风", "帐帘", "阶梯",
            "墙角", "墙根", "门槛", "门洞", "门扇", "院墙",
        })
        if base in _GENERIC_BASES:
            return f"compound positional ({base}+{name[-1]})"

    # Rule 15: Quantifier prefix + descriptive filler + generic suffix
    # E.g., "某条偏僻小路", "一个破旧山洞", "一片荒凉之地"
    _QUANT_PREFIXES = (
        "某条", "某个", "某座", "某片", "某处",
        "一条", "一个", "一座", "一片", "一处",
    )
    _GENERIC_TRAIL = (
        "洞穴", "通道", "山洞", "小路", "大路", "小道", "大道",
        "山路", "水路", "地方", "之地", "之处", "峡谷", "山谷",
        "路", "道", "洞", "房", "殿", "厅", "屋", "廊",
    )
    if n >= 3:
        for prefix in _QUANT_PREFIXES:
            if name.startswith(prefix):
                for trail in _GENERIC_TRAIL:
                    if name.endswith(trail) and len(name) <= len(prefix) + 4 + len(trail):
                        return f"quantifier + filler + generic ({prefix}...{trail})"
                break  # Only check first matching prefix

    # Rule 16: Descriptive adjective + generic location tail
    # E.g., "偏僻地方", "荒凉之地", "隐秘角落", "广阔地带"
    _DESCRIPTIVE_ADJECTIVES = frozenset({
        "偏僻", "荒凉", "偏远", "僻静", "幽静", "隐秘", "神秘",
        "安静", "清幽", "阴暗", "黑暗", "宽敞", "狭窄",
        "破旧", "简陋", "豪华", "广阔",
    })
    _GENERIC_TAILS = frozenset({
        "地方", "之地", "之处", "角落", "所在", "地带", "地界",
    })
    for adj in _DESCRIPTIVE_ADJECTIVES:
        if name.startswith(adj):
            tail = name[len(adj):]
            if tail in _GENERIC_TAILS:
                return f"descriptive adj + generic tail ({adj}+{tail})"
            break

    # Rule 18: "角色名 + 房间后缀" patterns (宝玉屋内, 贾母房中, 紫鹃房里)
    # These are character rooms, too specific / ephemeral to be useful locations.
    _ROOM_ENDINGS = ("屋内", "屋里", "屋中", "房内", "房里", "房中", "室中", "室内", "室里")
    if n >= 4 and any(name.endswith(e) for e in _ROOM_ENDINGS):
        return "character room suffix"

    return None


_SUFFIX_TO_TYPE: list[tuple[str, str]] = [
    # Longer suffixes first to avoid partial matches
    ("大陆", "大陆"), ("山脉", "山脉"), ("山谷", "山谷"),
    ("王国", "王国"), ("帝国", "帝国"),
    # Single-char suffixes
    ("国", "国"), ("省", "省"), ("州", "州"), ("府", "府"),
    ("城", "城市"), ("镇", "城镇"), ("县", "县"),
    ("村", "村庄"), ("庄", "庄园"),
    ("山", "山"), ("岭", "山岭"), ("峰", "山峰"),
    ("谷", "山谷"), ("崖", "山崖"),
    ("河", "河流"), ("江", "江"), ("湖", "湖泊"),
    ("海", "海"), ("溪", "溪流"), ("泉", "泉"),
    ("洲", "大洲"), ("界", "界域"), ("域", "域"),
    ("洞", "洞府"), ("殿", "宫殿"), ("宫", "宫殿"),
    ("阁", "阁楼"), ("楼", "楼阁"), ("塔", "塔"),
    ("寺", "寺庙"), ("庙", "寺庙"), ("观", "道观"),
    ("岛", "岛屿"), ("关", "关隘"),
    ("门", "门派"), ("宗", "宗门"), ("派", "门派"),
    ("林", "林地"), ("原", "平原"), ("漠", "沙漠"),
    ("园", "园林"), ("轩", "建筑"), ("斋", "建筑"),
]


def _infer_type_from_name(name: str) -> str:
    """Infer location type from Chinese name suffix.

    Used when auto-creating LocationFact entries for referenced parents/regions
    that lack explicit type information. Falls back to "区域" if no suffix matches.
    """
    for suffix, type_label in _SUFFIX_TO_TYPE:
        if name.endswith(suffix) and len(name) > len(suffix):
            return type_label
    return "区域"


def _is_generic_person(name: str) -> str | None:
    """Check if a person name is generic/invalid.

    Returns a reason string if filtered, or None if kept.
    """
    if name in _GENERIC_PERSON_WORDS:
        return "generic person reference"

    # Pure title without surname: "堂主", "长老" alone (not "岳堂主", "张长老")
    if name in _PURE_TITLE_WORDS:
        return "pure title without surname"

    return None


def _clamp_name(name: str) -> str:
    """Clean and truncate location name."""
    name = name.strip()
    # Split on Chinese/English list separators, take first element
    for sep in ("、", "，", "；", ",", ";"):
        if sep in name:
            name = name.split(sep)[0].strip()
            break
    if len(name) > _NAME_MAX_LEN:
        return name[:_NAME_MAX_LEN]
    return name


class FactValidator:
    """Validate and clean a ChapterFact instance."""

    def __init__(self) -> None:
        # name_corrections: short_name → full_name mapping built from
        # entity dictionary.  E.g., {"愣子": "二愣子"} when the dictionary
        # contains "二愣子" with a numeric prefix that jieba/LLM truncated.
        self._name_corrections: dict[str, str] = {}

    def set_name_corrections(self, corrections: dict[str, str]) -> None:
        """Set name correction mapping (truncated_name → full_name).

        Built from entity dictionary by AnalysisService at startup.
        Applied during character validation to fix LLM extraction errors
        where numeric-prefix names are truncated (e.g., 愣子 → 二愣子).
        """
        self._name_corrections = corrections

    def validate(self, fact: ChapterFact) -> ChapterFact:
        """Return a cleaned copy of the ChapterFact."""
        characters = self._validate_characters(fact.characters)
        relationships = self._validate_relationships(fact.relationships, characters)
        locations = self._validate_locations(fact.locations, characters)
        spatial_relationships = self._validate_spatial_relationships(
            fact.spatial_relationships, locations
        )
        item_events = self._validate_item_events(fact.item_events)
        org_events = self._validate_org_events(fact.org_events)
        events = self._validate_events(fact.events)
        new_concepts = self._validate_concepts(fact.new_concepts)
        world_declarations = self._validate_world_declarations(fact.world_declarations)

        # Post-processing: ensure referenced parent locations exist as entries
        locations = self._ensure_referenced_locations(locations, world_declarations)

        # Post-processing: remove location names incorrectly placed in characters
        characters = self._remove_locations_from_characters(characters, locations)

        # Post-processing: fill empty event participants/locations from summaries
        events = self._fill_event_participants(characters, events)
        events = self._fill_event_locations(locations, events)

        # Cross-check: ensure event participants exist in characters
        characters = self._ensure_participants_in_characters(characters, events)

        # Cross-check: ensure relationship persons exist in characters
        characters = self._ensure_relation_persons_in_characters(
            characters, relationships
        )

        # Post-processing: disambiguate homonymous location names (N29.3)
        # Renames generic names like "夹道" → "大观园·夹道" when parent is known.
        # Must run after all other validation so parent fields are finalized.
        locations, characters, events, spatial_relationships = (
            self._disambiguate_homonym_locations(
                locations, characters, events, spatial_relationships,
            )
        )

        return ChapterFact(
            chapter_id=fact.chapter_id,
            novel_id=fact.novel_id,
            characters=characters,
            relationships=relationships,
            locations=locations,
            spatial_relationships=spatial_relationships,
            item_events=item_events,
            org_events=org_events,
            events=events,
            new_concepts=new_concepts,
            world_declarations=world_declarations,
        )

    def _validate_characters(
        self, chars: list[CharacterFact]
    ) -> list[CharacterFact]:
        """Remove empty names, deduplicate by name, clamp name length."""
        seen: dict[str, CharacterFact] = {}
        for ch in chars:
            name = _clamp_name(ch.name)
            # Apply name corrections (e.g., 愣子 → 二愣子)
            if name in self._name_corrections:
                corrected = self._name_corrections[name]
                logger.debug(
                    "Name correction: '%s' → '%s'", name, corrected,
                )
                name = corrected
            if len(name) < _NAME_MIN_LEN:
                continue
            # Drop generic person references and pure titles
            reason = _is_generic_person(name)
            if reason:
                logger.debug("Dropping person '%s': %s", name, reason)
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

        # ── Alias-based character merge ──
        # When character A explicitly lists character B as an alias and B
        # exists as a separate character, merge B into A. This handles cases
        # like 韩立/二愣子 where the LLM identifies them as the same person
        # but also extracts both names as separate character entries.
        merge_targets: dict[str, str] = {}  # name_to_remove -> name_to_keep
        for name, ch in seen.items():
            for alias in ch.new_aliases:
                if alias in seen and alias != name:
                    if alias not in merge_targets and name not in merge_targets:
                        merge_targets[alias] = name

        for target, keeper in merge_targets.items():
            if target not in seen or keeper not in seen:
                continue
            target_ch = seen.pop(target)
            keeper_ch = seen[keeper]
            merged_aliases = list(dict.fromkeys(
                keeper_ch.new_aliases + target_ch.new_aliases + [target]
            ))
            merged_aliases = [a for a in merged_aliases if a != keeper]
            merged_locations = list(dict.fromkeys(
                keeper_ch.locations_in_chapter + target_ch.locations_in_chapter
            ))
            merged_abilities = keeper_ch.abilities_gained + target_ch.abilities_gained
            seen[keeper] = CharacterFact(
                name=keeper,
                new_aliases=merged_aliases,
                appearance=keeper_ch.appearance or target_ch.appearance,
                abilities_gained=merged_abilities,
                locations_in_chapter=merged_locations,
            )
            logger.debug(
                "Merged character '%s' into '%s' via explicit alias link",
                target, keeper,
            )

        # Second pass: clean new_aliases against the full character set
        # This catches LLM errors where one character's name is wrongly
        # listed as another character's alias (e.g., 李俊 in 李逵's aliases)
        all_names = set(seen.keys())
        for name, ch in seen.items():
            cleaned = self._clean_aliases(ch.new_aliases, name, all_names)
            if len(cleaned) != len(ch.new_aliases):
                seen[name] = ch.model_copy(update={"new_aliases": cleaned})

        return list(seen.values())

    def _clean_aliases(
        self,
        aliases: list[str],
        owner_name: str,
        all_char_names: set[str],
    ) -> list[str]:
        """Clean new_aliases by removing three classes of erroneous aliases.

        1. Alias is another independent character in this chapter
        2. Alias is too long (>6 chars) — likely a descriptive phrase
        3. Alias contains another character's full name (e.g., "水军头领李俊")
        """
        cleaned = []
        for alias in aliases:
            if not alias:
                continue
            # Rule 1: alias is itself an independent character in this chapter
            if alias in all_char_names and alias != owner_name:
                logger.debug(
                    "Alias conflict: '%s' is independent char, removing from %s",
                    alias, owner_name,
                )
                continue
            # Rule 2: alias too long — descriptive phrases, not names
            if len(alias) > 6:
                logger.debug(
                    "Alias too long (%d): '%s' for %s",
                    len(alias), alias, owner_name,
                )
                continue
            # Rule 3: alias contains another character's full name
            contaminated = False
            for other in all_char_names:
                if (
                    other != owner_name
                    and len(other) >= 2
                    and other in alias
                    and alias != other
                ):
                    logger.debug(
                        "Alias contains other char: '%s' contains '%s', removing from %s",
                        alias, other, owner_name,
                    )
                    contaminated = True
                    break
            if contaminated:
                continue
            cleaned.append(alias)
        return cleaned

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

    def _validate_locations(self, locs, characters=None):
        """Validate locations using morphological rules + hallucination detection.

        Uses _is_generic_location() for structural pattern matching (replaces
        hardcoded blocklists) and character-name + suffix detection for hallucinations.
        """
        # Build character name set for hallucination detection
        char_names: set[str] = set()
        if characters:
            for ch in characters:
                char_names.add(ch.name)
                char_names.update(ch.new_aliases)

        # Common hallucinated suffix patterns (e.g., "贾政府邸", "韩立住所")
        _HALLUCINATED_SUFFIXES = ("府邸", "住所", "居所", "家中", "宅邸", "房间")

        valid = []
        seen_names: set[str] = set()
        for loc in locs:
            name = _clamp_name(loc.name)
            if len(name) < _NAME_MIN_LEN_OTHER:
                continue
            # Deduplicate locations
            if name in seen_names:
                continue
            seen_names.add(name)
            # Morphological validation (replaces blocklist approach)
            reason = _is_generic_location(name)
            if reason:
                logger.debug("Dropping location '%s': %s", name, reason)
                continue
            # Drop hallucinated "character_name + suffix" locations
            if char_names:
                is_hallucinated = False
                for suffix in _HALLUCINATED_SUFFIXES:
                    if name.endswith(suffix):
                        prefix = name[: -len(suffix)]
                        if prefix in char_names:
                            logger.debug(
                                "Dropping hallucinated location: %s (char=%s + suffix=%s)",
                                name, prefix, suffix,
                            )
                            is_hallucinated = True
                            break
                if is_hallucinated:
                    continue
            valid.append(loc.model_copy(update={"name": name}))

        # Validate peers field
        cleaned_valid = []
        for loc in valid:
            if loc.peers:
                valid_peers = [
                    p for p in loc.peers
                    if p and p != loc.name and not _is_generic_location(p)
                ]
                cleaned_valid.append(
                    loc.model_copy(update={"peers": valid_peers if valid_peers else None})
                )
            else:
                cleaned_valid.append(loc)

        return cleaned_valid

    def _validate_spatial_relationships(
        self, rels: list[SpatialRelationship], locations: list
    ) -> list[SpatialRelationship]:
        """Validate spatial relationships: check types, dedup, and ensure source/target exist."""
        loc_names = {loc.name for loc in locations}
        valid = []
        seen: set[tuple[str, str, str]] = set()
        for rel in rels:
            source = _clamp_name(rel.source)
            target = _clamp_name(rel.target)
            if len(source) < _NAME_MIN_LEN or len(target) < _NAME_MIN_LEN:
                continue
            if source == target:
                continue
            relation_type = rel.relation_type
            if relation_type not in _VALID_SPATIAL_RELATION_TYPES:
                logger.debug(
                    "Dropping spatial rel with invalid type: %s", relation_type
                )
                continue
            confidence = rel.confidence if rel.confidence in _VALID_CONFIDENCE else "medium"
            # Deduplicate by (source, target, relation_type)
            key = (source, target, relation_type)
            if key in seen:
                continue
            seen.add(key)
            # Warn but don't drop if source/target not in extracted locations
            # (they may reference locations from other chapters)
            if source not in loc_names and target not in loc_names:
                logger.debug(
                    "Spatial rel %s->%s: neither in current chapter locations",
                    source, target,
                )
            evidence = rel.narrative_evidence[:50] if rel.narrative_evidence else ""
            valid.append(SpatialRelationship(
                source=source,
                target=target,
                relation_type=relation_type,
                value=rel.value,
                confidence=confidence,
                narrative_evidence=evidence,
            ))
        return valid

    def _validate_item_events(
        self, items: list[ItemEventFact]
    ) -> list[ItemEventFact]:
        valid = []
        for item in items:
            name = _clamp_name(item.item_name)
            if len(name) < _NAME_MIN_LEN_OTHER:
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
            if len(name) < _NAME_MIN_LEN_OTHER:
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
        seen_summaries: set[str] = set()
        for ev in events:
            if not ev.summary or not ev.summary.strip():
                continue
            # Deduplicate by summary text
            summary_key = ev.summary.strip()
            if summary_key in seen_summaries:
                logger.debug("Dropping duplicate event: %s", summary_key[:50])
                continue
            seen_summaries.add(summary_key)

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
            if len(name) < _NAME_MIN_LEN_OTHER:
                continue
            valid.append(c.model_copy(update={"name": name}))
        return valid

    def _remove_locations_from_characters(
        self, characters: list[CharacterFact], locations: list
    ) -> list[CharacterFact]:
        """Remove entries from characters that are actually location names."""
        loc_names = {loc.name for loc in locations}
        if not loc_names:
            return characters
        cleaned = []
        for ch in characters:
            if ch.name in loc_names:
                logger.debug(
                    "Removing location '%s' from characters list", ch.name
                )
                continue
            cleaned.append(ch)
        return cleaned

    def _fill_event_participants(
        self, characters: list[CharacterFact], events: list[EventFact]
    ) -> list[EventFact]:
        """Fill empty event participants by scanning summary for character names."""
        # Build name set: all character names + aliases
        all_names: set[str] = set()
        for ch in characters:
            all_names.add(ch.name)
            all_names.update(ch.new_aliases)

        # Sort by length descending to match longer names first
        sorted_names = sorted(all_names, key=len, reverse=True)

        updated = []
        for ev in events:
            if not ev.participants:
                # Scan summary for character names
                found = []
                for name in sorted_names:
                    if name in ev.summary and name not in found:
                        found.append(name)
                if found:
                    ev = ev.model_copy(update={"participants": found})
            updated.append(ev)
        return updated

    def _fill_event_locations(
        self, locations: list, events: list[EventFact]
    ) -> list[EventFact]:
        """Fill empty event locations by scanning summary for location names."""
        loc_names = sorted(
            [loc.name for loc in locations], key=len, reverse=True
        )

        updated = []
        for ev in events:
            if not ev.location and loc_names:
                for loc_name in loc_names:
                    if loc_name in ev.summary:
                        ev = ev.model_copy(update={"location": loc_name})
                        break
            updated.append(ev)
        return updated

    def _ensure_participants_in_characters(
        self, characters: list[CharacterFact], events: list[EventFact]
    ) -> list[CharacterFact]:
        """Add missing event participants as character entries."""
        char_names = {ch.name for ch in characters}
        # Also check aliases
        for ch in characters:
            char_names.update(ch.new_aliases)

        for ev in events:
            for p in ev.participants:
                p = p.strip()
                if p and p not in char_names and len(p) >= _NAME_MIN_LEN and not _is_generic_person(p):
                    characters.append(CharacterFact(name=p))
                    char_names.add(p)
                    logger.debug("Auto-added character from event participant: %s", p)
        return characters

    def _ensure_relation_persons_in_characters(
        self, characters: list[CharacterFact], relationships
    ) -> list[CharacterFact]:
        """Add missing relationship persons as character entries."""
        char_names = {ch.name for ch in characters}
        for ch in characters:
            char_names.update(ch.new_aliases)

        for rel in relationships:
            for name in (rel.person_a, rel.person_b):
                name = name.strip()
                if name and name not in char_names and len(name) >= _NAME_MIN_LEN and not _is_generic_person(name):
                    characters.append(CharacterFact(name=name))
                    char_names.add(name)
                    logger.debug("Auto-added character from relationship: %s", name)
        return characters

    def _ensure_referenced_locations(
        self,
        locations: list,
        world_declarations: list[WorldDeclaration],
    ) -> list:
        """Auto-create LocationFact entries for parent refs and world_declaration names
        that don't already exist in the locations list.

        This fixes a common LLM extraction gap: the model references locations like
        东胜神洲 as a parent field or in region_division children, but doesn't create
        standalone location entries for them.
        """
        from src.models.chapter_fact import LocationFact

        existing_names = {loc.name for loc in locations}
        to_add: dict[str, LocationFact] = {}  # name -> LocationFact

        # 1. Collect parent references from existing locations
        for loc in locations:
            parent = loc.parent
            if parent and parent.strip() and parent not in existing_names and parent not in to_add:
                to_add[parent] = LocationFact(
                    name=parent,
                    type=_infer_type_from_name(parent),
                    description="",
                )
                logger.debug("Auto-adding parent location: %s (referenced by %s)", parent, loc.name)

        # 2. Collect location names from world_declarations
        for decl in world_declarations:
            content = decl.content
            if decl.declaration_type == "region_division":
                # children are region names
                for child in content.get("children", []):
                    child = child.strip()
                    if child and child not in existing_names and child not in to_add:
                        to_add[child] = LocationFact(
                            name=child,
                            type=_infer_type_from_name(child),
                            parent=content.get("parent"),
                            description="",
                        )
                        logger.debug("Auto-adding location from region_division: %s", child)
                # parent of division
                div_parent = content.get("parent", "")
                if div_parent and div_parent.strip():
                    div_parent = div_parent.strip()
                    if div_parent not in existing_names and div_parent not in to_add:
                        to_add[div_parent] = LocationFact(
                            name=div_parent,
                            type=_infer_type_from_name(div_parent),
                            description="",
                        )
                        logger.debug("Auto-adding location from region_division parent: %s", div_parent)
            elif decl.declaration_type == "portal":
                # source_location and target_location
                for key in ("source_location", "target_location"):
                    loc_name = content.get(key, "")
                    if loc_name and loc_name.strip():
                        loc_name = loc_name.strip()
                        if loc_name not in existing_names and loc_name not in to_add:
                            to_add[loc_name] = LocationFact(
                                name=loc_name,
                                type="地点",
                                description="",
                            )
                            logger.debug("Auto-adding location from portal: %s", loc_name)

        if to_add:
            locations = locations + list(to_add.values())
            logger.info(
                "Auto-added %d referenced locations: %s",
                len(to_add),
                ", ".join(to_add.keys()),
            )
        return locations

    def _disambiguate_homonym_locations(
        self,
        locations: list,
        characters: list[CharacterFact],
        events: list[EventFact],
        spatial_relationships: list[SpatialRelationship],
    ) -> tuple[list, list[CharacterFact], list[EventFact], list[SpatialRelationship]]:
        """Disambiguate homonymous location names by adding parent prefix.

        Generic architectural names (夹道, 后门, etc.) that have a parent are
        renamed to "{parent}·{name}" (e.g. "大观园·夹道") to prevent data
        pollution when the same generic name exists in multiple buildings.

        Also updates all cross-references within the same ChapterFact.
        """
        rename_map: dict[str, str] = {}  # old_name -> new_name

        new_locations = []
        for loc in locations:
            if is_homonym_prone(loc.name) and loc.parent:
                new_name = f"{loc.parent}·{loc.name}"
                rename_map[loc.name] = new_name
                new_locations.append(loc.model_copy(update={"name": new_name}))
                logger.debug("Disambiguated location: '%s' → '%s'", loc.name, new_name)
            else:
                new_locations.append(loc)

        if not rename_map:
            return locations, characters, events, spatial_relationships

        logger.info(
            "Disambiguated %d homonym locations: %s",
            len(rename_map),
            ", ".join(f"{k}→{v}" for k, v in rename_map.items()),
        )

        # Sync references: characters[].locations_in_chapter
        new_characters = []
        for ch in characters:
            new_locs = [rename_map.get(loc, loc) for loc in ch.locations_in_chapter]
            if new_locs != list(ch.locations_in_chapter):
                new_characters.append(ch.model_copy(update={"locations_in_chapter": new_locs}))
            else:
                new_characters.append(ch)

        # Sync references: events[].location
        new_events = []
        for ev in events:
            if ev.location and ev.location in rename_map:
                new_events.append(ev.model_copy(update={"location": rename_map[ev.location]}))
            else:
                new_events.append(ev)

        # Sync references: spatial_relationships[].source and .target
        new_spatial = []
        for rel in spatial_relationships:
            updates: dict = {}
            if rel.source in rename_map:
                updates["source"] = rename_map[rel.source]
            if rel.target in rename_map:
                updates["target"] = rename_map[rel.target]
            new_spatial.append(rel.model_copy(update=updates) if updates else rel)

        # Sync references: locations[].parent (rare: parent itself was disambiguated)
        final_locations = []
        for loc in new_locations:
            if loc.parent and loc.parent in rename_map:
                final_locations.append(loc.model_copy(update={"parent": rename_map[loc.parent]}))
            else:
                final_locations.append(loc)

        return final_locations, new_characters, new_events, new_spatial

    def _validate_world_declarations(
        self, declarations: list[WorldDeclaration]
    ) -> list[WorldDeclaration]:
        """Validate world declarations: check types, deduplicate."""
        valid_types = {"region_division", "layer_exists", "portal", "region_position"}
        valid = []
        for decl in declarations:
            if decl.declaration_type not in valid_types:
                logger.debug(
                    "Dropping world declaration with invalid type: %s",
                    decl.declaration_type,
                )
                continue
            if not isinstance(decl.content, dict) or not decl.content:
                continue
            confidence = decl.confidence if decl.confidence in _VALID_CONFIDENCE else "medium"
            evidence = decl.narrative_evidence[:100] if decl.narrative_evidence else ""
            valid.append(WorldDeclaration(
                declaration_type=decl.declaration_type,
                content=decl.content,
                narrative_evidence=evidence,
                confidence=confidence,
            ))
        return valid
