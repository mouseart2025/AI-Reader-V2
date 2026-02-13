"""Location hint service: infer cardinal direction from Chinese location names.

Provides direction hints based on Chinese directional characters in location
names (东=east, 西=west, 南=south, 北=north, 中=center). Only applies to
geographic-type locations to avoid false positives from person names or
non-geographic terms.
"""

from __future__ import annotations

# Characters that imply cardinal direction
_DIRECTION_CHARS: dict[str, str] = {
    "东": "east",
    "西": "west",
    "南": "south",
    "北": "north",
    "中": "center",
}

# Geographic location type keywords — only apply direction hints to these
_GEO_TYPE_KEYWORDS = (
    "洲", "域", "界", "国", "海", "大陆", "部洲",
    "城", "城市", "镇", "府", "省", "州", "县",
    "山", "峰", "岭", "河", "湖", "泉", "池", "泽",
    "沙漠", "森林", "草原", "岛", "半岛", "群岛",
    "门派", "宗门", "洞", "宫", "殿", "庙", "寺",
)

# Exclusion patterns: names that contain direction chars but are not geographic
# (e.g., person names, abstract concepts)
_EXCLUSION_SUFFIXES = ("坡", "风", "方", "侧", "边", "面", "向")


def extract_direction_hint(
    location_name: str,
    location_type: str = "",
) -> str | None:
    """Infer cardinal direction from a location name.

    Args:
        location_name: The location name to analyze.
        location_type: The location type (e.g., "城市", "山"). When provided,
            only geographic types get direction hints.

    Returns:
        Direction string ("east", "west", "south", "north", "center") or None.
    """
    if not location_name:
        return None

    # If location_type is provided, only apply to geographic types
    if location_type and not any(kw in location_type for kw in _GEO_TYPE_KEYWORDS):
        return None

    # Check for direction characters — prioritize earlier match in name
    for char, direction in _DIRECTION_CHARS.items():
        if char not in location_name:
            continue

        # Check for exclusion patterns: character + exclusion suffix
        idx = location_name.index(char)
        if idx + 1 < len(location_name):
            next_char = location_name[idx + 1]
            if next_char in _EXCLUSION_SUFFIXES:
                continue

        return direction

    return None


def batch_extract_direction_hints(
    locations: list[dict],
) -> dict[str, str]:
    """Extract direction hints for a batch of locations.

    Args:
        locations: List of location dicts with "name" and optional "type" keys.

    Returns:
        Dict mapping location name to direction hint (only includes those with hints).
    """
    hints: dict[str, str] = {}
    for loc in locations:
        name = loc.get("name", "")
        loc_type = loc.get("type", "")
        hint = extract_direction_hint(name, loc_type)
        if hint is not None:
            hints[name] = hint
    return hints
