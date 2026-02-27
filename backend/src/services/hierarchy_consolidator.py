"""Post-processing module to consolidate location hierarchy.

Runs after WorldStructureAgent._resolve_parents() to reduce root nodes
to single digits by:
1. Parsing compound location names (山东济州 → 山东 > 济州)
2. Adopting sub-locations via suffix patterns (东京城外 → child of 东京)
3. Bridging known prefectures to their provinces via Chinese geo knowledge
4. Fixing inverted province relationships
5. Connecting provinces to an uber-root (天下)
6. Adopting remaining roots (mountains, rivers, foreign states)

This module is designed for Chinese novels across all periods (Song/Ming/Qing/modern).
For fantasy novels with non-Chinese geography, it gracefully degrades to using
only the existing region system from WorldStructure.
"""

from __future__ import annotations

import logging
from collections import Counter

logger = logging.getLogger(__name__)

# ── Province prefixes found in compound location names ──
# e.g., "山东济州" = "山东" + "济州", "江西信州" = "江西" + "信州"
_PROVINCE_PREFIXES: set[str] = {
    "山东", "山西", "河北", "河南", "河东",
    "江西", "江南", "江北",
    "浙西", "浙东", "两浙",
    "淮西", "淮东",
    "陕西", "关西",
    "湖南", "湖北",
    "广东", "广西",
    "福建", "四川",
    "北地",
}

# ── Location name suffixes that indicate sub-locations ──
# e.g., "东京城外" = "东京" + "城外" → parent should be "东京"
_LOCATION_SUFFIXES: tuple[str, ...] = (
    # City periphery
    "城外", "城里", "城内", "城中", "城下", "城边",
    # Administrative area
    "地面", "地界", "境内", "境界", "界上", "界",
    "管下", "管内",
    # Proximity
    "附近", "一带", "周边",
    # Direction
    "以东", "以西", "以南", "以北",
    "东门外", "西门外", "南门外", "北门外",
    "门外", "门内",
    # Sub-area patterns
    "城东", "城西", "城南", "城北",
    "上东边",
)

# Suffixes that make a location name a child of its base
# e.g., "苏州城" = variant name for "苏州", "孟州城" = 孟州's city
_VARIANT_SUFFIXES: tuple[str, ...] = (
    "城", "城池",
)

# ── Classical Chinese prefecture → province mapping ──
# Covers Song/Ming/Qing era names commonly found in Chinese literature.
_PREFECTURE_TO_PROVINCE: dict[str, str] = {
    # ── 山东 ──
    "济州": "山东", "兖州": "山东", "郓州": "山东", "青州": "山东",
    "登州": "山东", "莱州": "山东", "密州": "山东", "沂州": "山东",
    "淄州": "山东", "潍州": "山东", "济南": "山东", "济南府": "山东",
    "东平府": "山东", "东平": "山东", "泰安州": "山东", "泰安": "山东",
    "曹州": "山东", "单州": "山东", "濮州": "山东", "滕州": "山东",
    "东昌": "山东", "东昌府": "山东", "高唐州": "山东", "高唐": "山东",
    "昭德": "山东", "昭德州": "山东", "凌州": "山东",
    "沂水县": "山东", "郓城县": "山东", "寿张县": "山东",
    "曾头市": "山东", "寇州": "山东",  # Water Margin specific
    "阳谷县": "山东", "阳谷": "山东",
    "石碣村": "山东",  # Water Margin, near 梁山泊
    "还道村": "山东",  # Water Margin, near 梁山泊
    # ── 河北 ──
    "沧州": "河北", "大名府": "河北", "大名": "河北",
    "北京": "河北",  # 北京 = 大名府 in Song dynasty
    "真定府": "河北", "真定": "河北", "相州": "河北",
    "磁州": "河北", "洺州": "河北", "开州": "河北",
    "棣州": "河北", "清州": "河北", "蓟州": "河北",
    "檀州": "河北", "恩州": "河北", "霸州": "河北",
    # ── 京畿 (开封 area) ──
    "开封府": "京畿", "开封": "京畿", "东京": "京畿",
    "汴京": "京畿", "汴梁": "京畿", "陈州": "京畿",
    "颍昌府": "京畿", "许州": "京畿", "祥符县": "京畿",
    "陈桥驿": "京畿", "京师": "京畿",
    # ── 河东 (山西) ──
    "太原府": "河东", "太原": "河东",
    "威胜": "河东", "壶关": "河东", "盖州": "河东",
    "襄垣": "河东", "沁源": "河东",
    "代州": "河东", "雁门县": "河东", "雁门": "河东",
    # ── 河南 ──
    "孟州": "河南", "陕州": "河南", "宛州": "河南",
    # ── 江南 ──
    "江宁府": "江南", "建康府": "江南", "建康": "江南",
    "宣州": "江南", "歙州": "江南",
    "江州": "江南", "洪州": "江南", "信州": "江南",
    "金陵": "江南", "金陵建康府": "江南",
    "南丰府": "江南",
    # ── 两浙 ──
    "杭州": "两浙", "苏州": "两浙", "湖州": "两浙",
    "越州": "两浙", "明州": "两浙", "台州": "两浙",
    "温州": "两浙", "临安": "两浙", "临安府": "两浙",
    "润州": "两浙", "秀州": "两浙", "睦州": "两浙",
    # ── 淮南 ──
    "扬州": "淮南", "楚州": "淮南", "淮安": "淮南",
    "泰州": "淮南", "庐州": "淮南", "安庆": "淮南",
    "无为军": "淮南", "汝宁州": "淮南", "泗州": "淮南",
    "揭阳镇": "淮南",  # Water Margin, near 江州 area
    # ── 荆湖 ──
    "荆南": "荆湖", "江陵": "荆湖", "江陵府": "荆湖",
    "鄂州": "荆湖", "潭州": "荆湖", "荆门镇": "荆湖",
    "江阴": "两浙", "宜兴": "两浙",
    # ── 关西 (陕西) ──
    "渭州": "关西", "延安府": "关西", "延安": "关西",
    "华州": "关西", "同州": "关西", "凤翔府": "关西",
    "瓦官寺": "关西", "瓦官之寺": "关西",  # Water Margin ch3, near 渭州
    # ── 蜀 ──
    "成都": "蜀", "成都府": "蜀", "达州": "蜀",
    # ── 辽东/北方 ──
    "幽州": "河北", "燕京": "河北",
}

# ── Notable mountains → province mapping ──
_MOUNTAINS_TO_PROVINCE: dict[str, str] = {
    "五台山": "河东",
    "翠屏山": "河北",  # 蓟州 area
    "华山": "关西", "西岳华山": "关西",
    "泰山": "山东",
    "北邙山": "河南",
    "二龙山": "山东",  # Water Margin
    "独龙冈": "山东",  # 郓州 area
    "梁山": "山东", "梁山泊": "山东",
    "乌龙岭": "两浙",  # 杭州 area in Water Margin
    "桃花山": "山东",  # Water Margin (near 青州)
    "黄门山": "山东",  # Water Margin
    "饮马川": "山东",  # Water Margin
    "白虎山": "山东",  # Water Margin
    "登云山": "山东",  # Water Margin
    "沂岭": "山东",  # 沂州 area
    "景阳冈": "山东",  # 阳谷县 area
    "铜山": "河南",  # Water Margin
    "伊阙山": "河南",  # 洛阳南
    "蜈蚣岭": "山东",  # Water Margin
    "大禹山": "河北",  # near 大名府
    "槐树坡": "山东",  # Water Margin
    "房山": "河北",
    "桃源岭": "两浙",  # Water Margin, near 杭州
    "南土冈": "山东",  # Water Margin, near 郓城
}

# ── Notable rivers → province mapping ──
_RIVERS_TO_PROVINCE: dict[str, str] = {
    "黄河": "京畿",
    "扬子江": "江南", "扬子大江": "江南",
    "浔阳江": "江南",
    "渭河": "关西",
    "潞水": "河东",
}

# ── Province-level nodes that should be connected to the uber-root ──
_PROVINCES: set[str] = {
    # 历史行政区
    "山东", "河北", "京畿", "河东", "河南", "山西",
    "江南", "两浙", "淮南", "荆湖", "关西", "蜀",
    "福建", "广东", "广西", "湖南", "湖北", "陕西",
    "江西", "淮东", "淮西", "浙西", "浙东", "江北",
    "北地",
    # 常见二字城市/地区名（防止被 _is_sub_location_name 误判）
    "都中", "金陵", "姑苏", "扬州", "长安", "洛阳",
    "南京", "北京", "开封", "杭州", "苏州", "成都",
    "天津", "西安", "太原", "济南", "武汉", "广州",
    "长沙", "南昌", "贵阳", "昆明", "兰州", "沈阳",
}

# Tier assignments for provinces and uber-root
_PROVINCE_TIER = "continent"
_ROOT_TIER = "world"


def _parse_compound_name(name: str) -> tuple[str, str] | None:
    """Try to split a compound location name into (province, local_name).

    Examples:
        "山东济州" → ("山东", "济州")
        "江西信州" → ("江西", "信州")
        "山东济州郓城县" → ("山东", "济州郓城县")

    Returns None if not a compound name.
    """
    for prefix in sorted(_PROVINCE_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix) and len(name) > len(prefix):
            suffix = name[len(prefix):]
            # Avoid splitting names where the suffix is just a direction/suffix word
            if suffix in ("路上", "一带", "方面", "地方"):
                continue
            return (prefix, suffix)
    return None


def _parse_location_suffix(name: str, known_locations: set[str]) -> tuple[str, str] | None:
    """Try to split a location name into (base_location, suffix).

    Only succeeds if the base_location is a known location name.

    Examples:
        "东京城外" → ("东京", "城外") if "东京" is known
        "济州城下" → ("济州", "城下") if "济州" is known

    Returns None if no match.
    """
    for suffix in sorted(_LOCATION_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[:-len(suffix)]
            if base in known_locations:
                return (base, suffix)
    return None


def _parse_variant_name(name: str, known_locations: set[str]) -> str | None:
    """Check if a name is a variant of a known location (e.g., 苏州城 → 苏州).

    Returns the base location name if it's a variant, None otherwise.
    """
    for suffix in _VARIANT_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[:-len(suffix)]
            if base in known_locations:
                return base
    return None


def _would_create_cycle(
    child: str, parent: str, location_parents: dict[str, str],
) -> bool:
    """Check if adding child→parent would create a cycle."""
    if child == parent:
        return True
    # Walk up from parent to see if we reach child
    visited: set[str] = {child}
    node = parent
    while node in location_parents:
        if node in visited:
            return True
        visited.add(node)
        node = location_parents[node]
    return node in visited


def _safe_set_parent(
    child: str,
    parent: str,
    location_parents: dict[str, str],
    reason: str = "",
) -> bool:
    """Set parent with cycle prevention. Returns True if successful."""
    if child == parent:
        return False
    if _would_create_cycle(child, parent, location_parents):
        logger.debug(
            "Cycle prevented: %s → %s (%s)", child, parent, reason,
        )
        return False
    location_parents[child] = parent
    return True


def _get_roots(location_parents: dict[str, str]) -> set[str]:
    """Get all root nodes (parents that are not themselves children)."""
    children = set(location_parents.keys())
    parents = set(location_parents.values())
    return parents - children


# ── Sub-location name patterns ──
# Names ending with these suffixes are highly likely to be sub-locations
# and should never be root nodes parenting proper geographic locations.
_SUB_LOCATION_ENDINGS: tuple[str, ...] = (
    # Positional suffixes — "X门外", "X门内", "X前", "X后"
    "门外", "门内", "门前", "门后", "门头",
    "前", "后面", "旁边", "上面", "下面",
    # Interior/exterior
    "里", "里面", "内", "外", "外面",
    "中", "中间",
    # Building parts
    "上", "下", "边", "头",
    # Compound building/room patterns
    "房内", "房里", "房中", "房前", "房后",
    "厅上", "厅内", "厅里", "厅中",
    "堂内", "堂里", "堂中", "堂上",
    "阁儿里", "阁儿内", "阁内",
    "墙下", "墙外", "墙边",
    "树下", "树林",
)

# Names containing these patterns are very likely sub-locations
_SUB_LOCATION_PATTERNS: tuple[str, ...] = (
    "粪窖", "打麦场", "葡萄架", "化人场", "牢城营",
)


def _is_sub_location_name(name: str) -> bool:
    """Check if a location name looks like a sub-location based on name patterns.

    Sub-locations are building parts, relative positions, interior rooms, etc.
    that should never be roots of a geographic hierarchy.
    """
    # Very short names are often sub-locations
    if len(name) <= 2 and name not in _PROVINCES:
        return True

    # Check ending patterns
    for suffix in sorted(_SUB_LOCATION_ENDINGS, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            # Single-char suffixes: require name length >= 3 to avoid
            # false positives on valid 2-char place names (都中, 河上)
            if len(suffix) == 1 and len(name) <= 2:
                continue
            return True

    # Check containing patterns
    for pat in _SUB_LOCATION_PATTERNS:
        if pat in name:
            return True

    return False


def _is_geographic_name(name: str) -> bool:
    """Check if a location name looks like a proper geographic entity.

    Proper geographic names typically end with administrative or natural
    geographic suffixes (州, 府, 县, 山, 岭, 江, etc.).
    """
    _GEO_SUFFIXES = (
        "州", "府", "县", "郡", "路", "京",
        "城", "镇", "村", "庄", "寨", "营", "驿", "关", "隘",
        "山", "岭", "峰", "岗", "冈",
        "江", "河", "湖", "海", "泊", "溪", "港",
        "寺", "庙", "观", "庵", "祠",
        "国",
    )
    for suffix in _GEO_SUFFIXES:
        if name.endswith(suffix) and len(name) >= 2:
            return True
    return False


def _tiered_catchall(
    all_known: set[str],
    uber_root: str | None,
    location_parents: dict[str, str],
    location_tiers: dict[str, str],
    saved_parents: dict[str, str] | None = None,
) -> int:
    """Adopt orphan nodes with tiered intermediate matching before uber_root fallback.

    For building/site orphans, tries to find an existing node whose name is
    a prefix of the orphan name (e.g., "七玄门百药园" → "七玄门"). Falls back
    to uber_root if no suitable intermediate node is found.

    When uber_root is None (foreign novels), only prefix matching is performed
    and no fallback adoption occurs — orphans without a prefix match stay as roots.

    Returns the number of nodes adopted.
    """
    from src.services.world_structure_agent import TIER_ORDER

    # 1. Collect orphan nodes (skip uber_root, existing parents, worlds with >3 desc)
    orphans: list[str] = []
    for node in list(all_known):
        if node == uber_root or node in location_parents:
            continue
        node_tier = location_tiers.get(node, "city")
        if node_tier == "world":
            desc_count = 0
            queue = [c for c, p in location_parents.items() if p == node]
            seen: set[str] = set()
            while queue and desc_count <= 3:
                n = queue.pop()
                if n in seen:
                    continue
                seen.add(n)
                desc_count += 1
                queue.extend(c for c, p in location_parents.items() if p == n)
            if desc_count > 3:
                continue
        orphans.append(node)

    if not orphans:
        return 0

    # 2. Build candidate lookup: all nodes that already have a place in the hierarchy
    existing_nodes = set(location_parents.keys()) | set(location_parents.values())
    existing_nodes.discard(uber_root)
    # Sort by name length descending for longest-prefix-first matching
    sorted_candidates = sorted(existing_nodes, key=len, reverse=True)

    # 3. Sort orphans: most specific (building/room) first, so they can find
    #    intermediate parents before less specific orphans are processed
    orphans.sort(
        key=lambda n: TIER_ORDER.get(location_tiers.get(n, "city"), 4),
        reverse=True,
    )

    adopted = 0
    tiered_matches = 0

    for orphan in orphans:
        orphan_tier = location_tiers.get(orphan, "city")
        orphan_rank = TIER_ORDER.get(orphan_tier, 4)

        matched = False

        # Try name prefix matching: orphan starts with a known node's name
        # e.g., "七玄门百药园" starts with "七玄门"
        if orphan_rank >= 4:  # city or more specific
            for candidate in sorted_candidates:
                if candidate == orphan:
                    continue
                if not orphan.startswith(candidate) or len(candidate) < 2:
                    continue
                cand_tier = location_tiers.get(candidate, "city")
                cand_rank = TIER_ORDER.get(cand_tier, 4)
                if cand_rank < orphan_rank:  # candidate is a bigger entity
                    if _safe_set_parent(orphan, candidate, location_parents,
                                        f"tiered-catchall:{orphan}→{candidate}"):
                        matched = True
                        tiered_matches += 1
                        adopted += 1
                        break

        # D.4: Try dominant intermediate node matching for site/building orphans
        # Search up to 3 levels deep to find the best adoption target
        if not matched and orphan_rank >= 5 and uber_root is not None:
            dominant = None
            dominant_desc = 0
            # BFS: check uber_root children, then their children, etc. (max 3 levels)
            search_parents = [uber_root]
            for _depth in range(3):
                next_parents = []
                for sp in search_parents:
                    sp_children = [c for c, p in location_parents.items() if p == sp]
                    for uc in sp_children:
                        uc_rank = TIER_ORDER.get(location_tiers.get(uc, "city"), 4)
                        if uc_rank >= orphan_rank or uc_rank < 2:
                            continue
                        # Skip realm/fantasy locations (幻/梦/仙/灵/冥/虚/魔)
                        # — they should not adopt real-world orphans
                        if any(kw in uc for kw in "幻梦仙灵冥虚魔"):
                            continue
                        desc = sum(1 for c, p in location_parents.items() if p == uc)
                        if desc > dominant_desc:
                            dominant = uc
                            dominant_desc = desc
                        if desc > 0:
                            next_parents.append(uc)
                search_parents = next_parents
                if not search_parents:
                    break
            if dominant and dominant_desc >= 2:
                if _safe_set_parent(orphan, dominant, location_parents,
                                    f"tiered-dominant:{orphan}→{dominant}"):
                    matched = True
                    tiered_matches += 1
                    adopted += 1

        # D.5: Try saved parent first, then uber_root (tier-gated)
        if not matched and uber_root is not None:
            # Prefer saved parent over uber_root to avoid regressions
            saved_ok = False
            _realm_kw = "幻梦仙灵冥虚魔"
            if saved_parents and not any(kw in orphan for kw in _realm_kw):
                old_p = saved_parents.get(orphan)
                if old_p and old_p != uber_root and old_p in all_known:
                    if old_p in location_parents or any(
                        p == old_p for p in location_parents.values()
                    ):
                        if _safe_set_parent(orphan, old_p, location_parents,
                                            f"catchall-saved:{orphan}→{old_p}"):
                            adopted += 1
                            saved_ok = True
            if not saved_ok and orphan_rank <= 4:  # city 及以上才直接挂天下
                if _safe_set_parent(orphan, uber_root, location_parents,
                                    f"catchall-adopt:{orphan}"):
                    adopted += 1
            # site/building 孤儿留为独立根（优于污染天下子节点）

    if adopted:
        logger.info(
            "Tiered catch-all: %d adopted (%d via intermediate matching, %d to %s)",
            adopted, tiered_matches, adopted - tiered_matches, uber_root,
        )
    return adopted


def _is_foreign_novel(location_tiers: dict[str, str]) -> bool:
    """Detect if locations suggest a foreign (non-Chinese) novel.

    Heuristic: if ≥3 location names match known foreign place patterns
    (entries in _SUPPLEMENT_GEO — US states, countries, world cities, etc.)
    and 0 locations match _PREFECTURE_TO_PROVINCE (Chinese historical geography),
    the novel is likely foreign (e.g., a translated Western novel).
    """
    from src.services.geo_resolver import _SUPPLEMENT_GEO

    location_names = set(location_tiers.keys())
    foreign_count = sum(1 for n in location_names if n in _SUPPLEMENT_GEO)
    chinese_count = sum(1 for n in location_names if n in _PREFECTURE_TO_PROVINCE)

    is_foreign = foreign_count >= 3 and chinese_count == 0
    if is_foreign:
        logger.info(
            "_is_foreign_novel: detected foreign novel "
            "(foreign_matches=%d, chinese_matches=%d)",
            foreign_count, chinese_count,
        )
    return is_foreign


def consolidate_hierarchy(
    location_parents: dict[str, str],
    location_tiers: dict[str, str],
    novel_genre_hint: str | None = None,
    parent_votes: dict[str, Counter] | None = None,
    saved_parents: dict[str, str] | None = None,
    synonym_pairs: list[tuple[str, str]] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Consolidate location hierarchy to reduce roots to single digits.

    Args:
        location_parents: Current child → parent mapping (modified in place and returned).
        location_tiers: Current location → tier mapping (modified in place and returned).
        novel_genre_hint: Genre of the novel (historical/wuxia/fantasy/etc.).
        parent_votes: Vote counters for diagnostics (optional, read-only).
        saved_parents: Previously saved parent mapping (optional). Used as fallback
            for orphan roots — prefer saved parent over 天下 to prevent regressions.
        synonym_pairs: List of (canonical, alias) pairs from macro skeleton.
            Alias locations are merged into canonical: children transferred, alias removed.

    Returns:
        Updated (location_parents, location_tiers) tuple.
    """
    genre = novel_genre_hint or "unknown"

    all_known = set(location_tiers.keys())
    changes_made = 0

# ── Step 0: Break any pre-existing cycles ──
    # Cycles can persist from earlier versions or edge cases in _resolve_parents.
    # Walk each parent chain; if we revisit a node, break the weakest edge.
    checked: set[str] = set()
    cycles_broken = 0
    for start in list(location_parents):
        if start in checked:
            continue
        visited: list[str] = []
        visited_set: set[str] = set()
        node = start
        while node in location_parents and node not in visited_set:
            visited.append(node)
            visited_set.add(node)
            node = location_parents[node]
        checked.update(visited_set)
        if node in visited_set:
            # Found a cycle — collect edges in the cycle and remove the weakest
            cycle_edges: list[tuple[str, str, int]] = []
            cur = node
            while True:
                parent = location_parents[cur]
                vote_count = (
                    parent_votes.get(cur, Counter()).get(parent, 0)
                    if parent_votes
                    else 0
                )
                cycle_edges.append((cur, parent, vote_count))
                cur = parent
                if cur == node:
                    break
            weakest = min(cycle_edges, key=lambda e: e[2])
            del location_parents[weakest[0]]
            cycles_broken += 1
    if cycles_broken:
        logger.info("Broke %d pre-existing cycle(s) in location_parents", cycles_broken)

    # ── Step 0.5: Merge synonym locations ──
    # When the macro skeleton identifies two hub names as synonyms (e.g.,
    # 神京/都中), merge the alias into the canonical name: transfer all
    # children, update all parent references, remove alias from tiers.
    if synonym_pairs:
        for canonical, alias in synonym_pairs:
            if canonical not in all_known or alias not in all_known:
                continue
            if canonical == alias:
                continue
            # Transfer children: any node whose parent is alias → canonical
            for child, parent in list(location_parents.items()):
                if parent == alias:
                    location_parents[child] = canonical
            # If alias itself has a parent, remove that entry
            location_parents.pop(alias, None)
            # If canonical had alias as parent, remove it
            if location_parents.get(canonical) == alias:
                del location_parents[canonical]
            # Remove alias from tiers (keep canonical's tier)
            location_tiers.pop(alias, None)
            all_known.discard(alias)
            changes_made += 1
            logger.info("Synonym merge: %s → %s", alias, canonical)

# Snapshot input parents for oscillation damping at the end
    input_parents = dict(location_parents)

    # Import TIER_ORDER and _get_suffix_rank once
    from src.services.world_structure_agent import TIER_ORDER, _get_suffix_rank

    # ── Genre-specific: Chinese geography steps (Steps 1-2) ──
    # Province tier fixes and province inversion fixes only apply to
    # genres where Chinese geography is relevant.
    is_foreign = _is_foreign_novel(location_tiers)
    skip_chinese_geo = genre in ("fantasy", "urban") or is_foreign

    if not skip_chinese_geo:
        # ── Step 1: Fix province tiers first ──
        for prov in _PROVINCES:
            if prov in location_tiers:
                location_tiers[prov] = _PROVINCE_TIER

    # ── Step 2: Fix inverted province relationships ──
    # (Only for Chinese-geography genres)
    if not skip_chinese_geo:
        for prov in list(_PROVINCES):
            if prov not in location_parents:
                continue
            current_parent = location_parents[prov]
            if current_parent not in _PROVINCES and current_parent != "天下":
                parent_tier = location_tiers.get(current_parent, "city")
                parent_rank = TIER_ORDER.get(parent_tier, 4)
                if parent_rank > TIER_ORDER.get(_PROVINCE_TIER, 1):
                    wrong_parent = current_parent
                    del location_parents[prov]
                    if wrong_parent not in location_parents:
                        _safe_set_parent(
                            wrong_parent, prov, location_parents,
                            f"province inversion fix: {prov} was child of {wrong_parent}",
                        )
                    changes_made += 1

    # ── Step 2b: Fix tier inversions in the full hierarchy (ALL genres) ──
    # Buildings parenting cities, rooms parenting regions — wrong in ANY genre.
    # Uses suffix rank (primary) and tier order (fallback) for direction detection.
    # Example: "大尉府" (building) → child "东京" (city) → reversed!
    #
    # IMPORTANT: Collect all fixes first, then apply in batch to avoid
    # race conditions where one fix undoes another during iteration.
    inversion_fixes: list[tuple[str, str]] = []  # (child, parent) pairs to reverse
    for child, parent in list(location_parents.items()):
        if not skip_chinese_geo and (parent in _PROVINCES or parent == "天下"):
            continue

        should_fix = False

        # Conservative inversion detection: only compare like-with-like.
        # Suffix rank is unreliable across types (e.g., 府 = prefecture OR manor),
        # so mixing suffix rank with tier rank causes false inversions.
        child_suf = _get_suffix_rank(child)
        parent_suf = _get_suffix_rank(parent)
        if child_suf is not None and parent_suf is not None:
            # Both have suffix → high-confidence comparison
            if parent_suf > child_suf:
                should_fix = True
        elif child_suf is None and parent_suf is None:
            # Neither has suffix → use tier comparison
            child_rank = TIER_ORDER.get(location_tiers.get(child, "city"), 4)
            parent_rank = TIER_ORDER.get(location_tiers.get(parent, "city"), 4)
            if parent_rank > child_rank:
                should_fix = True
            elif parent_rank == child_rank and _is_sub_location_name(parent) and _is_geographic_name(child):
                should_fix = True
        # Mixed (one has suffix, one doesn't): skip — ambiguous, let
        # _resolve_parents's vote-based result stand.

        if should_fix:
            inversion_fixes.append((child, parent))

    # Apply fixes: for each inverted pair, free the child and optionally reverse
    inversions_fixed = 0
    for child, parent in inversion_fixes:
        # Verify the relationship still exists (may have been modified by earlier fix)
        if location_parents.get(child) != parent:
            continue
        del location_parents[child]
        # Only reverse (make old-parent a child of old-child) if old-parent
        # doesn't already have its own parent. Otherwise we'd OVERWRITE a
        # correct parent assignment (e.g., 大观园→荣国府 overwritten by
        # 大观园→甬道 when fixing 甬道→大观园 inversion).
        if parent not in location_parents:
            if _safe_set_parent(parent, child, location_parents,
                                f"tier-inversion: {parent} was parent of {child}"):
                inversions_fixed += 1
                changes_made += 1
            else:
                changes_made += 1
        else:
            changes_made += 1

    if inversions_fixed:
        logger.info("Fixed %d tier inversions", inversions_fixed)

    # ── Step 2c: Rescue remaining noise roots ──
    # Some roots are clearly sub-locations (粪窖边, 后门, etc.) that shouldn't
    # be roots. If they have geographic children, reverse the relationship.
    roots = _get_roots(location_parents)
    for root in list(roots):
        if root == "天下" or root in _PROVINCES:
            continue
        if not _is_sub_location_name(root):
            continue

        # Find direct children of this root
        direct_children = [c for c, p in location_parents.items() if p == root]
        if not direct_children:
            continue

        # Find the best child to become the new parent
        # Prefer children with geographic names and higher tiers
        best_child = None
        best_rank = 999
        for c in direct_children:
            c_tier = location_tiers.get(c, "city")
            c_rank = TIER_ORDER.get(c_tier, 4)
            if _is_geographic_name(c) and c_rank < best_rank:
                best_child = c
                best_rank = c_rank
            elif best_child is None and c_rank < best_rank:
                best_child = c
                best_rank = c_rank

        if best_child is not None:
            # Move all children except best_child to be children of best_child
            for c in direct_children:
                if c == best_child:
                    continue
                location_parents[c] = best_child
            # Remove best_child→root and make root→best_child
            del location_parents[best_child]
            if _safe_set_parent(root, best_child, location_parents,
                                f"noise-root-rescue: {root} → child of {best_child}"):
                changes_made += 1
            else:
                # Just orphan the root — it's noise
                pass

    # ── Oscillation damping ──
    # Detect direction flips: if A→B in input became B→A after consolidation,
    # and suffix rank doesn't clearly justify the flip, revert it.
    # This prevents infinite oscillation for ambiguous pairs (e.g., 后宫↔宫廷).
    damped = 0
    for child, parent in list(location_parents.items()):
        # Check if this is a flip of an input relationship
        if input_parents.get(parent) == child:
            # Input had parent→child (parent was child of child)
            # Now we have child→parent — this is a direction flip
            child_suf = _get_suffix_rank(child)
            parent_suf = _get_suffix_rank(parent)
            # Only keep the flip if suffix rank clearly justifies it
            if child_suf is not None and parent_suf is not None and parent_suf < child_suf:
                continue  # Flip is justified: parent is bigger by suffix rank
            # Check tier as secondary signal
            child_tier = location_tiers.get(child, "city")
            parent_tier = location_tiers.get(parent, "city")
            child_rank = TIER_ORDER.get(child_tier, 4)
            parent_rank = TIER_ORDER.get(parent_tier, 4)
            if parent_rank < child_rank:
                continue  # Flip is justified: parent has clearly bigger tier
            # Not clearly justified — revert to input direction
            del location_parents[child]
            if _safe_set_parent(parent, child, location_parents, "oscillation-damp"):
                damped += 1
            else:
                # Can't revert (cycle) — just remove the relationship
                damped += 1
    if damped:
        logger.info("Oscillation damping: reverted %d ambiguous flips", damped)

    # ── Steps 3-11: Chinese geography-specific consolidation ──
    # Only applies to genres where Chinese geography is relevant.
    if skip_chinese_geo:
        if is_foreign:
            # Foreign novel: skip 天下 (Chinese concept), just run tiered catch-all
            # with no uber_root so orphans only get adopted via prefix matching.
            catchall_adopted = _tiered_catchall(
                all_known, None, location_parents, location_tiers,
            )
        else:
            # Fantasy/urban: use 天下 catch-all as before
            uber_root = "天下"
            if uber_root not in location_tiers:
                location_tiers[uber_root] = _ROOT_TIER
            catchall_adopted = _tiered_catchall(
                all_known, uber_root, location_parents, location_tiers,
            )
        changes_made += catchall_adopted

        final_roots = _get_roots(location_parents)
        logger.info(
            "Hierarchy consolidation (genre=%s, foreign=%s): %d generic fixes, "
            "%d final roots, skipped Chinese geo steps",
            genre, is_foreign, changes_made, len(final_roots),
        )
        return location_parents, location_tiers

    # ── Step 3: Parse compound names ──
    # "山东济州" → create 山东 > 山东济州 hierarchy
    for name in list(all_known):
        parsed = _parse_compound_name(name)
        if parsed is None:
            continue
        province, local_part = parsed

        # Ensure province exists in tiers
        if province not in location_tiers:
            location_tiers[province] = _PROVINCE_TIER
            all_known.add(province)

        # Connect compound name to province
        if name not in location_parents:
            if _safe_set_parent(name, province, location_parents, "compound"):
                changes_made += 1

        # If the local_part also exists standalone, connect it too
        if local_part in all_known and local_part not in location_parents:
            if _safe_set_parent(local_part, province, location_parents, "compound-local"):
                changes_made += 1

    # ── Step 4: Parse location suffixes ──
    # "东京城外" → parent = "东京", "济州城下" → parent = "济州"
    for name in list(all_known):
        if name in location_parents:
            continue
        parsed = _parse_location_suffix(name, all_known)
        if parsed is not None:
            base, _ = parsed
            if _safe_set_parent(name, base, location_parents, "suffix"):
                changes_made += 1

    # ── Step 4b: Parse variant names ──
    # "苏州城" → parent = "苏州", "孟州城" → parent = "孟州"
    for name in list(all_known):
        if name in location_parents:
            continue
        base = _parse_variant_name(name, all_known)
        if base is not None:
            if _safe_set_parent(name, base, location_parents, "variant"):
                changes_made += 1

    # ── Step 5: Bridge roots to provinces via geo table ──
    roots = _get_roots(location_parents)

    for root in list(roots):
        if root in _PROVINCES or root == "天下":
            continue

        # Try multiple lookup strategies
        province = None

        # 5a: Exact match in prefecture table
        province = _PREFECTURE_TO_PROVINCE.get(root)

        # 5b: Mountain/river tables
        if province is None:
            province = _MOUNTAINS_TO_PROVINCE.get(root)
        if province is None:
            province = _RIVERS_TO_PROVINCE.get(root)

        # 5c: Strip common suffixes for fuzzy match
        if province is None and len(root) >= 3:
            for suffix in ("府", "州", "县", "城"):
                if root.endswith(suffix):
                    base = root[:-1]
                    for alt_suffix in ("", "州", "府"):
                        alt = base + alt_suffix if alt_suffix else base
                        province = _PREFECTURE_TO_PROVINCE.get(alt)
                        if province:
                            break
                    if not province:
                        province = _MOUNTAINS_TO_PROVINCE.get(base)
                if province:
                    break

        # 5d: Try compound name parsing on the root
        if province is None:
            parsed = _parse_compound_name(root)
            if parsed:
                province = parsed[0]  # province prefix

        if province:
            if province not in location_tiers:
                location_tiers[province] = _PROVINCE_TIER
                all_known.add(province)
            if _safe_set_parent(root, province, location_parents, "geo-bridge"):
                changes_made += 1

    # ── Step 6: Connect provinces to uber-root ──
    uber_root = "天下"
    if uber_root not in location_tiers:
        location_tiers[uber_root] = _ROOT_TIER
        all_known.add(uber_root)
    else:
        location_tiers[uber_root] = _ROOT_TIER

    roots = _get_roots(location_parents)
    provinces_connected = 0
    for root in list(roots):
        if root == uber_root:
            continue
        if root in _PROVINCES:
            location_parents[root] = uber_root
            provinces_connected += 1
            changes_made += 1

    # ── Step 7: Connect foreign states and remaining 国-suffix to uber-root ──
    roots = _get_roots(location_parents)
    for root in list(roots):
        if root == uber_root:
            continue
        if root.endswith("国"):
            location_parents[root] = uber_root
            if root not in location_tiers or location_tiers[root] not in ("world", "continent"):
                location_tiers[root] = "kingdom"
            changes_made += 1

    # ── Step 8: Prefix matching — connect roots starting with known prefectures ──
    # "青州地面" starts with "青州" → connect to 山东
    # "济州梁山泊边" starts with "济州" → connect to 山东
    # "扬州城外" starts with "扬州" → connect to 两浙
    roots = _get_roots(location_parents)
    # Build sorted lookup (longest match first to avoid partial matches)
    _all_geo_keys = sorted(
        list(_PREFECTURE_TO_PROVINCE.keys())
        + list(_MOUNTAINS_TO_PROVINCE.keys())
        + list(_RIVERS_TO_PROVINCE.keys()),
        key=len, reverse=True,
    )
    for root in list(roots):
        if root == uber_root or root in _PROVINCES:
            continue
        for geo_key in _all_geo_keys:
            if root.startswith(geo_key) and len(root) > len(geo_key):
                province = (
                    _PREFECTURE_TO_PROVINCE.get(geo_key)
                    or _MOUNTAINS_TO_PROVINCE.get(geo_key)
                    or _RIVERS_TO_PROVINCE.get(geo_key)
                )
                if province:
                    if province not in location_tiers:
                        location_tiers[province] = _PROVINCE_TIER
                    if _safe_set_parent(root, province, location_parents, f"prefix-match:{geo_key}"):
                        changes_made += 1
                        break

    # ── Step 9: Second pass — try to connect remaining roots ──
    roots = _get_roots(location_parents)
    for root in list(roots):
        if root == uber_root:
            continue

        # Try geo table again (some roots changed in previous steps)
        province = _PREFECTURE_TO_PROVINCE.get(root)
        if province is None:
            province = _MOUNTAINS_TO_PROVINCE.get(root)
        if province is None:
            province = _RIVERS_TO_PROVINCE.get(root)

        if province and province in location_parents:
            if _safe_set_parent(root, province, location_parents, "second-pass"):
                changes_made += 1
                continue

        # Try suffix parsing against all current locations (not just all_known)
        current_all = set(location_parents.keys()) | set(location_parents.values())
        parsed = _parse_location_suffix(root, current_all)
        if parsed is not None:
            base, _ = parsed
            if _safe_set_parent(root, base, location_parents, "second-pass-suffix"):
                changes_made += 1
                continue

        base = _parse_variant_name(root, current_all)
        if base is not None:
            if _safe_set_parent(root, base, location_parents, "second-pass-variant"):
                changes_made += 1

    # ── Step 9b: Ensure known geographic locations are under correct provinces ──
    # This is the MOST IMPORTANT fix: after all tier inversion fixes and geo bridging,
    # check that locations in _PREFECTURE_TO_PROVINCE / _MOUNTAINS / _RIVERS are
    # actually under their correct province. If not (e.g., 东京 is still under 大尉府),
    # reparent them.
    all_geo_lookups = {
        **_PREFECTURE_TO_PROVINCE,
        **_MOUNTAINS_TO_PROVINCE,
        **_RIVERS_TO_PROVINCE,
    }
    geo_rescues = 0
    for loc_name, expected_province in all_geo_lookups.items():
        if loc_name not in location_parents and loc_name not in all_known:
            continue  # Location doesn't exist in this novel
        if loc_name not in location_parents:
            # Location exists but has no parent — connect to province
            if expected_province not in location_tiers:
                location_tiers[expected_province] = _PROVINCE_TIER
            if _safe_set_parent(loc_name, expected_province, location_parents, "geo-rescue-orphan"):
                geo_rescues += 1
                changes_made += 1
            continue

        # Check if location is already correctly placed (under its province somewhere)
        current = loc_name
        visited = {current}
        is_under_correct = False
        while current in location_parents:
            current = location_parents[current]
            if current in visited:
                break
            visited.add(current)
            if current == expected_province:
                is_under_correct = True
                break

        if is_under_correct:
            continue  # Already correctly placed

        # Location is NOT under its expected province. Reparent it.
        # First, save current parent and children so we can reconnect them
        old_parent = location_parents[loc_name]

        # Ensure province exists
        if expected_province not in location_tiers:
            location_tiers[expected_province] = _PROVINCE_TIER
        if expected_province not in location_parents:
            # Province not yet connected to uber_root
            _safe_set_parent(expected_province, uber_root, location_parents, "geo-rescue-province")

        # Reparent: remove from old parent, connect to province
        del location_parents[loc_name]
        if _safe_set_parent(loc_name, expected_province, location_parents, f"geo-rescue:{loc_name}→{expected_province}"):
            geo_rescues += 1
            changes_made += 1
            # Reconnect old_parent and its remaining children to this location.
            # The old_parent was likely a sub-location of loc_name
            # (e.g., 大尉府 was in 东京, 梁中书府 was in 北京).
            if old_parent != uber_root and old_parent not in _PROVINCES:
                # Move old_parent's remaining children to loc_name
                old_parent_children = [c for c, p in location_parents.items() if p == old_parent]
                for orphan in old_parent_children:
                    if orphan != loc_name:
                        location_parents[orphan] = loc_name
                # Make old_parent a child of loc_name
                _safe_set_parent(old_parent, loc_name, location_parents, f"geo-rescue-adopt:{old_parent}→{loc_name}")
        else:
            # Can't reparent (cycle), restore old
            location_parents[loc_name] = old_parent

    if geo_rescues:
        logger.info("Geo-rescued %d locations to correct provinces", geo_rescues)


    # Helper: try saved parent before falling back to uber_root
    _REALM_KW = "幻梦仙灵冥虚魔"

    def _try_saved_or_uber(orphan: str, reason: str) -> bool:
        """Try to reconnect orphan to its saved parent; fall back to uber_root."""
        nonlocal changes_made
        if saved_parents and not any(kw in orphan for kw in _REALM_KW):
            old_p = saved_parents.get(orphan)
            if old_p and old_p != uber_root and old_p in all_known:
                # Check the saved parent itself is in the hierarchy
                # (either has a parent or IS the uber_root's child)
                if old_p in location_parents or any(
                    p == old_p for p in location_parents.values()
                ):
                    if _safe_set_parent(orphan, old_p, location_parents,
                                        f"saved-fallback:{reason}"):
                        changes_made += 1
                        return True
        if _safe_set_parent(orphan, uber_root, location_parents, reason):
            changes_made += 1
            return True
        return False

    # ── Step 10: Connect large-subtree roots first (before geographic roots) ──
    # Large subtree roots (e.g., 荣国府 with many children) establish anchor
    # points in the hierarchy. Running this BEFORE geographic root connection
    # ensures intermediate parents (like 都中) are available for saved_parents
    # fallback in subsequent steps.
    roots = _get_roots(location_parents)
    for root in list(roots):
        if root == uber_root or root in _PROVINCES:
            continue
        # Count descendants
        desc_count = 0
        queue = [root]
        while queue:
            node = queue.pop()
            for c, p in location_parents.items():
                if p == node:
                    desc_count += 1
                    queue.append(c)
                    if desc_count > 5:
                        break
            if desc_count > 5:
                break
        # Only connect roots with substantial subtrees
        if desc_count >= 5:
            _try_saved_or_uber(root, "large-subtree-to-root")

    # ── Step 11: Connect remaining geographic roots to uber-root ──
    # Any root with a proper geographic name that couldn't be mapped to a
    # province gets connected directly to 天下 rather than being an orphan root.
    # Runs AFTER large-subtree step so saved_parents fallback can find
    # intermediate parents (e.g., 都中) established in the previous step.
    roots = _get_roots(location_parents)
    for root in list(roots):
        if root == uber_root:
            continue
        if _is_geographic_name(root) and not _is_sub_location_name(root):
            _try_saved_or_uber(root, "geo-to-root")

    # ── Step 12: Tiered catch-all — adopt orphan nodes with intermediate matching ──
    catchall_adopted = _tiered_catchall(
        all_known, uber_root, location_parents, location_tiers,
        saved_parents=saved_parents,
    )
    changes_made += catchall_adopted

    # ── Final stats ──
    final_roots = _get_roots(location_parents)
    logger.info(
        "Hierarchy consolidation: %d changes, %d provinces connected, "
        "%d final roots (of %d before)",
        changes_made, provinces_connected, len(final_roots), 148,
    )
    if len(final_roots) <= 20:
        logger.info("Final roots: %s", sorted(final_roots))

    return location_parents, location_tiers
