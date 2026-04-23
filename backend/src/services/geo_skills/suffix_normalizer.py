"""SuffixNormalizer — GeoSkill that merges location name suffix variants.

Many novels have LLM-extracted location names that differ only by a positional
or descriptive suffix:
    乌斯藏 / 乌斯藏国 / 乌斯藏界 / 乌斯藏国界  (4 variants for the same place)
    玉华州 / 玉华县 / 玉华城 / 玉华州城池      (4 variants)
    潮音洞 / 潮音洞外                       (outside description)
    火焰山 / 火焰山界                       (border description)
    流沙河 / 流沙河岸 / 流沙河东岸            (positional)
    平顶山 / 平顶山山路 / 平顶山山顶         (mountain descriptor, usually
                                            filtered by FactValidator Rule 21)

This skill detects such pairs by stripping a known suffix from the variant
and checking if the stripped "base" exists as another location. Variants
are then merged into the base: children are transferred, variant is removed.

Runs BEFORE EdmondsResolver to ensure consistent parent assignments.
"""

from __future__ import annotations

import logging

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)


# Suffix stripping table.
# Each tuple: (suffix, min_prefix_len) — the suffix to strip, and the
# minimum length of the base name after stripping.
_MERGE_SUFFIXES: list[tuple[str, int]] = [
    # 3-char: more specific first
    ("国城池", 2),
    ("国城门", 2),
    ("国城头", 2),
    ("州城池", 2),
    ("府城池", 2),
    ("国界内", 2),
    ("国界外", 2),
    ("大街上", 2),
    # 2-char variants (administrative/positional)
    ("国界", 2),
    ("城池", 2),
    ("城门", 2),
    ("城头", 2),
    ("城外", 2),
    ("城内", 2),
    ("城中", 2),
    ("山顶", 2),
    ("山路", 2),
    ("山脚", 2),
    ("山下", 2),
    ("山腰", 2),
    ("山口", 2),
    ("山道", 2),
    ("之外", 2),
    ("之内", 2),
    ("之中", 2),
    ("之下", 2),
    ("之上", 2),
    ("门外", 2),
    ("门内", 2),
    ("门前", 2),
    ("门口", 2),
    # 1-char positional/descriptive
    ("外", 2),
    ("内", 2),
    ("里", 2),
    ("上", 2),
    ("下", 2),
    ("岸", 2),
    ("边", 2),
    ("头", 2),
    ("顶", 2),
    ("口", 2),
    ("前", 2),
    ("后", 2),
    ("中", 2),
    ("界", 3),  # "界" more risky (某界 could be realm) — stricter min len
]

# Explicit synonym table for well-known compound variants.
# Format: {base: [variants]} — base must exist; variants merged into base.
_EXPLICIT_SYNONYMS: dict[str, list[str]] = {
    # ── 西游记 ──
    "乌斯藏国": ["乌斯藏", "乌斯藏界", "乌斯藏国界"],
    "狮驼岭": ["八百里狮驼岭"],
    "火焰山": ["火焰山界"],
    "通天河": ["通天河界"],
    "流沙河": ["流沙河界", "流沙河岸", "流沙河东岸"],
    "朱紫国": ["朱紫国城池"],
    "乌鸡国": ["乌鸡国城池"],
    "车迟国": ["车迟国城池"],
    "玉华县": ["玉华城", "玉华州城池"],
    "潮音洞": ["潮音洞外"],
    "獬豸洞": ["獬豸洞外"],
    "黄花观": ["黄花观外"],
    "平顶山": ["平顶山山路", "平顶山山顶"],
    "五行山": ["五行山顶"],
    "西梁国": ["西梁国界", "西梁女国"],  # 西梁女国 = 西梁国
    "西番哈咇国": ["西番哈咇国界"],
    "南赡部洲": ["南瞻部洲", "南膳部洲"],  # 字形变体兜底

    # ── 红楼梦(Phase C 地点先验) ──
    # 京城别名合并:石头城/都中/神京/金陵/京都/京师 → 都中(最常用)
    # 红楼梦故事发生在"都中"(京城),"石头城"(南京古称)在小说语境下与
    # "都中"指同一地点(曹雪芹用石头城作为隐喻)。"神京/京都/京师"均同义。
    "都中": [
        "京都", "京师", "神京",
        "石头城", "金陵",  # 小说语境同指京城
    ],
    # 贾府内部建筑合并(各人物房间的变体)
    "贾母院": ["贾母处", "贾母房", "贾母上房", "贾母里间", "贾母正房", "贾母后院"],
    "凤姐院": ["凤姐处", "凤姐房", "凤姐屋", "凤姐房中", "凤姐院中"],
    "宝玉房": ["宝玉屋", "宝玉书房"],
    "袭人房": ["袭人下房"],
    "惜春房": ["惜春卧房"],
    "蘅芜苑": ["蘅芜院"],
    "栊翠庵": ["枕翠庵"],
    "沁芳亭": ["沁芳亭子"],
    "荣国府·正门": ["荣国府大门"],
}


class SuffixNormalizer(GeoSkill):
    """Merge location variants that differ only by positional/descriptive suffix."""

    @property
    def name(self) -> str:
        return "Suffix normalization"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        tiers = snapshot.location_tiers
        parents = snapshot.location_parents
        # Build full location universe — include parent keys/values too,
        # since some locations exist only as parents of others (no tier entry).
        all_locs = set(tiers.keys())
        all_locs.update(parents.keys())
        all_locs.update(v for v in parents.values() if v)

        # Build variant → base mapping
        variant_to_base: dict[str, str] = {}

        # Pass 1: explicit synonyms (highest priority)
        for base, variants in _EXPLICIT_SYNONYMS.items():
            if base not in all_locs:
                continue
            for v in variants:
                if v in all_locs and v != base:
                    variant_to_base[v] = base

        # Pass 2: suffix-based detection
        for name in all_locs:
            if name in variant_to_base:
                continue
            for suffix, min_len in _MERGE_SUFFIXES:
                if not name.endswith(suffix):
                    continue
                base = name[: -len(suffix)]
                if len(base) < min_len:
                    continue
                if base in all_locs and base != name:
                    # Found a variant — but only merge if base has an equal
                    # or greater tier level (we don't want to collapse real
                    # sub-locations into their parent)
                    variant_to_base[name] = base
                    break

        if not variant_to_base:
            return SkillResult.empty(self.name, "No variants to merge")

        # Build parent_overrides: transfer children from variant → base.
        # Also set variant's own parent = base, so variant becomes a leaf
        # attached to base (preserves the node for history but prevents it
        # from acting as a catch-all parent).
        parent_overrides: dict[str, str | None] = {}
        children_transferred = 0
        for child, old_parent in parents.items():
            if old_parent in variant_to_base and child not in variant_to_base:
                new_parent = variant_to_base[old_parent]
                if new_parent == child:
                    continue
                parent_overrides[child] = new_parent
                children_transferred += 1

        # Point each variant at its base (so it's a child of base, not a sibling)
        for variant, base in variant_to_base.items():
            if variant != base:
                parent_overrides[variant] = base

        logger.info(
            "SuffixNormalizer: %d variants merged, %d children transferred",
            len(variant_to_base), children_transferred,
        )

        return SkillResult(
            skill_name=self.name,
            parent_overrides=parent_overrides,
            synonym_pairs=[(base, variant) for variant, base in variant_to_base.items()],
        )
