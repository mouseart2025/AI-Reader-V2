"""TierClassifier — GeoSkill that re-classifies location tiers.

Two-phase classification:
  Phase 1 (suffix/type): delegates to WorldStructureAgent._classify_tier
  Phase 2 (multi-feature refinement): Phase 2a from errata analysis —
    uses mention_count, children_count, parent tier coherence to fix
    systematic errors.

This skill always succeeds (no LLM dependency).
"""

from __future__ import annotations

import logging

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)

# Tier rank: smaller = higher/bigger geographic entity
_TIER_RANK = {
    "world": 0, "realm": 1, "continent": 2, "kingdom": 3,
    "city": 4, "region": 5, "site": 6, "building": 7,
}

# ── Dynasty/era detection for "州" classification ──────────────────
# 州 (zhōu) changed administrative scale across Chinese history:
#   三国/汉 (Sanguo/Han): 州 = province (kingdom-level, e.g. 荆州, 益州)
#   商周/封神 (Shang-Zhou): 州 = feudal territory (region/city, e.g. 冀州)
#   宋 (Song/Shuihu): 州 = prefecture (city-level, e.g. 沧州, 孟州)
#   明清 (Ming-Qing/Honglou): 州 = county-level city (e.g. 苏州, 扬州)
#   Fantasy/Xianxia: 州 = large realm (kingdom-level)
_ERA_KEYWORDS: dict[str, list[str]] = {
    # Mix of person names + distinctive location names for robust detection.
    # Person names may appear in location names (e.g. 曹操营, 刘备府).
    # Location names provide direct matching against the location set.
    "sanguo": [
        # Distinctive locations
        "赤壁", "官渡", "许昌", "许都", "建业", "白帝城", "街亭",
        "五丈原", "麦城", "新野", "樊城", "襄阳", "长坂",
        # Person-derived locations (曹操寨, 袁绍营, etc.)
        "曹操", "刘备", "孙权", "诸葛亮", "吕布", "董卓", "袁绍",
        # Faction names that appear in locations
        "蜀汉", "东吴", "曹魏",
    ],
    "shangzhou": [
        # Distinctive locations
        "西岐", "朝歌", "汜水关", "临潼关", "佳梦关", "界牌关",
        "穿云关", "青龙关", "金鸡岭", "万仙阵",
        # Person names that may appear in locations
        "纣王", "妲己", "姜子牙", "闻太师", "黄飞虎",
        # Faction/concept keywords
        "封神", "截教", "阐教", "西伯侯",
    ],
}

def _detect_era(genre: str, location_names: set[str]) -> str:
    """Detect historical era from genre + location name evidence.

    Era keywords take priority over genre because a novel can be genre=fantasy
    but set in a specific historical era (e.g. 封神演义 = fantasy + 商周).

    Returns: 'sanguo' | 'shangzhou' | 'fantasy' | 'historical' | 'default'
    """
    # Check era keywords first — specific era overrides genre
    for era, keywords in _ERA_KEYWORDS.items():
        hits = sum(1 for kw in keywords if any(kw in name for name in location_names))
        if hits >= 2:
            return era
    if genre == "fantasy":
        return "fantasy"
    if genre in ("historical", "wuxia"):
        return "historical"
    return "default"


def _zhou_target_tier(era: str) -> str:
    """Get target tier for "州" suffix based on detected era."""
    if era == "sanguo":
        return "kingdom"   # 三国: 州 = province (荆州/益州/徐州)
    if era == "fantasy":
        return "kingdom"   # Fantasy: 州 = large realm
    # All other eras: 州 = city-level
    return "city"


class TierClassifier(GeoSkill):
    """Re-classify location tiers: suffix + multi-feature refinement."""

    def __init__(self, novel_id: str):
        self._novel_id = novel_id

    @property
    def name(self) -> str:
        return "层级分类"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.services.world_structure_agent import WorldStructureAgent
        from src.db import world_structure_store

        ws = await world_structure_store.load(self._novel_id)
        if not ws:
            return SkillResult.empty(self.name, "WorldStructure not found")

        agent = WorldStructureAgent(self._novel_id)
        agent.structure = ws
        ws.location_tiers = dict(snapshot.location_tiers)

        # ── Phase 1: Suffix/type-based classification (existing) ──
        tier_updates: dict[str, str] = {}
        for loc_name, old_tier in snapshot.location_tiers.items():
            parent = snapshot.location_parents.get(loc_name)
            level = 1 if parent else 0
            new_tier = agent._classify_tier(loc_name, "", parent, level)
            if new_tier != old_tier:
                tier_updates[loc_name] = new_tier

        # ── Phase 2: Multi-feature refinement (Phase 2a errata-driven) ──
        # Effective tiers after Phase 1
        effective_tiers = dict(snapshot.location_tiers)
        effective_tiers.update(tier_updates)

        # Compute children counts from current parents
        children_count: dict[str, int] = {}
        for _, p in snapshot.location_parents.items():
            if p:
                children_count[p] = children_count.get(p, 0) + 1

        # Detect era for dynasty-aware suffix classification
        genre = snapshot.novel_genre_hint or ""
        era = _detect_era(genre, set(snapshot.location_tiers.keys()))

        refinement_updates = self._multi_feature_refine(
            tiers=effective_tiers,
            parents=snapshot.location_parents,
            frequencies=snapshot.location_frequencies,
            children_count=children_count,
            era=era,
        )

        # Merge refinements into tier_updates
        refine_changed = 0
        for name, new_tier in refinement_updates.items():
            old = effective_tiers.get(name)
            if new_tier != old:
                tier_updates[name] = new_tier
                refine_changed += 1

        result = SkillResult(
            skill_name=self.name,
            tier_updates=tier_updates,
        )
        if tier_updates:
            logger.info(
                "TierClassifier: %d total tier changes (%d from refinement); e.g., %s",
                len(tier_updates), refine_changed,
                ", ".join(f"{k}:{v}" for k, v in list(tier_updates.items())[:3]),
            )
        return result

    @staticmethod
    def _multi_feature_refine(
        tiers: dict[str, str],
        parents: dict[str, str],
        frequencies,  # Counter
        children_count: dict[str, int],
        era: str = "default",
    ) -> dict[str, str]:
        """Apply multi-signal adjustments based on errata analysis.

        Signals:
          1. 父子tier一致性: 子节点tier rank < 父节点 → demote to site
          2. 零证据高tier: mc=0 且 tier ∈ {continent,kingdom,realm} → site
          3. 单次提及叶节点: mc=1, children=0, tier ≥ region → site
          4. 强证据提升: mc≥30 且 children≥15 且 tier ∈ {site,building} → region
          5. X界/境界 修正: 后缀 界/境界 且 tier=city → region
          6. 人名/官职+府 修正: 后缀 府 且 tier=city 且 parent=region → site
          7. 朝代感知"州" 修正: 根据 era 重分类 X州 的 tier
        """
        updates: dict[str, str] = {}

        for name, tier in tiers.items():
            parent = parents.get(name)
            parent_tier = tiers.get(parent, "") if parent else ""
            mc = frequencies.get(name, 0)
            ch = children_count.get(name, 0)

            new_tier = tier

            # Rule 7: Dynasty-aware 州 reclassification
            # "部洲" (e.g. 南赡部洲) is handled by 2-char suffix → continent, skip.
            # Only apply to nodes with real evidence (mc≥2); zero/single-evidence
            # nodes fall through to Rules 1-3 for demotion.
            if (name.endswith("州") and len(name) >= 2
                    and not name.endswith("部洲") and mc >= 2):
                target = _zhou_target_tier(era)
                if tier != target:
                    new_tier = target

            # Rule 5: X界/境界 → region
            elif (name.endswith("界") or name.endswith("境界")) and tier == "city":
                new_tier = "region"

            # Rule 6: X府 + parent=region → site (residence, not administrative)
            elif name.endswith("府") and tier == "city" and parent_tier == "region":
                new_tier = "site"

            # Rule 1: Parent-child coherence (只处理严重违反情况)
            # 父节点是region/site/building, 子节点是continent/kingdom → 降为site
            elif parent_tier in ("region", "site", "building") and tier in ("continent", "kingdom", "realm"):
                new_tier = "site"

            # Rule 2: 零证据高tier
            elif mc == 0 and tier in ("continent", "kingdom", "realm"):
                new_tier = "site"

            # Rule 3: 单次提及叶节点
            elif mc == 1 and ch == 0 and tier in ("continent", "kingdom", "realm"):
                new_tier = "site"

            # Rule 4: 强证据提升 (保守: 需要同时满足高mc和高children)
            elif mc >= 30 and ch >= 15 and tier in ("site", "building"):
                new_tier = "region"

            if new_tier != tier:
                updates[name] = new_tier

        return updates
