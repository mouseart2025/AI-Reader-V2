"""Shared homonym-prone location name definitions.

Used by both conflict_detector (to skip false-positive hierarchy conflicts)
and fact_validator (to disambiguate generic building names with parent prefixes).
"""

# Architectural suffixes — single chars representing building parts/rooms/passages.
# Locations composed purely of these are inherently ambiguous (e.g. "夹道", "后门")
# and can exist in multiple distinct buildings across a novel.
ARCH_SUFFIXES = frozenset(
    "门道廊厅堂殿阁楼房室间院墙窗"
    "阶梯井亭台榭轩斋"
)

# Explicit homonym-prone names — common architectural terms that appear
# in many different buildings (e.g. 荣国府's 夹道 vs 甄家's 夹道).
HOMONYM_PRONE_NAMES = frozenset({
    # Passages / entrances
    "夹道", "角门", "后门", "侧门", "正门", "大门", "二门", "三门", "垂花门",
    "前门", "山门", "辕门", "朝门", "仪门",
    "甬道", "走廊", "过道", "回廊", "穿堂", "抄手游廊",
    # Rooms / chambers
    "上房", "正房", "正室", "里间", "外间", "外间房", "内室", "内房",
    "厢房", "偏房", "耳房", "暖阁", "套间",
    "书房", "卧房", "卧室", "厨房", "柴房",
    # Halls
    "前厅", "后堂", "正厅", "大厅", "花厅", "偏厅", "中堂",
    "配殿", "偏殿", "抱厦",
    # Palace / imperial buildings — each kingdom has one,
    # must be disambiguated by parent (朱紫国·皇宫 vs 乌鸡国·皇宫)
    "皇宫", "后宫", "内宫", "正宫", "偏宫",
    "金殿", "正殿", "后殿", "前殿", "内殿",
    "御花园", "后花园", "御书房",
    "金銮殿", "大雄宝殿",
    # Outdoor spaces
    "后院", "前院", "院子", "花园", "庭院",
    # Generic facilities — same name appears in many kingdoms/houses
    "仓库", "马厩", "马棚", "门房", "倒座",
    "馆驿", "驿馆",
    # Natural terrain — same name at different locations
    "树林", "山洞", "小路", "山坡", "河边", "湖边", "草地",
    "森林", "密林", "林中", "溪边", "崖边", "洞口",
    "山脚", "山腰", "山顶", "岸边", "路边", "林间",
    "水潭", "深潭", "石洞", "山谷", "峡谷",
    # Military / temporary encampments
    "中军帐", "营地", "军营", "帐篷", "大帐", "营寨", "阵前",
})


def is_homonym_prone(name: str) -> bool:
    """Return True if the location name is a generic architectural term
    that commonly exists in multiple distinct buildings."""
    if name in HOMONYM_PRONE_NAMES:
        return True
    # Short names (≤2 chars) composed entirely of architectural suffixes
    return bool(len(name) <= 2 and all(c in ARCH_SUFFIXES for c in name))


# ── 地名别名归一(2026-09-20,方案 A:VoteBuilder canonical 映射)────
# geo 管线全链路用原始字符串做 key,同一城市的异名(水浒 东京/京师/汴梁城)
# 各自成节点、票仓被分摊,是 revisit parent 冲突大头。此处收录**双源证据**
# (errata gold + fixture/原文共指/prior 注释佐证)的别名→canonical 映射;
# 证据不足的不收。消费方:VoteBuilder(票仓归并)、KnowledgePrior(先验边
# 短路)、compute_revisit_consistency(可选 canonical 化)。开关
# evolve_param("geo_alias.enabled", True):默认开,表为空(其余四本)时
# 零行为。
#
# 收录判断(水浒,2026-09-20 实证):
# - 东京系:errata 东京=正确(city,parent 京畿);fixture 汴梁城→东京;
#   快照 v7 东京/京师/汴梁城 三个独立 city 节点票仓分摊(50/6/4)。
# - 北京系:canonical 选「北京」——errata 主称谓"北京(大名府)是北宋四京
#   之一"、freq 最高(北京 11 / 大名府 10 / 北京大名府 4)、prior 锚点
#   北京→河北;errata 大名府/北京大名府 带 C-tier 错误(别名分裂所致)。
# - 明确不收:梁山泊/水浒寨(寨在泊中,fixture/errata 均为父子)、
#   南丰/南丰城(区域/城父子,fixture 南丰城→南丰)、盖州类同名异地。
LOCATION_ALIAS_MAP: dict[str, dict[str, str]] = {
    "水浒": {
        "京师": "东京",        # errata 东京/京师 同指北宋首都开封;票仓分摊实证
        "汴梁城": "东京",      # fixture correct_parent=东京;prior 注释"汴梁城为东京别称"
        "北京大名府": "北京",  # errata 主称谓"北京(大名府)";fixture 三者同 parent 河北
        "大名府": "北京",      # 同上;errata C-tier 错误(别名分裂)
    },
}


def location_alias_map_for_title(novel_title: str) -> dict[str, str]:
    """按小说标题取别名映射(与 KnowledgePrior 的标题匹配约定一致);
    无匹配(含其余四本)返回空表 = 零行为。"""
    for key, mapping in LOCATION_ALIAS_MAP.items():
        if key in novel_title:
            return mapping
    return {}


# 别名节点保留边(apply 层归并用,2026-09-21):VoteBuilder 归并票仓后,
# 旧 ws/snapshot 中仍残留别名节点(汴梁城→东京 等)。apply 归并时别名
# 节点的 children 一律改指 canonical;别名节点自身的 parent 边只保留
# 金标(fixture correct_parent)/先验佐证者,列入下表;无佐证的
# (北京大名府→主世界、京师→京畿)删除,空壳节点随之摘除。
# 与 LOCATION_ALIAS_MAP 同标题匹配约定;未列出即零行为。
LOCATION_ALIAS_KEEP_EDGES: dict[str, dict[str, str]] = {
    "水浒": {
        "汴梁城": "东京",  # fixture correct_parent=东京(别名自指边)
        "大名府": "河北",  # fixture correct_parent=河北
    },
}


def location_alias_keep_edges_for_title(novel_title: str) -> dict[str, str]:
    """按小说标题取别名保留边表;无匹配返回空表 = 全部删除别名边。"""
    for key, edges in LOCATION_ALIAS_KEEP_EDGES.items():
        if key in novel_title:
            return edges
    return {}


# ── Passage-like / transit forms (Story 5.2) ───────────────────────────
# Roads, corridors, stairs, intersections, and similar transit structures are
# *edges* in the spatial graph, not *containers*. They must never participate
# in a hierarchical parent-child edge as the PARENT (they connect/transit
# rather than contain), and a passage-like child must not be force-attached to
# uber_root / world root (it is a topology node, not a geographic container).
#
# The hierarchy builder (world_structure_agent / vote_builder / edmonds_resolver)
# gates on this predicate so that e.g. "道路下辖学校" or "走廊挂到天下" never
# materialize. Single source of truth — sibling of HOMONYM_PRONE_NAMES.
PASSAGE_EXACT = frozenset({
    # Corridors / passages
    "走廊", "甬道", "过道", "回廊", "穿堂", "抄手游廊", "长廊", "游廊",
    "夹道", "门道",
    # Stairs / steps
    "楼梯", "台阶", "石阶", "阶梯", "扶梯", "天梯",
    # Roads / paths / streets
    "通道", "隧道", "地道", "小路", "大路", "小道", "大道", "山路", "水路",
    "官道", "古道", "长街", "大街", "街道", "小巷", "巷子", "小径", "曲径",
    # Intersections
    "路口", "岔口", "岔路", "十字路口", "交叉口", "三岔口", "丁字路口",
})

# Single-char suffixes marking a transit form when they END a 2+ char name.
# Deliberately narrow: 道/路/街/巷/径/廊/阶/隧.
PASSAGE_SUFFIXES = frozenset("道路街巷径廊阶隧")


def is_passage_like(name: str) -> bool:
    """Return True if the location name is a passage / transit form.

    Used by Story 5.2 hierarchical-evidence gating: a passage-like node must
    not be a parent in the hierarchy (it does not contain anything), and a
    passage-like child must not be force-hung under uber_root / world root.

    Design note: this is PARENT-side full gating + CHILD-side world-root /
    legacy gating. A passage-like child that carries explicit real-parent votes
    (e.g. contains: 长安 contains 长安街) is still attached to that real parent;
    only the spurious "dangle under 天下" fallback is suppressed. See
    PASSAGE_EXACT / PASSAGE_SUFFIXES for the lexicon.
    """
    if not name:
        return False
    if name in PASSAGE_EXACT:
        return True
    # 2+ char names whose final char is a transit suffix (山路, 长街, 回廊,
    # 地道...). Single-char names excluded to avoid false positives (道/路 as
    # standalone nouns).
    return bool(len(name) >= 2 and name[-1] in PASSAGE_SUFFIXES)


# ── Special-space (realm / pocket-dimension) detection (Story 5.3) ──────────
# Single source of truth for classifying 架空特殊空间 (异空间 / 领域 / 维度 /
# 结界 / 秘境 / 仙界 / 魔域 …) as the dedicated `realm` tier.
#
# Aligned with:
#   • Epic 4 Story 4.1 closed subtype lexicon (placeholder "特殊空间"), which
#     delegates the full taxonomy to this story — this module is the shared
#     constant source both tier_classifier (5.3) and the 4.1 subtype validator
#     should reference.
#   • world_structure_agent._REALM_LAYER_KEYWORDS / _INSTANCE_NAME_KEYWORDS /
#     _INSTANCE_TYPE_KEYWORDS (the layer-assignment realm keywords).
#
# Design: CURATED keyword set (substring match), NOT a blanket 界/域 suffix
# rule. A naive "界/域 → realm" rule would misclassify geographic compounds
# (西域 = Western Regions, 国界 = border, 世界 = world). Those are listed in
# _SPECIAL_SPACE_EXCLUDE so they stay geographic. Sub-realms that must remain
# `region` (天庭 within 天界, 幽冥界 within 冥界) are also excluded and handled
# by tier_classifier._TIER_OVERRIDES instead.
_SPECIAL_SPACE_KEYWORDS: frozenset[str] = frozenset({
    # Realm planes (界 / 域)
    "仙界", "魔界", "妖界", "灵界", "人界", "真仙界", "真魔界", "古魔界",
    "圣界", "冥界", "幽冥", "地府", "阴曹", "阴司", "黄泉", "天界",
    "魔域", "妖域", "灵域", "神域", "天域", "鬼域", "仙域", "佛界",
    "鬼界", "妖境", "魔境", "幻界",
    # Pocket dimensions / secret realms
    "异空间", "封印空间", "法术空间", "特殊空间", "小世界", "次元", "维度",
    "洞天", "秘境", "结界", "幻境", "福地", "芥子空间", "须弥空间",
    # Sci-fi planes
    "太阳系", "银河系", "银河", "三体世界", "三体星系", "三体行星",
    "三体游戏世界", "三体游戏", "蛮荒世界", "冥河之地",
})

# Geographic / sub-realm names that contain realm-looking characters but are NOT
# standalone special spaces — keep them geographic or let _TIER_OVERRIDES decide.
_SPECIAL_SPACE_EXCLUDE: frozenset[str] = frozenset({
    "西域", "国界", "世界", "藏界", "仙景界", "国东界", "苦界", "法界",
    "境界", "海域", "天庭", "幽冥界",
})


def is_special_space(name: str) -> bool:
    """Return True if `name` denotes a fantasy / sci-fi special space (realm,
    pocket dimension, alien plane) rather than a conventional geographic place.

    Used by Story 5.3 so that "异空间=大陆" no longer happens: special-space
    names are classified into the dedicated `realm` tier and exempted from
    suffix-rank direction validation. Exact-match EXCLUDE wins over keyword
    substring so geographic compounds (西域 / 国界 / 世界) and sub-realms
    (天庭 / 幽冥界) are not caught.
    """
    if not name:
        return False
    if name in _SPECIAL_SPACE_EXCLUDE:
        return False
    return any(kw in name for kw in _SPECIAL_SPACE_KEYWORDS)
