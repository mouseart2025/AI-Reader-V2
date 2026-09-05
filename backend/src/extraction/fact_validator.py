"""Lightweight post-validation and cleaning for ChapterFact.

Location filtering uses a 3-layer approach based on Chinese place name morphology
(专名 + 通名 structure). See _bmad-output/spatial-entity-quality-research.md.
"""

import logging
from pathlib import Path

from src.utils.location_names import is_homonym_prone

from src.extraction.name_resolver import write_audit_records

from src.models.chapter_fact import (
    ChapterFact,
    CharacterFact,
    EventFact,
    ItemEventFact,
    OrgEventFact,
    RelationshipFact,
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
    "in_between", "travel_path", "relative_scale", "cluster",
}
_VALID_CONFIDENCE = {"high", "medium", "low"}
_VALID_DISTANCE_CLASS = {"near", "medium", "far", "very_far"}

# ── Contains direction fix: suffix-based geographic rank ─────────────
# Smaller number = larger geographic entity. Used to detect inverted contains.
# Reuses the same principle as world_structure_agent._get_suffix_rank().
_CONTAINS_RANK_ORDER = {"world": 0, "continent": 1, "kingdom": 2, "region": 3,
                        "city": 4, "site": 5, "building": 6}

_CONTAINS_SUFFIX_RANK: list[tuple[str, int]] = [
    # 3+ char suffixes
    ("自治区", 1), ("直辖市", 1), ("黑龙江", 1),
    # Sci-fi / astronomy multi-char suffixes
    ("太阳系", 1), ("银河系", 0), ("恒星系", 1), ("星系", 1),
    ("天文台", 5), ("观测站", 5), ("基地", 5), ("控制中心", 5),
    ("加速器", 5), ("发电站", 5),
    # 2+ char suffixes
    ("大陆", 1), ("王国", 2), ("帝国", 2), ("山脉", 3), ("地区", 2),
    ("坊市", 5),   # fantasy marketplace, not municipality
    ("城市", 4), ("城池", 4), ("公社", 4), ("县城", 4),
    ("客栈", 6), ("酒楼", 6), ("酒馆", 6), ("茶馆", 6), ("茶楼", 6),
    ("当铺", 6), ("药铺", 6), ("书院", 6),
    ("祠堂", 6), ("宅院", 6), ("府邸", 6), ("洞府", 6),
    ("牌坊", 5), ("码头", 5), ("渡口", 5), ("胡同", 5),
    # Residential 府 — noble/official residences (NOT administrative prefectures)
    ("王府", 5), ("侯府", 5), ("国府", 5), ("公府", 5),
    ("相府", 5), ("帅府", 5),
    # Province/city exceptions for 江/海/原
    ("上海", 4), ("珠海", 4), ("威海", 4), ("北海", 4),
    ("青海", 1), ("大海", 3),
    ("浙江", 1), ("镇江", 4), ("九江", 4), ("湛江", 4),
    ("丽江", 4), ("阳江", 4), ("内江", 4), ("吴江", 4),
    ("太原", 4),
    # 1-char: continent
    ("省", 1), ("界", 1), ("洲", 1), ("域", 1), ("洋", 1),
    ("海", 3),
    # 1-char: kingdom
    ("国", 2), ("府", 2), ("州", 2), ("道", 2), ("市", 2),
    # 1-char: region
    ("郡", 3), ("县", 3), ("山", 3), ("岭", 3), ("岛", 3), ("谷", 3),
    ("湖", 3), ("河", 3), ("江", 3), ("林", 3), ("原", 3),
    ("峡", 3), ("泊", 3), ("湾", 3), ("境", 3),
    # 1-char: city
    ("城", 4), ("都", 4), ("镇", 4), ("乡", 4), ("京", 4),
    # 1-char: site
    ("村", 5), ("庄", 5), ("寨", 5), ("屯", 5), ("营", 5),
    ("洞", 5), ("窟", 5), ("穴", 5),
    ("峰", 5), ("坡", 5), ("崖", 5), ("岗", 5), ("冈", 5),
    ("泉", 5), ("潭", 5), ("溪", 5), ("沟", 5),
    ("关", 5), ("坊", 5), ("街", 5), ("巷", 5), ("弄", 5),
    ("寺", 5), ("庙", 5), ("观", 5), ("庵", 5), ("祠", 5),
    ("园", 5), ("宫", 5), ("池", 5), ("苑", 5), ("渊", 5),
    ("桥", 5), ("墓", 5), ("陵", 5), ("堡", 5), ("坝", 5), ("哨", 5),
    ("坞", 5), ("径", 5),
    # 1-char: building
    ("殿", 6), ("堂", 6), ("阁", 6), ("楼", 6), ("塔", 6),
    ("亭", 6), ("台", 6), ("房", 6), ("室", 6), ("厅", 6), ("院", 6),
    ("馆", 6), ("铺", 6), ("店", 6), ("家", 6), ("宅", 6), ("舍", 6),
    ("居", 6), ("轩", 6), ("斋", 6), ("棚", 6), ("架", 6),
    ("窗", 6), ("门", 6),
]


def _get_contains_rank(name: str) -> int | None:
    """Get geographic scale rank from Chinese location name suffix.

    Returns rank (0=world, 6=building) or None if no suffix matches.
    Used to fix inverted contains relationships.
    """
    if len(name) < 2:
        return None
    for suffix, rank in _CONTAINS_SUFFIX_RANK:
        if name.endswith(suffix):
            if len(suffix) >= 2 or len(name) > len(suffix):
                return rank
    return None


# ── Location name normalization (variant → canonical) ────────────────
# LLMs sometimes output different character variants for the same place.
# Map all known variants to a single canonical form.
_LOCATION_NAME_NORMALIZE: dict[str, str] = {
    "南瞻部洲": "南赡部洲",
    "南赡养部洲": "南赡部洲",
    "南瞻养部洲": "南赡部洲",
}

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
    # Collective/abstract terms — not specific locations
    "全国各地", "全球各战区", "全国科学大会",
    "地平线", "西方夜空", "云海",
    "同步轨道", "大气层",
})

# Vehicle/object words that are not locations
_VEHICLE_WORDS = frozenset({
    "小舟", "大船", "船只", "马车", "轿子", "飞剑", "法宝",
    "车厢", "船舱", "轿内",
    # Modern vehicles
    "出租车", "出租车内", "警车", "警车内", "救护车", "直升机",
    "飞机", "汽车", "火车", "大巴", "公交车",
    # Sci-fi vehicles/objects
    "飞船", "母舰", "战舰", "穿梭机",

    # auto-improve 2026-03-28 — vehicles
    "翠幄青紬车", "黄吉普车",
})

# v0.72 Phase 1 (Story 1.4): non-vehicle entries formerly lumped into
# _VEHICLE_WORDS, moved to correctly-named lists. Filtering decisions are
# unchanged — _is_generic_location() checks all of these lists.
# Note: 东边/南边/西边/北边/九霄 were dropped here as duplicates of
# _DIRECTIONAL_RELATIVE_PHRASES; 地狱/恶鬼/畜生/阿修罗 moved to _BUDDHIST_CONCEPTS.
_GENERIC_NON_LOCATION_TERMS = frozenset({
    # auto-improve 2026-03-28 — equipment/objects (not locations)
    "抛物面天线", "青石", "青石棋局", "青石岩",
    # auto-improve 2026-03-28 — generic non-locations
    "半空", "夕阳", "天", "人", "区域",
    "无数仙域", "坎宫之地", "孙玉厚家",
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
    # Computer/tech components — not locations
    "主板", "系统总线", "显示阵列", "CPU区", "外存",
    # Generic objects
    "长桌", "大鼎", "凉亭", "岗亭",
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
    # Generic palace/hall names
    "宝殿", "大殿", "正殿", "偏殿", "内殿",
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

    # auto-improve 2026-03-28
    "东阁",
    "二层门下",
    "二门外",
    "廊庑",
    "高台",

    # auto-improve 2026-03-28
    "冰地",
    "峡谷",
    "石柱",

    # auto-improve 2026-03-28
    "宝座",
    "宝阁",
    "讲堂",
    "高阁",

    # auto-improve 2026-03-28
    "影壁",
    "正房台矶",

    # auto-improve 2026-03-28
    "三间厅",
    "廊檐下",

    # auto-improve 2026-03-28
    "食堂饭厅",

    # auto-improve 2026-03-28
    "横梁",

    # auto-improve 2026-03-28
    "密林",
    "雪地",

    # auto-improve 2026-03-28
    "宿舍",
    "教室",

    # v0.70 review 2026-04-08 — 西游记人工审核发现
    # Generic temple/palace rooms
    "方丈", "禅堂", "禅院", "法堂", "经堂",
    "前廊", "后园", "前殿", "朝门", "丹墀",
    "客房", "静室", "库房",
    # Generic natural features
    "高山", "山凹", "松林", "草坡", "山崖",
    "涧边", "半空中", "大路口", "大路", "小河",
    "峻岭", "山坡", "小茅山", "石头山",
    # Abstract / non-location concepts
    "天", "地", "万物", "混沌", "五仙", "五虫",
    "四猴", "周天", "仙道", "人道", "鬼道",
    "天仙", "地仙", "人仙", "羽虫", "鳞虫",
    "昆虫", "毛虫", "蟾宫",
    # Objects / furniture / plants (not places)
    "龙床", "绣墩", "铁笼", "牙床", "油锅",
    "红梅", "翠竹", "天罗地网", "净瓶", "红匣",
    "芭蕉树", "经柜",
    # Building parts
    "正梁", "檐柱", "格子", "龙门", "城墙",
    "城廓", "关厢", "街坊", "坟堆",
    # Misc non-locations
    "孟兰盆会", "四部洲", "三清圣象",
    "九霄云里", "九霄空上", "巅险峰头",
    "摩天高山", "正南方大山", "南北大街",
    "蓼汀", "深衢", "东观", "丹灶", "雷府",

    # v0.71.1 cross-novel audit (2026-04-11)
    # — 西游记 catch-all 垃圾桶父节点 / 描述词误作地点
    "柴扉",  # "柴门" 的雅称,诗意指代贫家,非地名
    "假山",  # 园林装饰物,不是独立地点(红楼大观园 catch-all)
    "山石",  # 园林装饰物(红楼 catch-all)
    "池中",  # 方位描述(红楼 catch-all)
    # 注:"洞府" 在 _FANTASY_LOCATION_WHITELIST 内,fantasy 类型保留
    "洞府门前", "洞府门外",
    "荒野", "荒山",
    "大山", "大洞", "大川",
    "西行路上", "西行道上", "取经路上",  # xiyouji 描述性路径
    "东土", "西土", "南土", "北土",  # 泛称方位,不是具体地点
    # — 描述性"方位+建筑" 短语 (红楼高频)
    "东南角", "西北角", "东北角", "西南角",
    "东南角井边", "西边院子", "东边院子",
    "园子里", "铺子里", "村子里", "庄子里",
    "西边穿堂", "西边穿堂儿",
})

# ---------------------------------------------------------------------------
# Extended directional/relative phrases (Phase 1b, from errata KB)
# 方位词/相对方位词 - 从西游记 errata 收集的 A-方位泛称 类别
# ---------------------------------------------------------------------------
_DIRECTIONAL_RELATIVE_PHRASES = frozenset({
    # 四面八方
    "东方", "西方", "南方", "北方",
    "东南", "东北", "西南", "西北",
    "正东", "正西", "正南", "正北",
    "正南方", "正北方", "正东方", "正西方",
    "东边", "西边", "南边", "北边",
    # 山川相对位置
    "山中", "山下", "山上", "山后", "山前", "山顶", "山脚", "山背后",
    "洞中", "洞内", "洞外", "洞口", "洞前", "洞后",
    "城东", "城西", "城南", "城北", "城中", "城内", "城外",
    # 天地水中
    "天上", "云端", "云中", "云上", "九霄", "九霄云", "九霄空", "霄汉",
    "波中", "水底", "海藏中", "涧底", "崖边",
    # 泛称
    "凡间", "当空", "正中", "正上", "正下",
})

# 佛道概念词 - A-概念非地名
_BUDDHIST_CONCEPTS = frozenset({
    "人道", "仙道", "贵道", "神道", "鬼道", "畜生道",
    "饿鬼道", "地狱道", "天道", "修罗道",
    "六道", "三界",
    # v0.72 Phase 1 (Story 1.4) — moved from _VEHICLE_WORDS (六道轮回概念)
    "地狱", "恶鬼", "畜生", "阿修罗",
    "五仙", "五虫",
})

# 描述性短语后缀 - A-描述性短语
_DESCRIPTIVE_SUFFIXES = ("之处", "所在", "所在处", "之地")

# 人物称号后缀 - A-非地名
_PERSON_TITLE_SUFFIXES = ("大王", "大圣", "大仙", "真人", "娘娘", "天尊", "帝君")

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
    "荒原", "平原", "辽阔的平原",
    "悬崖", "悬崖边", "崖底", "操场",
    # On-object
    "树上", "石上", "岩上", "岩石上", "岩石边", "岩石下",
    "石壁", "崖壁", "绝壁",
    # Vague "place" terms
    "偏僻地方", "偏僻之地", "偏僻之处",
    "神秘之处", "神秘地方", "秘密之处", "隐秘之处",
    "安全之处", "安全地方", "隐蔽之处",
})

# ── Person generic references ─────────────────────────────────────────
# v0.72 Phase 1: person generic lists moved to name_authority.py (single
# source of truth). _is_generic_person() below delegates there.


# Fantasy/xianxia: these conceptual terms are valid world-layer locations
_FANTASY_LOCATION_WHITELIST = frozenset({
    "仙界", "魔界", "妖界", "灵域", "修仙界",
    "洞府", "秘境", "禁地", "结界", "虚空",
    # v0.63.0 Story 4.1: expanded fantasy/wuxia whitelist
    "洞天", "福地", "洞天福地", "灵脉", "仙府", "魔窟",
    "演武场", "藏经阁", "炼丹房", "聚灵阵",
    "天界", "冥界", "鬼界", "神界",
})

# Realistic/urban: generic facility names that should be filtered when standalone
# (no proper-noun prefix). Names with a proper-noun prefix pass through.
_REALISTIC_GENERIC_FACILITIES = frozenset({
    "区政府", "市政府", "派出所", "居委会", "街道办",
    "网吧", "超市", "菜市场", "停车场", "公交站",
    "便利店", "快递站", "洗衣房", "理发店",
})

# Historical: administrative facility names (generic without proper-noun prefix)
_HISTORICAL_GENERIC_FACILITIES = frozenset({
    "府衙", "县衙", "总兵府", "提督府", "巡抚衙门",
    "军营", "校场", "粮仓", "驿站",
})


def _is_generic_location(name: str, genre: str | None = None) -> str | None:
    """Check if a location name is generic/invalid using morphological rules.

    Genre-aware: fantasy allows 仙界/魔界/洞府/秘境 etc.
    Returns a reason string if the name should be filtered, or None if it should be kept.
    """
    n = len(name)

    # Rule 1: Single-char generic suffix alone (山, 河, 城, ...)
    if n == 1 and name in _GEO_GENERIC_SUFFIXES:
        return "single-char generic suffix"

    # Rule 2: Abstract/conceptual spatial terms (genre-aware)
    if name in _CONCEPTUAL_GEO_WORDS:
        # Fantasy whitelist: 仙界/魔界 are valid world layers in xianxia
        if genre in ("fantasy", "wuxia") and name in _FANTASY_LOCATION_WHITELIST:
            return None
        return "conceptual geo word"

    # Rule 3: Vehicle/object words
    if name in _VEHICLE_WORDS:
        return "vehicle/object"

    # Rule 3b: Generic non-location terms (equipment/abstract; Story 1.4)
    if name in _GENERIC_NON_LOCATION_TERMS:
        return "generic non-location term"

    # Rule 17: Furniture / object names — never locations
    if name in _FURNITURE_OBJECT_NAMES:
        return "furniture/object"

    # Rule 4: Generic facility/building names (酒店, 客店, 后堂, 书房, ...)
    if name in _GENERIC_FACILITY_NAMES:
        return "generic facility name"

    # Rule 4b: Hardcoded fallback blocklist
    if name in _FALLBACK_GEO_BLOCKLIST:
        return "fallback blocklist"

    # Rule 4b-bis: Directional/relative phrases (A-方位泛称 from errata KB)
    if name in _DIRECTIONAL_RELATIVE_PHRASES:
        return "directional/relative phrase"

    # Rule 4b-ter: Buddhist/Daoist concepts (A-概念非地名)
    if name in _BUDDHIST_CONCEPTS:
        return "buddhist concept"

    # Rule 4b-quater: Descriptive phrases ending with "处/之处/所在/之地"
    # "三藏被擒处", "王夫人处" 类描述短语
    if n >= 2 and name.endswith("处") and not name.endswith(("出处", "住处", "去处", "归处")):
        # 过滤人名+处 / 动词+处 模式
        # 保留有意义的地方名: 如"张家处"不算, 但"贾母处"算 (描述+人名开头)
        return "descriptive phrase (X+处)"
    if name.endswith(_DESCRIPTIVE_SUFFIXES):
        return "descriptive phrase"

    # Rule 4b-quinque: Person title suffixes (A-非地名 - people not places)
    # "XX大王"/"XX大圣" 等人物称号
    if n >= 3 and name.endswith(_PERSON_TITLE_SUFFIXES):
        # 豁免: 历史君主 (如"秦始皇"类不会进地点提取), 以及明确地点后缀组合
        return "person title, not location"

    # Rule 4c: Genre-specific facility filtering (v0.63.0 Story 4.1)
    # Fantasy/wuxia: allow expanded whitelist (洞天, 福地, 仙府 etc.)
    if genre in ("fantasy", "wuxia") and name in _FANTASY_LOCATION_WHITELIST:
        return None  # Explicitly pass — overrides any later rule
    # Realistic/urban: filter standalone generic facilities (区政府, 网吧 etc.)
    if genre in ("realistic", "urban") and name in _REALISTIC_GENERIC_FACILITIES:
        return "genre:realistic generic facility"
    # Historical: filter standalone admin facilities (府衙, 县衙 etc.)
    if genre == "historical" and name in _HISTORICAL_GENERIC_FACILITIES:
        return "genre:historical generic facility"

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

    # Rule 19: LLM noise — names ending with non-geographic suffixes (花果山届, 某地的时候)
    _NOISE_SUFFIXES = ("届", "届时", "的时候", "的地方", "那里的", "这里的")
    if any(name.endswith(s) for s in _NOISE_SUFFIXES):
        return f"noise suffix ({name[-2:]})"

    # v0.71.1 — Phase B S6: cross-novel structural patterns
    # Rule 20: "X国界/X城池/X城门/X城头/X城外" — descriptive admin suffix variants.
    # These are references like "border/walled city/gate" — never canonical locations.
    # Blocked for n > suffix_len + 1 (must have a real prefix name).
    _VARIANT_SUFFIXES = ("国界", "城池", "城门", "城头", "城外", "城内", "城中")
    for vs in _VARIANT_SUFFIXES:
        if n > len(vs) + 1 and name.endswith(vs):
            return f"administrative variant suffix ({vs})"

    # Rule 21: "X山路/X山顶/X山下/X山脚/X山道/X山口" — mountain-descriptor phrases.
    # Always descriptive ("path up the mountain", "mountain peak" etc.), never
    # a standalone location name.
    _MOUNTAIN_DESC_SUFFIXES = ("山路", "山顶", "山脚", "山下", "山道", "山口", "山腰")
    for ms in _MOUNTAIN_DESC_SUFFIXES:
        if n > len(ms) + 1 and name.endswith(ms):
            return f"mountain descriptor suffix ({ms})"

    # Rule 22: "X + 边/旁/岸/口/头" describing locations near a feature
    # 东南角井边 / 苇坑边 / 大观园山石边 / 沁芳桥头 / 山脚边
    # These are positional references relative to a landmark.
    if n >= 4 and name[-1] in "边旁岸口头":
        # Count how many chars are geographic feature words
        feature_chars = sum(1 for c in name[:-1] if c in _GEO_GENERIC_SUFFIXES)
        # If half or more of the prefix chars are generic geo features, it's descriptive
        if feature_chars >= (n - 1) // 2:
            return f"positional landmark reference ({name[-1]})"

    # Rule 23: "方位 + 名词 + 位置" — 东南角井边 / 西边穿堂儿 / 大观园山石边
    # Pattern: starts with a direction word followed by compound nouns
    _DIRECTION_PREFIXES = ("东南", "西南", "东北", "西北", "正东", "正西", "正南", "正北")
    if n >= 5:
        for dp in _DIRECTION_PREFIXES:
            if name.startswith(dp):
                return f"direction prefix compound ({dp})"

    # Rule 24: "X处/X中屋子里" — nested positional, clearly descriptive
    if n >= 5 and (name.endswith("屋子里") or name.endswith("屋中")
                    or name.endswith("房子里") or name.endswith("院子里")):
        return "nested room phrase"

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


# ── Location type consistency soft-check (issue #70, D2) ─────────────
# LocationFact.type 是 LLM 自由文本,实测出现形态漂移(海洋→"大陆"、街道→"国")。
# 这里把 type 与名字后缀各自映射到「形态族」,两侧都已知且不同族时判定明显
# 矛盾 → 软降级为「区域」+ logger.info(不删条目、不抛错)。任一侧未知 → 不动。
_LOCATION_TYPE_FAMILY: dict[str, str] = {
    # 水域
    "海": "water", "海洋": "water", "洋": "water",
    "江": "water", "河": "water", "河流": "water",
    "湖": "water", "湖泊": "water", "溪": "water", "溪流": "water",
    "泉": "water", "潭": "water", "湾": "water", "泊": "water", "池": "water",
    # 山体
    "山": "mountain", "山脉": "mountain", "山岭": "mountain", "岭": "mountain",
    "山峰": "mountain", "峰": "mountain", "山谷": "mountain", "谷": "mountain",
    "山崖": "mountain", "崖": "mountain",
    # 宏观地理(大洲/世界层)
    "大陆": "macro", "大洲": "macro", "洲": "macro",
    "界": "macro", "界域": "macro", "域": "macro", "世界": "macro",
    # 行政区划
    "国": "admin", "王国": "admin", "帝国": "admin", "国家": "admin",
    "省": "admin", "州": "admin", "郡": "admin", "县": "admin", "地区": "admin",
    # 聚落
    "城市": "settlement", "城": "settlement", "城镇": "settlement",
    "镇": "settlement", "乡": "settlement", "京": "settlement",
    "都": "settlement", "市": "settlement",
    "村庄": "settlement", "村": "settlement", "庄园": "settlement",
    "庄": "settlement", "寨": "settlement",
    "街道": "settlement", "街": "settlement", "巷": "settlement",
    # 建筑/人造场所
    "宫殿": "building", "宫": "building", "殿": "building",
    "阁楼": "building", "阁": "building", "楼阁": "building",
    "楼": "building", "塔": "building",
    "寺庙": "building", "寺": "building", "庙": "building",
    "道观": "building", "观": "building", "庵": "building",
    "建筑": "building", "府邸": "building", "宅邸": "building",
    "园林": "building", "园": "building",
    "洞府": "building", "门派": "building", "宗门": "building",
    "关隘": "building", "桥梁": "building", "桥": "building",
    # 地貌
    "平原": "terrain", "原": "terrain", "沙漠": "terrain", "漠": "terrain",
    "林地": "terrain", "林": "terrain", "森林": "terrain",
    "岛屿": "terrain", "岛": "terrain",
}

# 名字以江/海/原等结尾但实为城市的已知例外(与 _CONTAINS_SUFFIX_RANK 同口径),
# 这些名字不参与形态族判断,避免把"上海(type=城市)"误降级。
_CITY_NAME_EXCEPTIONS = frozenset({
    "上海", "珠海", "威海", "北海", "青海",
    "浙江", "镇江", "九江", "湛江", "丽江", "阳江", "内江", "吴江",
    "太原",
})

# 名字侧补充后缀:_SUFFIX_TO_TYPE 未覆盖但形态明确的常见通名
_EXTRA_SUFFIX_FAMILY: list[tuple[str, str]] = [
    ("街道", "settlement"), ("街", "settlement"),
    ("巷", "settlement"), ("弄", "settlement"),
    ("桥", "building"), ("潭", "water"), ("湾", "water"),
]


def _name_suffix_family(name: str) -> str | None:
    """Infer the morphological family of a location name from its suffix."""
    if name in _CITY_NAME_EXCEPTIONS:
        return None
    inferred = _infer_type_from_name(name)
    # "府" 双义(行政府 vs 宅邸),不作为矛盾判断依据
    if inferred not in ("区域", "府"):
        return _LOCATION_TYPE_FAMILY.get(inferred)
    for suffix, family in _EXTRA_SUFFIX_FAMILY:
        if name.endswith(suffix) and len(name) > len(suffix):
            return family
    return None


def _downgrade_inconsistent_location_type(name: str, loc_type: str) -> str:
    """Soft-downgrade location type to 区域 when it clearly contradicts the
    name's morphological family (issue #70 subtype drift, e.g. 海洋→"大陆").

    Never raises and never drops the entry; returns the original type when
    either side is unknown or both families agree (默认路径行为不变).
    """
    if not loc_type or loc_type == "区域":
        return loc_type
    type_family = _LOCATION_TYPE_FAMILY.get(loc_type)
    if type_family is None:
        return loc_type  # 词表外的 type 不做判断
    name_family = _name_suffix_family(name)
    if name_family is None:
        return loc_type
    if type_family != name_family:
        logger.info(
            "Location type downgrade: '%s' type '%s' → '区域' (suffix family=%s)",
            name, loc_type, name_family,
        )
        return "区域"
    return loc_type


# ── Generic person candidates for disambiguation ─────────────────────
# These are valid unnamed characters that should be disambiguated with
# their chapter's primary setting location, not filtered out.
# E.g., "樵夫" → "灵台方寸山·樵夫" to distinguish from other chapters' 樵夫.
_GENERIC_PERSON_CANDIDATES = frozenset({
    "樵夫", "渔夫", "猎户", "农夫",
    "魔王", "妖王", "大王", "山大王",
    "童子", "仙童", "道童",
    "老者", "老翁", "老丈", "老汉",
    "道人", "道士", "和尚", "老僧", "僧人",
    "店家", "店主", "小二",
    "国王", "王后", "公主", "太子",
    "土地", "山神", "城隍",
    "驿丞", "太守", "知县",
})

# ── Genre-aware person filtering ──────────────────────────────────────




def _is_generic_person(name: str, genre: str | None = None) -> str | None:
    """Check if a person name is generic/invalid.

    Delegates to name_authority.is_generic_person() — single source of truth.
    This thin wrapper is kept for backward compatibility (callers & tests).
    """
    from src.services.name_authority import is_generic_person
    return is_generic_person(name, genre)


# CJK character variant normalization — map uncommon/archaic forms to standard forms.
# Prevents duplicates like "南瞻部洲" vs "南赡部洲" (same place, different writing).
_CHAR_VARIANTS: dict[str, str] = {
    "瞻": "赡",  # 南瞻部洲 → 南赡部洲
    "赊": "赡",  # 南赊部洲 → 南赡部洲 (another variant found in 西游记)
    "倶": "俱",  # 北倶芦洲 → 北俱芦洲
    "峯": "峰",  # 峯 → 峰
    "嶽": "岳",  # 嶽 → 岳
    "裏": "里",  # 裏 → 里
    "崑": "昆",  # 崑仑 → 昆仑
    "崙": "仑",  # 崑崙 → 昆仑
    "餘": "余",  # 餘 → 余
    "滙": "汇",  # 滙 → 汇
}


# 已知专名的精确修正表 (exact-match, 不用字符级替换以避免误伤).
# 源: 西游记 errata 人工标注. 这些名字LLM倾向于选同音字但与原文不符.
_EXACT_NAME_CORRECTIONS: dict[str, str] = {
    "南膳部洲": "南赡部洲",  # 四大部洲之一, 原文赡
    "典膳所": "典赡所",      # 西游记原文用赡
    "南瞻部洲": "南赡部洲",  # LLM另一种变体
    "南赊部洲": "南赡部洲",  # 另一变体
}


def _normalize_char_variants(name: str) -> str:
    """Replace archaic/variant CJK characters with their standard equivalents."""
    # 第一轮: 专名精确修正 (安全, 不会误伤)
    if name in _EXACT_NAME_CORRECTIONS:
        return _EXACT_NAME_CORRECTIONS[name]
    # 第二轮: 字符级替换 (仅真正的archaic chars, 不含多义字如"膳")
    for old, new in _CHAR_VARIANTS.items():
        if old in name:
            name = name.replace(old, new)
    # 第三轮: 替换后再次检查专名表 (处理"南膳部洲"→字符替换→"南赡部洲"重复匹配)
    if name in _EXACT_NAME_CORRECTIONS:
        return _EXACT_NAME_CORRECTIONS[name]
    return name


def get_name_variant_hint(name: str) -> dict | None:
    """Check if a name is a known typo/variant and return hint info.

    Returns dict with keys: canonical, variant_type, note.
    Returns None if name is already canonical.
    """
    # Check exact corrections (typos, LLM variants)
    canonical = _EXACT_NAME_CORRECTIONS.get(name)
    if canonical:
        return {
            "canonical": canonical,
            "variant_type": "typo",
            "note": f"疑似笔误：{name} → {canonical}",
        }
    # Check character-level variants
    normalized = name
    for old, new in _CHAR_VARIANTS.items():
        if old in normalized:
            normalized = normalized.replace(old, new)
    if normalized != name:
        return {
            "canonical": normalized,
            "variant_type": "char_variant",
            "note": f"字形变体：{name} → {normalized}",
        }
    return None


def _clamp_name(name: str) -> str:
    """Clean and truncate location name."""
    name = name.strip()
    # Normalize character variants (瞻→赡, 倶→俱, etc.)
    name = _normalize_char_variants(name)
    # Split on Chinese/English list separators, take first element
    for sep in ("、", "，", "；", ",", ";"):
        if sep in name:
            name = name.split(sep)[0].strip()
            break
    if len(name) > _NAME_MAX_LEN:
        return name[:_NAME_MAX_LEN]
    return name


def _name_in_text(name: str, chapter_text: str) -> bool:
    """人名原文锚定:复用证据锚定的 span 定位口径(归一化空白后子串匹配)。

    "X·樵夫" 类消歧名按 "·" 后基本名再查一次(与幻觉判定层同口径)。
    """
    from src.extraction.chapter_fact_extractor import span_located
    if span_located(name, chapter_text):
        return True
    base = name.split("·")[-1]
    return base != name and span_located(base, chapter_text)


# ── Alias 语境指称软校验 (issue #70, E3 验证层闸) ─────────────────────
# 实测(约会大作战 22 卷)发现系统把「当前语境可指向此人」的临时指称错误
# 升级为「此人的稳定别名」:代词自称(吾/汝等)、假想名(假如她其实叫 X)、
# 外观状态短语(身穿某装备)、泛类身份(某组织成员)、临时指称(某某少年)。
# 命中以下高精确度特征的 alias 不入 new_aliases,记 logger.info +
# name_resolution 审计。原则:宁可漏拦不可误杀,拿不准的一律放行。

# 代词与自称(语料级小词表,精确匹配)
_CONTEXTUAL_ALIAS_PRONOUNS = frozenset({
    "吾", "我", "汝", "尔", "你", "您", "他", "她",
    "吾等", "我等", "汝等", "尔等", "我们", "你们", "他们", "她们",
    "在下", "鄙人", "小可", "某家", "俺", "咱", "咱家", "洒家",
    "老夫", "老朽", "老身", "老奴", "奴家", "妾身", "小女子",
    "贫道", "贫僧", "贫尼", "老衲",
    "本座", "本尊", "本王", "本宫", "本将", "本仙", "本神",
    "朕", "孤", "寡人", "卑职", "微臣", "末将",
    "诸位", "各位", "众位", "大家",
})

# 假想/反事实身份特征词(「假如她其实叫 X」「假装叫 Y」类)
_CONTEXTUAL_ALIAS_HYPOTHETICAL = (
    "假如", "如果", "要是", "假设", "若是", "倘若",
    "假装", "冒充", "假扮",
)

# 外观/状态描述特征词(「身穿某装备」「受伤的 X」类)
_CONTEXTUAL_ALIAS_APPEARANCE = (
    "身穿", "身着", "身披", "头戴", "手持", "手执", "手拿", "手握",
    "腰佩", "背负", "受伤", "带伤", "昏迷",
)

# 泛类身份后缀(「某组织成员」「一个士兵」类)
_CONTEXTUAL_ALIAS_ROLE_SUFFIXES = ("成员", "队员", "士兵", "手下", "一员")

# 临时指称前缀(「某某少年」「那个少女」类)
_CONTEXTUAL_ALIAS_TEMP_PREFIXES = (
    "某某", "某个", "某名", "某位", "某",
    "一个", "一名", "一位", "那个", "这个", "那名", "这名",
)


def _is_contextual_alias(alias: str) -> str | None:
    """判断 alias 是否为语境指称而非稳定别名(issue #70, E3)。

    命中返回原因字符串,未命中返回 None(放行)。
    只用高精确度特征:代词精确匹配、假想/外观关键词、泛类后缀、
    临时指称前缀;拿不准的一律放行。
    """
    if not alias:
        return None
    # 代词与自称(精确匹配,不做子串匹配,避免误伤含这些字的人名)
    if alias in _CONTEXTUAL_ALIAS_PRONOUNS:
        return "pronoun/self-address"
    # 假想/反事实身份
    for marker in _CONTEXTUAL_ALIAS_HYPOTHETICAL:
        if marker in alias:
            return f"hypothetical identity ({marker})"
    # 外观与状态描述
    for marker in _CONTEXTUAL_ALIAS_APPEARANCE:
        if marker in alias:
            return f"appearance/state phrase ({marker})"
    # 描述性短语(与 name_authority.alias_safety_level level-0 同口径)
    if "的" in alias:
        return "descriptive phrase (contains 的)"
    # 泛类角色身份
    for suffix in _CONTEXTUAL_ALIAS_ROLE_SUFFIXES:
        if len(alias) > len(suffix) and alias.endswith(suffix):
            return f"generic role ({suffix})"
    # 临时指称
    for prefix in _CONTEXTUAL_ALIAS_TEMP_PREFIXES:
        if len(alias) > len(prefix) and alias.startswith(prefix):
            return f"temporary reference ({prefix}…)"
    return None


class FactValidator:
    """Validate and clean a ChapterFact instance."""

    def __init__(self, genre: str | None = None, *, skip_validation: bool = False,
                 audit_log_path: Path | None = None) -> None:
        self._genre = genre
        self._skip_validation = skip_validation  # For ablation experiments
        # name_corrections: short_name → full_name mapping built from
        # entity dictionary.  E.g., {"愣子": "二愣子"} when the dictionary
        # contains "二愣子" with a numeric prefix that jieba/LLM truncated.
        self._name_corrections: dict[str, str] = {}
        # 决策审计(issue #70 provenance):validate() 内的改名/吞并决策,
        # 与 NameResolver 改写共用 name_resolution_log.jsonl 通道;
        # audit_log_path 仅供测试重定向,None = 默认审计路径。
        self._audit_log_path = audit_log_path
        self._audit_records: list[dict] = []

    def set_name_corrections(self, corrections: dict[str, str]) -> None:
        """Set name correction mapping (truncated_name → full_name).

        Built from entity dictionary by AnalysisService at startup.
        Applied during character validation to fix LLM extraction errors
        where numeric-prefix names are truncated (e.g., 愣子 → 二愣子).
        """
        self._name_corrections = corrections

    def validate(self, fact: ChapterFact, chapter_text: str | None = None) -> ChapterFact:
        """Return a cleaned copy of the ChapterFact.

        chapter_text(可选):本章原文。传入后,自动补 character 的交叉检查会做
        原文锚定(canonical 污染防线)——原文不可定位的名字不凭空造实体条目;
        默认 None 时跳过该校验,保持旧行为。
        """
        if self._skip_validation:
            return fact  # Ablation: bypass all validation
        self._audit_records = []
        characters = self._validate_characters(fact.characters)
        relationships = self._validate_relationships(fact.relationships, characters)
        locations = self._validate_locations(fact.locations, characters)
        spatial_relationships = self._validate_spatial_relationships(
            fact.spatial_relationships, locations
        )
        item_events = self._validate_item_events(
            fact.item_events,
            non_item_names=self._collect_non_item_names(
                characters, locations, fact.org_events, fact.new_concepts,
            ),
        )
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
        characters = self._ensure_participants_in_characters(
            characters, events, chapter_text,
        )

        # Cross-check: ensure relationship persons exist in characters
        characters = self._ensure_relation_persons_in_characters(
            characters, relationships, chapter_text,
        )

        # Post-processing: disambiguate homonymous location names (N29.3)
        # Renames generic names like "夹道" → "大观园·夹道" when parent is known.
        # Must run after all other validation so parent fields are finalized.
        locations, characters, events, spatial_relationships = (
            self._disambiguate_homonym_locations(
                locations, characters, events, spatial_relationships,
            )
        )

        # Post-processing: disambiguate generic person names (v0.65)
        # Renames "樵夫" → "灵台方寸山·樵夫" using the chapter's primary setting.
        # Must run AFTER _validate_characters (which already ran) but we need
        # the rename_map to sync across relationships and events.
        person_rename_map = self._build_generic_person_rename_map(characters, locations)
        if person_rename_map:
            # 审计:泛称改名(「地点·泛称」消歧),可追溯每个消歧名的出处
            for old, new in person_rename_map.items():
                self._audit_records.append({
                    "field": "characters",
                    "from": old,
                    "to": new,
                    "source": "correction",
                    "rule": "generic_person_rename",
                })
            characters = [
                ch.model_copy(update={"name": person_rename_map[ch.name]})
                if ch.name in person_rename_map else ch
                for ch in characters
            ]
            relationships = [
                rel.model_copy(update={
                    **({"person_a": person_rename_map[rel.person_a]} if rel.person_a in person_rename_map else {}),
                    **({"person_b": person_rename_map[rel.person_b]} if rel.person_b in person_rename_map else {}),
                }) if rel.person_a in person_rename_map or rel.person_b in person_rename_map else rel
                for rel in relationships
            ]
            events = [
                evt.model_copy(update={"participants": [person_rename_map.get(p, p) for p in evt.participants]})
                if any(p in person_rename_map for p in evt.participants) else evt
                for evt in events
            ]

        if self._audit_records:
            for rec in self._audit_records:
                rec["novel_id"] = fact.novel_id
                rec["chapter_id"] = fact.chapter_id
            write_audit_records(self._audit_records, self._audit_log_path)

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
            # Exception: _GENERIC_PERSON_CANDIDATES are kept for later disambiguation
            # (e.g., "樵夫" → "灵台方寸山·樵夫" in validate() post-processing)
            if name not in _GENERIC_PERSON_CANDIDATES:
                reason = _is_generic_person(name, self._genre)
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
                    source=existing.source,  # FR-4.1: 保留来源标记(recall_pass 可追溯)
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
                source=keeper_ch.source,  # FR-4.1: 保留来源标记
            )
            # 审计:alias-merge 吞并角色(issue #70 provenance)
            self._audit_records.append({
                "field": "characters",
                "from": target,
                "to": keeper,
                "source": "correction",
                "rule": "alias_merge",
            })
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

        # ── Composite character.name defense (canonical 污染防线) ──
        # _clean_aliases Rule 3 的同款思路应用到 character.name 本身:
        # name 同时包含同章 ≥2 个其他 character 的完整名字(如"八戒沙僧"),
        # 是 LLM 把多个人名拼接成了一个伪名,按 Rule 3 的处置方式剔除。
        # 只含 1 个他人全名不算(如"孙悟空"含"悟空"是合法长名),避免误伤。
        composite_names = [
            name for name in seen
            if sum(
                1 for other in seen
                if other != name and len(other) >= 2 and other in name
            ) >= 2
        ]
        for name in composite_names:
            logger.info(
                "Dropping composite character name '%s' (含同章多个他人全名)",
                name,
            )
            del seen[name]

        return list(seen.values())

    def _clean_aliases(
        self,
        aliases: list[str],
        owner_name: str,
        all_char_names: set[str],
    ) -> list[str]:
        """Clean new_aliases by removing four classes of erroneous aliases.

        1. Alias is another independent character in this chapter
        2. Alias is too long (>6 chars) — likely a descriptive phrase
        3. Alias contains another character's full name (e.g., "水军头领李俊")
        4. Alias is a contextual reference, not a stable alias (issue #70, E3):
           pronouns/self-address, hypothetical names, appearance/state phrases,
           generic roles, temporary references. Decisions go to logger.info +
           the name_resolution audit channel.
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
            # Rule 4 (issue #70, E3): 语境指称不是稳定别名
            # 宁可漏拦不可误杀:仅高精确度特征命中才剔除,拿不准的一律放行
            ctx_reason = _is_contextual_alias(alias)
            if ctx_reason:
                logger.info(
                    "Alias '%s' dropped from '%s': 语境指称非稳定别名 (%s)",
                    alias, owner_name, ctx_reason,
                )
                self._audit_records.append({
                    "field": "characters.new_aliases",
                    "from": alias,
                    "to": "",
                    "source": "correction",
                    "rule": "contextual_alias_drop",
                    "owner": owner_name,
                    "reason": ctx_reason,
                })
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
        # Pre-processing: split compound location names joined by conjunctions
        # E.g., "新房与西院" → "新房" + "西院" as separate entries
        from src.models.chapter_fact import LocationFact
        expanded_locs = []
        for loc in locs:
            split_parts = None
            for conj in ("与", "和", "及"):
                if conj in loc.name:
                    idx = loc.name.index(conj)
                    left = loc.name[:idx].strip()
                    right = loc.name[idx + 1:].strip()
                    if len(left) >= 2 and len(right) >= 2:
                        split_parts = [left, right]
                        break
            if split_parts:
                logger.debug(
                    "Splitting compound location: '%s' → %s",
                    loc.name, split_parts,
                )
                for part in split_parts:
                    expanded_locs.append(loc.model_copy(update={"name": part}))
            else:
                expanded_locs.append(loc)
        locs = expanded_locs

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
            # Normalize known variant spellings
            name = _LOCATION_NAME_NORMALIZE.get(name, name)
            if len(name) < _NAME_MIN_LEN_OTHER:
                continue
            # Deduplicate locations
            if name in seen_names:
                continue
            seen_names.add(name)
            # Morphological validation (replaces blocklist approach)
            reason = _is_generic_location(name, self._genre)
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
            # Normalize parent field through the same pipeline (_clamp_name →
            # character variants → exact corrections). Without this, parent
            # strings like "南膳部洲" stay unnormalized and become phantom nodes.
            normalized_parent = loc.parent
            if normalized_parent:
                normalized_parent = _clamp_name(normalized_parent)
                normalized_parent = _LOCATION_NAME_NORMALIZE.get(
                    normalized_parent, normalized_parent
                )
            # D2 (issue #70): type 与名字后缀形态明显矛盾时软降级为「区域」
            checked_type = _downgrade_inconsistent_location_type(name, loc.type)
            valid.append(
                loc.model_copy(update={
                    "name": name, "parent": normalized_parent, "type": checked_type,
                })
            )

        # Validate peers field
        cleaned_valid = []
        for loc in valid:
            if loc.peers:
                valid_peers = [
                    p for p in loc.peers
                    if p and p != loc.name and not _is_generic_location(p, self._genre)
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
            # Normalize known variant spellings
            source = _LOCATION_NAME_NORMALIZE.get(source, source)
            target = _LOCATION_NAME_NORMALIZE.get(target, target)
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
            # ── Contains direction fix: ensure source is larger than target ──
            if relation_type == "contains":
                swapped = False
                src_rank = _get_contains_rank(source)
                tgt_rank = _get_contains_rank(target)
                if src_rank is not None and tgt_rank is not None and src_rank > tgt_rank:
                    # Source is smaller (higher rank) than target → swap
                    source, target = target, source
                    swapped = True
                elif src_rank == tgt_rank or (src_rank is None and tgt_rank is None):
                    # Same rank or both unknown: use name length (longer = more specific = smaller)
                    if len(source) > len(target) + 2:
                        source, target = target, source
                        swapped = True
                    # Name containment tiebreak: "石圪节公社" starts with "石圪节"
                    elif source.startswith(target) and len(source) > len(target):
                        source, target = target, source
                        swapped = True
                if swapped:
                    logger.debug("Fixed contains inversion: %s→%s (was %s→%s)",
                                 source, target, target, source)

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
            # Validate distance_class enum
            distance_class = rel.distance_class
            if distance_class and distance_class not in _VALID_DISTANCE_CLASS:
                distance_class = None
            # Clamp confidence_score to 0.0-1.0
            confidence_score = rel.confidence_score
            if confidence_score is not None:
                confidence_score = max(0.0, min(1.0, confidence_score))
            valid.append(SpatialRelationship(
                source=source,
                target=target,
                relation_type=relation_type,
                value=rel.value,
                confidence=confidence,
                narrative_evidence=evidence,
                distance_class=distance_class,
                confidence_score=confidence_score,
                waypoints=rel.waypoints,
            ))
        return valid

    @staticmethod
    def _collect_non_item_names(
        characters: list[CharacterFact],
        locations: list,
        org_events: list[OrgEventFact],
        concepts: list,
    ) -> set[str]:
        """收集本章非物品实体名(人物+别名/地点/组织/概念),供 related item
        的 target-type 校验使用(issue #70:领域/能力机制类实体不得进入关联物品)。"""
        names: set[str] = set()
        for ch in characters:
            if ch.name:
                names.add(ch.name)
            names.update(a for a in ch.new_aliases if a)
        for loc in locations:
            if loc.name:
                names.add(loc.name)
        for oe in org_events:
            if oe.org_name:
                names.add(oe.org_name)
        for nc in concepts:
            if nc.name:
                names.add(nc.name)
        return names

    def _validate_item_events(
        self,
        items: list[ItemEventFact],
        non_item_names: set[str] | None = None,
    ) -> list[ItemEventFact]:
        # 第一遍:本章有效物品名集合,作为 related「两端必须都是物品」的判据
        chapter_item_names: set[str] = set()
        for item in items:
            n = _clamp_name(item.item_name)
            if len(n) >= _NAME_MIN_LEN_OTHER:
                chapter_item_names.add(n)

        valid = []
        for item in items:
            name = _clamp_name(item.item_name)
            if len(name) < _NAME_MIN_LEN_OTHER:
                continue
            action = item.action
            if action not in _VALID_ITEM_ACTIONS:
                action = "出现"
            related = self._validate_related_items(
                item.related, name, chapter_item_names, non_item_names or set(),
            )
            valid.append(
                item.model_copy(update={
                    "item_name": name, "action": action, "related": related,
                })
            )
        return valid

    @staticmethod
    def _validate_related_items(
        related: list,
        owner_name: str,
        chapter_item_names: set[str],
        non_item_names: set[str],
    ) -> list:
        """related item 软校验(issue #70):logger.info + 剔除,不抛错。

        - 空名/过短名、自引用:剔除
        - 无 evidence:剔除(related 是证据门控关系,prompt 要求逐字引用原文;
          口径与 org_event evidence 的「无依据不记录」一致,这里在验证层执行)
        - 目标不在本章物品列表中:剔除(两端必须都是 item 类型实体;
          目标是人物/地点/组织/概念时即 issue #70 的 target-type 泄漏)
        拿不准的一律放行(名字同时是物品与其他实体时不判)。
        """
        valid = []
        seen: set[str] = set()
        for rel in related:
            rname = _clamp_name(rel.name)
            if len(rname) < _NAME_MIN_LEN_OTHER or rname == owner_name:
                continue
            if rname in seen:
                continue
            if not rel.evidence or not rel.evidence.strip():
                logger.info(
                    "Dropping related item '%s' on '%s': 无原文证据(evidence 为空)",
                    rname, owner_name,
                )
                continue
            if rname not in chapter_item_names:
                if rname in non_item_names:
                    logger.info(
                        "Dropping related item '%s' on '%s': 目标是人物/地点/组织/概念,"
                        "非物品实体(target-type 泄漏)",
                        rname, owner_name,
                    )
                else:
                    logger.info(
                        "Dropping related item '%s' on '%s': 不在本章物品列表中",
                        rname, owner_name,
                    )
                continue
            seen.add(rname)
            relation = rel.relation.strip() if rel.relation else ""
            valid.append(
                rel.model_copy(update={"name": rname, "relation": relation})
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

    # Suffixes that indicate a name match is part of a place/org, not a person
    _NAME_BOUNDARY_BLOCKLIST = set("国省市县镇村区域界地洲岛山河湖海洋城池寺庙观殿阁楼台塔")

    def _fill_event_participants(
        self, characters: list[CharacterFact], events: list[EventFact]
    ) -> list[EventFact]:
        """Fill empty event participants by scanning summary for character names.

        Uses boundary-aware matching: a matched name must not be followed by
        geographic/architectural suffixes (e.g., "韩" should not match in "韩国").
        """
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
                # Scan summary for character names with boundary check
                found = []
                for name in sorted_names:
                    if len(name) < 1:
                        continue
                    idx = ev.summary.find(name)
                    if idx < 0:
                        continue
                    # Boundary check: next char should not be a place suffix
                    end = idx + len(name)
                    if end < len(ev.summary) and ev.summary[end] in self._NAME_BOUNDARY_BLOCKLIST:
                        continue
                    if name not in found:
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
        self, characters: list[CharacterFact], events: list[EventFact],
        chapter_text: str | None = None,
    ) -> list[CharacterFact]:
        """Add missing event participants as character entries.

        canonical 污染防线:传入 chapter_text 时,名字在本章原文中不可定位
        (span 定位口径)则不补成 character——事件记录本身保留,只是不凭空
        造实体条目(LLM 编造名由此进入结构化结果的通道)。
        """
        char_names = {ch.name for ch in characters}
        # Also check aliases
        for ch in characters:
            char_names.update(ch.new_aliases)

        for ev in events:
            for p in ev.participants:
                p = p.strip()
                if p and p not in char_names and len(p) >= _NAME_MIN_LEN and not _is_generic_person(p, self._genre):
                    if chapter_text is not None and not _name_in_text(p, chapter_text):
                        logger.info(
                            "事件参与者 %r 原文不可定位,不自动补为 character", p,
                        )
                        continue
                    characters.append(CharacterFact(name=p))
                    char_names.add(p)
                    logger.debug("Auto-added character from event participant: %s", p)
        return characters

    def _ensure_relation_persons_in_characters(
        self, characters: list[CharacterFact], relationships,
        chapter_text: str | None = None,
    ) -> list[CharacterFact]:
        """Add missing relationship persons as character entries.

        canonical 污染防线:传入 chapter_text 时,名字在本章原文中不可定位
        则不补成 character(关系记录本身保留)。
        """
        char_names = {ch.name for ch in characters}
        for ch in characters:
            char_names.update(ch.new_aliases)

        for rel in relationships:
            for name in (rel.person_a, rel.person_b):
                name = name.strip()
                if name and name not in char_names and len(name) >= _NAME_MIN_LEN and not _is_generic_person(name, self._genre):
                    if chapter_text is not None and not _name_in_text(name, chapter_text):
                        logger.info(
                            "关系人名 %r 原文不可定位,不自动补为 character", name,
                        )
                        continue
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
            if parent:
                parent = _clamp_name(parent)
                parent = _LOCATION_NAME_NORMALIZE.get(parent, parent)
            if parent and parent not in existing_names and parent not in to_add:
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

    def _build_generic_person_rename_map(
        self,
        characters: list[CharacterFact],
        locations: list,
    ) -> dict[str, str]:
        """Build rename map for generic person disambiguation.

        Returns {old_name: new_name} for generic persons found in this chapter.
        E.g., {"樵夫": "灵台方寸山·樵夫"}
        """
        # Find primary setting location
        primary_setting: str | None = None
        for loc in locations:
            if getattr(loc, "role", None) == "setting" and loc.name:
                primary_setting = loc.name
                break
        if not primary_setting:
            for loc in locations:
                if loc.parent and loc.name:
                    primary_setting = loc.name
                    break
        if not primary_setting and locations:
            primary_setting = locations[0].name if locations[0].name else None

        if not primary_setting:
            return {}

        rename_map: dict[str, str] = {}
        for char in characters:
            if char.name in _GENERIC_PERSON_CANDIDATES:
                rename_map[char.name] = f"{primary_setting}·{char.name}"

        if rename_map:
            logger.info(
                "Disambiguated %d generic persons: %s",
                len(rename_map),
                ", ".join(f"{k}→{v}" for k, v in rename_map.items()),
            )

        return rename_map

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
