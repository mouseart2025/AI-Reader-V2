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
    "夹道", "角门", "后门", "侧门", "正门", "大门", "二门", "垂花门",
    "甬道", "走廊", "过道", "回廊", "穿堂", "抄手游廊",
    # Rooms / chambers
    "上房", "正房", "正室", "里间", "外间", "外间房", "内室", "内房",
    "厢房", "偏房", "耳房", "暖阁", "套间",
    "书房", "卧房", "卧室", "厨房", "柴房",
    # Halls
    "前厅", "后堂", "正厅", "大厅", "花厅", "偏厅", "中堂",
    "配殿", "偏殿", "抱厦",
    # Outdoor spaces
    "后院", "前院", "院子", "花园", "后花园",
    # Generic
    "仓库", "马厩", "马棚", "门房", "倒座",
})


def is_homonym_prone(name: str) -> bool:
    """Return True if the location name is a generic architectural term
    that commonly exists in multiple distinct buildings."""
    if name in HOMONYM_PRONE_NAMES:
        return True
    # Short names (≤2 chars) composed entirely of architectural suffixes
    if len(name) <= 2 and all(c in ARCH_SUFFIXES for c in name):
        return True
    return False
