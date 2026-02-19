"""WorldStructureAgent: signal scanning, heuristic updates, and LLM incremental updates.

Scans each chapter's raw text and extracted ChapterFact for world-building
signals (region divisions, layer transitions, instance entries, macro geography),
then applies lightweight keyword-based heuristics to assign locations to the
correct layer and region.

When trigger conditions are met, calls the LLM for deeper world-structure
analysis and applies incremental operations (ADD_REGION, ADD_LAYER, ADD_PORTAL,
ASSIGN_LOCATION, UPDATE_REGION, NO_CHANGE).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.db import world_structure_override_store, world_structure_store
from src.extraction.fact_validator import _is_generic_location
from src.infra.config import LLM_PROVIDER
from src.infra.llm_client import LLMClient, get_llm_client
from src.models.chapter_fact import ChapterFact
from src.services.location_hint_service import extract_direction_hint
from src.services.hierarchy_consolidator import consolidate_hierarchy
from collections import Counter

from src.models.world_structure import (
    LayerType,
    LocationIcon,
    LocationTier,
    MapLayer,
    Portal,
    WorldBuildingSignal,
    WorldRegion,
    WorldStructure,
)

logger = logging.getLogger(__name__)

# ── Signal detection keywords / patterns ─────────────────────────

# region_division
_REGION_DIV_KEYWORDS = ("分为", "划为")
_REGION_DIV_PATTERN = re.compile(
    r"(分|划)为[\d一二三四五六七八九十]+[大]?(部洲|大陆|界|域|国)"
)

# layer_transition
_LAYER_TRANS_KEYWORDS = ("上了天", "到天宫", "进了地府", "入冥界", "潜入海底")
_LAYER_TRANS_LOC_KEYWORDS = ("天宫", "天庭", "天界", "地府", "冥界", "海底", "龙宫")

# instance_entry
_INSTANCE_ENTRY_KEYWORDS = ("走进洞", "入洞", "进了洞", "进入阵")
_INSTANCE_TYPE_PATTERN = re.compile(r"(洞|府|宫|阵|秘境|幻境|禁地)")

# macro_geography — location types that indicate macro-level places
_MACRO_GEO_SUFFIXES = ("洲", "域", "界", "国")

# ── Heuristic layer-assignment keywords ──────────────────────────

_CELESTIAL_KEYWORDS = (
    "天宫", "天庭", "天门", "天界", "三十三天", "大罗天",
    "离恨天", "兜率宫", "凌霄殿", "蟠桃园", "瑶池",
    "灵霄宝殿", "南天门", "北天门", "东天门", "西天门",
    "九天应元府",
)
_UNDERWORLD_KEYWORDS = (
    "地府", "冥界", "幽冥", "阴司", "阴曹", "黄泉",
    "奈何桥", "阎罗殿", "森罗殿", "枉死城",
)

# Instance candidates: location type keywords
_INSTANCE_TYPE_KEYWORDS = ("洞", "府")

# Direction inference from location names
_DIRECTION_MAP: dict[str, str] = {
    "东": "east",
    "西": "west",
    "南": "south",
    "北": "north",
}

# Context window half-size for raw text excerpt
_EXCERPT_HALF = 100

# ── Genre detection keywords ─────────────────────────────────────

_GENRE_KEYWORDS: dict[str, list[str]] = {
    "fantasy": [
        # Cultivation-specific terms (avoid single-char keywords that cause false positives)
        "修炼", "修仙", "灵气", "法宝", "丹药", "阵法", "飞升", "渡劫",
        "结丹", "元婴", "灵根", "功法", "法术", "御剑", "遁光", "神通",
        "灵石", "仙门", "仙宗", "仙界", "魔界", "妖界", "魔族", "妖族",
        "妖兽", "妖核", "魔力", "灵力", "仙人", "洞府",
        "天宫", "天庭", "龙宫", "地府",
    ],
    "wuxia": [
        "江湖", "门派", "武功", "内力", "武林", "侠", "剑法", "掌法",
        "轻功", "暗器", "镖局", "帮", "盟", "掌门", "弟子", "比武",
        "好汉", "头领", "落草", "绿林", "拳脚", "英雄好汉",
        "强盗", "响马", "草寇", "山贼",
    ],
    "historical": [
        "朝廷", "皇帝", "太监", "丞相", "将军", "知府", "知县",
        "年号", "国号", "殿下", "陛下", "圣旨", "科举",
        "太尉", "节度使", "制使", "提辖", "都头", "教头",
        "官府", "衙门", "差人", "公人", "押司", "殿帅",
        "府尹", "县令", "刺史", "巡抚", "总兵",
    ],
    "urban": [
        "公司", "学校", "大学", "手机", "电脑", "网络", "办公室",
        "警察", "医院", "地铁", "出租车", "餐厅",
    ],
    "realistic": [
        "省", "市", "县", "公社", "大队", "生产队",
        "工人", "农民", "知青", "干部", "书记",
        "粮食", "工资", "收入", "劳动", "生产",
        "高考", "中学", "小学", "文化大革命",
        "火车", "汽车", "拖拉机", "煤矿", "窑洞",
    ],
}

# ── Tier classification keyword maps ──────────────────────────

# Tier order for comparison (smaller number = bigger / higher level)
TIER_ORDER: dict[str, int] = {
    "world": 0, "continent": 1, "kingdom": 2, "region": 3,
    "city": 4, "site": 5, "building": 6,
}
_TIER_NAMES = list(TIER_ORDER.keys())

# Administrative system (realistic + historical)
_ADMIN_TIER_MAP: dict[str, str] = {
    # continent level
    "省": "continent", "自治区": "continent", "直辖市": "continent",
    # kingdom level
    "市": "kingdom", "州": "kingdom", "地区": "kingdom",
    "府": "kingdom", "道": "kingdom", "路": "kingdom",
    # region level
    "县": "region", "区": "region", "郡": "region",
    # city level
    "镇": "city", "乡": "city", "公社": "city",
    "集镇": "city", "街道": "city",
    # site level
    "村": "site", "庄": "site", "屯": "site", "寨": "site",
    "大队": "site", "生产队": "site", "自然村": "site",
}

# Fantasy system
_FANTASY_TIER_MAP: dict[str, str] = {
    "洲": "continent", "大陆": "continent", "界": "continent", "域": "continent",
    "大海": "continent",
    "国": "kingdom", "王国": "kingdom", "帝国": "kingdom",
    "城": "city", "城市": "city", "都": "city", "镇": "city",
    "宗": "region", "门": "region", "派": "region",
    "山": "region", "海": "region", "林": "region",
    "岛": "region", "谷": "region",
}

# Facility system (universal)
_FACILITY_TIER_MAP: dict[str, str] = {
    "学校": "building", "医院": "building", "工厂": "building",
    "车站": "building", "饭店": "building", "旅馆": "building",
    "公司": "building", "机关": "building", "银行": "building",
    "殿": "building", "堂": "building", "阁": "building",
    "楼": "building", "房": "building", "室": "building", "厅": "building",
    "宿舍": "building",
    "操场": "site", "广场": "site", "院子": "site",
    "饭场": "site", "窑洞": "site",
}

# Name suffix → tier (used by _classify_tier Layer 1 and _get_suffix_rank)
# Ordered by suffix length descending to avoid partial matches.
# Comprehensive coverage: administrative, fantasy, natural features, buildings.
_NAME_SUFFIX_TIER: list[tuple[str, str]] = [
    # ── 3+ char suffixes ──
    ("自治区", "continent"),
    ("直辖市", "continent"),
    # ── 2-char suffixes ──
    ("大陆", "continent"),
    ("王国", "kingdom"),
    ("帝国", "kingdom"),
    ("山脉", "region"),
    ("坊市", "site"),   # 坊市 = marketplace in fantasy, not municipality
    ("县城", "city"),   # "县城" = county seat, functions as a city/town
    ("地区", "kingdom"),
    ("城市", "city"),
    ("城池", "city"),
    ("公社", "city"),
    # Common building compound suffixes
    ("客栈", "building"),
    ("酒楼", "building"),
    ("酒馆", "building"),
    ("茶馆", "building"),
    ("茶楼", "building"),
    ("当铺", "building"),
    ("药铺", "building"),
    ("牌坊", "site"),
    ("书院", "building"),
    ("祠堂", "building"),
    ("宅院", "building"),
    ("府邸", "building"),
    ("洞府", "building"),  # cultivation cave dwelling
    # ── 1-char: macro geography (continent) ──
    ("省", "continent"),
    ("界", "continent"),
    ("洲", "continent"),
    ("域", "continent"),
    ("海", "continent"),
    # ── 1-char: kingdom ──
    ("国", "kingdom"),
    ("道", "kingdom"),   # Song dynasty administrative route
    ("府", "kingdom"),   # 开封府, 大名府, etc.
    ("州", "kingdom"),
    ("市", "kingdom"),
    # ── 1-char: region ──
    ("郡", "region"),
    ("县", "region"),
    ("山", "region"),
    ("岭", "region"),
    ("岛", "region"),
    ("谷", "region"),
    ("湖", "region"),
    ("河", "region"),
    ("林", "region"),
    # ── 1-char: city ──
    ("城", "city"),
    ("都", "city"),
    ("镇", "city"),
    ("乡", "city"),
    # ── 1-char: site ──
    ("村", "site"),
    ("庄", "site"),
    ("寨", "site"),
    ("屯", "site"),
    ("营", "site"),
    ("峰", "site"),
    ("坡", "site"),
    ("崖", "site"),
    ("岗", "site"),
    ("冈", "site"),
    ("洞", "site"),
    ("窟", "site"),
    ("穴", "site"),
    ("泉", "site"),
    ("潭", "site"),
    ("溪", "site"),
    ("关", "site"),
    ("坊", "site"),     # marketplace area
    ("寺", "site"),
    ("庙", "site"),
    ("观", "site"),
    ("庵", "site"),
    ("祠", "site"),
    # ── 1-char: building ──
    ("殿", "building"),
    ("堂", "building"),
    ("阁", "building"),
    ("楼", "building"),
    ("塔", "building"),
    ("亭", "building"),
    ("房", "building"),
    ("室", "building"),
    ("厅", "building"),
    ("院", "building"),
    ("馆", "building"),
    ("铺", "building"),
    ("店", "building"),
    ("家", "building"),
    ("宅", "building"),
    ("舍", "building"),
]


def _get_suffix_rank(name: str) -> int | None:
    """Get geographic scale rank from Chinese location name suffix.

    Uses name morphology (the suffix) to determine the geographic scale.
    Returns a rank from TIER_ORDER (smaller = larger geographic entity),
    or None if no recognizable suffix is found.

    This is more reliable than LLM-classified location_tiers because the
    suffix is factual (from the name itself), not model-inferred.
    """
    if len(name) < 2:
        return None
    for suffix, tier in _NAME_SUFFIX_TIER:
        if name.endswith(suffix):
            # Single-char suffix: require name longer than suffix (proper noun + suffix)
            # Multi-char suffix: allow name == suffix (e.g., "坊市" matches "坊市")
            if len(suffix) >= 2 or len(name) > len(suffix):
                return TIER_ORDER.get(tier, 4)
    return None

# Minimum score to assign a genre (otherwise "unknown")
_GENRE_MIN_SCORE = 5

# ── LLM prompt template ─────────────────────────────────────────

_PROMPTS_DIR = Path(__file__).parent.parent / "extraction" / "prompts"

# Max entries in location_region_map / location_layer_map sent to LLM
_MAX_MAP_ENTRIES = 50

# LLM output schema for structured output
_LLM_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "ADD_REGION", "ADD_LAYER", "ADD_PORTAL",
                            "ASSIGN_LOCATION", "UPDATE_REGION",
                            "SET_TIER", "SET_ICON", "SET_PARENT",
                            "NO_CHANGE",
                        ],
                    },
                    "layer_id": {"type": "string"},
                    "name": {"type": "string"},
                    "cardinal_direction": {"type": "string"},
                    "region_type": {"type": "string"},
                    "description": {"type": "string"},
                    "layer_type": {"type": "string"},
                    "source_layer": {"type": "string"},
                    "source_location": {"type": "string"},
                    "target_layer": {"type": "string"},
                    "target_location": {"type": "string"},
                    "is_bidirectional": {"type": "boolean"},
                    "location_name": {"type": "string"},
                    "region_name": {"type": "string"},
                    "tier": {"type": "string"},
                    "icon": {"type": "string"},
                    "parent": {"type": "string"},
                },
                "required": ["op"],
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["operations", "reasoning"],
}

# Valid LayerType values for ADD_LAYER
_VALID_LAYER_TYPES = {t.value for t in LayerType}


def _load_update_prompt_template() -> str:
    path = _PROMPTS_DIR / "world_structure_update.txt"
    return path.read_text(encoding="utf-8")


class WorldStructureAgent:
    """Scans chapters for world-building signals and updates WorldStructure."""

    def __init__(self, novel_id: str, llm: LLMClient | None = None) -> None:
        self.novel_id = novel_id
        self.structure: WorldStructure | None = None
        self._pending_signals: list[WorldBuildingSignal] = []
        self._llm = llm or get_llm_client()
        self._prompt_template: str | None = None
        self._llm_call_count: int = 0
        self._overridden_keys: set[tuple[str, str]] = set()
        self._parent_votes: dict[str, Counter] = {}  # child → Counter({parent: count})

    async def load_or_init(self) -> None:
        """Load existing WorldStructure from DB, or create default."""
        loaded = await world_structure_store.load(self.novel_id)
        if loaded is not None:
            self.structure = loaded
            logger.info("Loaded existing WorldStructure for %s", self.novel_id)
        else:
            self.structure = WorldStructure.create_default(self.novel_id)
            await world_structure_store.save(self.novel_id, self.structure)
            logger.info("Created default WorldStructure for %s", self.novel_id)
        # Load user override keys so we can skip them during LLM updates
        self._overridden_keys = await world_structure_override_store.get_overridden_keys(
            self.novel_id,
        )
        # Rebuild parent votes from existing facts (for pause/resume)
        self._parent_votes = await self._rebuild_parent_votes()

    async def process_chapter(
        self,
        chapter_num: int,
        chapter_text: str,
        fact: ChapterFact,
    ) -> None:
        """Main entry: scan signals + heuristic + optional LLM update."""
        try:
            if self.structure is None:
                await self.load_or_init()

            # Genre detection on early chapters
            if chapter_num <= 10 and self.structure is not None:
                self._detect_genre(chapter_text, fact)

            # Spatial scale detection after first 5 chapters
            if (
                chapter_num == 5
                and self.structure is not None
                and self.structure.spatial_scale is None
            ):
                scale = self._detect_spatial_scale()
                self.structure.spatial_scale = scale
                logger.info("Spatial scale detected: %s", scale)

            signals = self._scan_signals(chapter_num, chapter_text, fact)
            if signals:
                self._pending_signals.extend(signals)
                logger.debug(
                    "Chapter %d: detected %d world-building signals",
                    chapter_num, len(signals),
                )

            self._apply_heuristic_updates(chapter_num, fact)

            # LLM incremental update when trigger conditions are met
            if self._should_trigger_llm(chapter_num, signals, fact):
                await self._run_llm_update(chapter_num, signals, fact)

            # Resolve authoritative parents from accumulated votes
            if self._parent_votes:
                self.structure.location_parents = self._resolve_parents()

            # Consolidate hierarchy: reduce roots to single digits
            self.structure.location_parents, self.structure.location_tiers = (
                consolidate_hierarchy(
                    self.structure.location_parents,
                    self.structure.location_tiers,
                    novel_genre_hint=self.structure.novel_genre_hint,
                    parent_votes=self._parent_votes,
                )
            )

            await world_structure_store.save(self.novel_id, self.structure)
        except Exception:
            logger.warning(
                "WorldStructureAgent.process_chapter failed for chapter %d, "
                "continuing with last known good state",
                chapter_num,
                exc_info=True,
            )

    # ── Genre detection ────────────────────────────────────────────

    def _detect_genre(self, chapter_text: str, fact: ChapterFact) -> None:
        """Detect novel genre from chapter text keywords. Updates structure.novel_genre_hint."""
        assert self.structure is not None
        # Only detect once: skip if already confidently assigned
        if self.structure.novel_genre_hint and self.structure.novel_genre_hint != "unknown":
            return

        # Accumulate scores
        if not hasattr(self, "_genre_scores"):
            self._genre_scores: dict[str, int] = {g: 0 for g in _GENRE_KEYWORDS}

        # Scan chapter text for genre keywords
        for genre, keywords in _GENRE_KEYWORDS.items():
            for kw in keywords:
                if kw in chapter_text:
                    self._genre_scores[genre] += 1

        # Also scan concepts and location types from fact
        for concept in fact.new_concepts:
            for genre, keywords in _GENRE_KEYWORDS.items():
                if any(kw in concept.name or kw in concept.definition for kw in keywords):
                    self._genre_scores[genre] += 2

        # Determine best genre
        best_genre = max(self._genre_scores, key=lambda g: self._genre_scores[g])
        best_score = self._genre_scores[best_genre]

        if best_score >= _GENRE_MIN_SCORE:
            self.structure.novel_genre_hint = best_genre
            logger.info(
                "Genre detected: %s (score=%d, scores=%s)",
                best_genre, best_score, self._genre_scores,
            )

    def _is_instance_detection_enabled(self) -> bool:
        """Check if instance/pocket layer detection is enabled for this genre."""
        assert self.structure is not None
        genre = self.structure.novel_genre_hint
        # Urban/realistic novels: disable instance detection
        if genre in ("urban", "realistic"):
            return False
        return True

    # ── LLM trigger conditions ───────────────────────────────────

    def _should_trigger_llm(
        self,
        chapter_num: int,
        signals: list[WorldBuildingSignal],
        fact: ChapterFact,
    ) -> bool:
        """Determine whether to call LLM for world-structure update."""
        # Condition 1: first 5 chapters — always trigger
        if chapter_num <= 5:
            return True

        # Condition 2: any region_division signal
        if any(s.signal_type == "region_division" for s in signals):
            return True

        # Condition 3: layer_transition to a new (not yet existing) layer
        if any(s.signal_type == "layer_transition" for s in signals):
            assert self.structure is not None
            existing_layer_ids = {l.layer_id for l in self.structure.layers}
            for s in signals:
                if s.signal_type != "layer_transition":
                    continue
                # Check if any mentioned location keyword implies a new layer
                for kw in _LAYER_TRANS_LOC_KEYWORDS:
                    if kw in s.raw_text_excerpt:
                        implied = self._detect_layer(kw, "")
                        if implied and implied not in existing_layer_ids:
                            return True

        # Condition 4: 2+ new macro geography locations in this chapter
        macro_count = sum(
            1 for loc in fact.locations
            if any(s in (loc.type or "") for s in _MACRO_GEO_SUFFIXES)
        )
        if macro_count >= 2:
            return True

        # Condition 5: periodic check every 20 chapters
        if chapter_num % 20 == 0:
            return True

        return False

    # ── LLM update pipeline ──────────────────────────────────────

    async def _run_llm_update(
        self,
        chapter_num: int,
        signals: list[WorldBuildingSignal],
        fact: ChapterFact,
    ) -> None:
        """Call LLM and apply returned operations. Never raises."""
        try:
            operations = await self._call_llm_for_update(
                chapter_num, signals, fact,
            )
            if operations:
                self._apply_operations(operations)
                logger.info(
                    "Chapter %d: applied %d LLM operations to WorldStructure",
                    chapter_num, len(operations),
                )
        except Exception:
            logger.warning(
                "LLM world-structure update failed for chapter %d, "
                "keeping heuristic-only state",
                chapter_num,
                exc_info=True,
            )

    async def _call_llm_for_update(
        self,
        chapter_num: int,
        signals: list[WorldBuildingSignal],
        fact: ChapterFact,
    ) -> list[dict]:
        """Build prompt, call LLM, parse operations list."""
        if self._prompt_template is None:
            self._prompt_template = _load_update_prompt_template()

        assert self.structure is not None

        # Build prompt sections
        structure_summary = self._summarize_structure()
        signals_text = self._format_signals(signals)
        locations_text = self._format_locations(fact)
        spatial_text = self._format_spatial(fact)

        prompt = self._prompt_template.format(
            current_structure=structure_summary,
            signals=signals_text,
            locations=locations_text,
            spatial_relationships=spatial_text,
        )

        # Inject genre-aware guidance to prevent hallucinating inappropriate regions
        genre = self.structure.novel_genre_hint or "unknown"
        if genre in ("urban", "historical", "realistic"):
            prompt += (
                "\n\n**重要: 本小说为现实题材，不要创建奇幻/神话类的区域"
                "（如仙界、魔域等）。区域应基于现实地理（省份、城市、地区等）。**"
            )
        elif genre == "fantasy":
            prompt += "\n\n**本小说为奇幻题材，区域可以包含虚构的大陆、界域等。**"

        # Inject suspicious hierarchy relationships for LLM correction
        suspicious: list[str] = []
        for child, parent in self.structure.location_parents.items():
            child_tier = self.structure.location_tiers.get(child)
            parent_tier = self.structure.location_tiers.get(parent)
            if child_tier and parent_tier:
                if TIER_ORDER.get(parent_tier, 3) > TIER_ORDER.get(child_tier, 3):
                    suspicious.append(
                        f"{child}({child_tier}) ⊂ {parent}({parent_tier}) — 可能反转"
                    )
        if suspicious:
            prompt += (
                "\n\n⚠️ 以下层级关系可能有误，请用 SET_PARENT 修正：\n"
                + "\n".join(suspicious[:10])
            )

        system = "你是一个小说世界观构建专家。请严格按照 JSON 格式输出。"

        _is_cloud = LLM_PROVIDER == "openai"
        result, _usage = await self._llm.generate(
            system=system,
            prompt=prompt,
            format=_LLM_OUTPUT_SCHEMA,
            temperature=0.1,
            max_tokens=8192 if _is_cloud else 4096,
            timeout=180 if _is_cloud else 120,
            num_ctx=8192,
        )
        self._llm_call_count += 1

        if isinstance(result, str):
            logger.warning("LLM returned str instead of dict, attempting parse")
            result = json.loads(result)

        operations = result.get("operations", [])
        reasoning = result.get("reasoning", "")
        if reasoning:
            logger.debug("LLM reasoning: %s", reasoning[:200])

        return [op for op in operations if isinstance(op, dict)]

    def _summarize_structure(self) -> str:
        """Summarize current WorldStructure for LLM context (≤ 2000 tokens)."""
        assert self.structure is not None
        parts: list[str] = []

        # Layers summary
        parts.append("### 层 (Layers)")
        for layer in self.structure.layers:
            parts.append(
                f"- {layer.layer_id}: {layer.name} (type={layer.layer_type.value})"
            )
            for region in layer.regions:
                dir_str = f", direction={region.cardinal_direction}" if region.cardinal_direction else ""
                parts.append(
                    f"  - 区域: {region.name} (type={region.region_type or '?'}{dir_str})"
                )

        # Portals summary
        if self.structure.portals:
            parts.append("\n### 传送通道 (Portals)")
            for portal in self.structure.portals:
                parts.append(
                    f"- {portal.name}: {portal.source_layer} → {portal.target_layer}"
                )

        # Location maps (truncated)
        loc_layer = self.structure.location_layer_map
        if loc_layer:
            entries = list(loc_layer.items())[:_MAX_MAP_ENTRIES]
            parts.append(f"\n### 地点→层映射 (前{len(entries)}条)")
            for name, layer_id in entries:
                parts.append(f"- {name} → {layer_id}")

        loc_region = self.structure.location_region_map
        if loc_region:
            entries = list(loc_region.items())[:_MAX_MAP_ENTRIES]
            parts.append(f"\n### 地点→区域映射 (前{len(entries)}条)")
            for name, region_name in entries:
                parts.append(f"- {name} → {region_name}")

        return "\n".join(parts)

    def _format_signals(self, signals: list[WorldBuildingSignal]) -> str:
        if not signals and not self._pending_signals:
            return "（无信号）"
        # Include both current chapter signals and recent pending signals
        all_signals = signals + self._pending_signals[-10:]
        parts: list[str] = []
        for sig in all_signals[:15]:  # Cap at 15 signals for context budget
            parts.append(
                f"- [{sig.signal_type}] 第{sig.chapter}章 (置信度={sig.confidence}): "
                f"{sig.raw_text_excerpt[:200]}"
            )
        return "\n".join(parts) if parts else "（无信号）"

    @staticmethod
    def _format_locations(fact: ChapterFact) -> str:
        if not fact.locations:
            return "（无地点）"
        parts: list[str] = []
        for loc in fact.locations:
            parent = f", parent={loc.parent}" if loc.parent else ""
            desc = f", desc={loc.description[:50]}" if loc.description else ""
            parts.append(f"- {loc.name} (type={loc.type or '?'}{parent}{desc})")
        return "\n".join(parts)

    @staticmethod
    def _format_spatial(fact: ChapterFact) -> str:
        if not fact.spatial_relationships:
            return "（无空间关系）"
        parts: list[str] = []
        for sr in fact.spatial_relationships:
            parts.append(
                f"- {sr.source} → {sr.target}: {sr.relation_type}={sr.value} "
                f"(confidence={sr.confidence})"
            )
        return "\n".join(parts)

    # ── Operation application ────────────────────────────────────

    def _apply_operations(self, operations: list[dict]) -> None:
        """Apply a list of LLM-returned operations to self.structure."""
        for op in operations:
            op_type = op.get("op", "")
            try:
                if op_type == "ADD_REGION":
                    self._op_add_region(op)
                elif op_type == "ADD_LAYER":
                    self._op_add_layer(op)
                elif op_type == "ADD_PORTAL":
                    self._op_add_portal(op)
                elif op_type == "ASSIGN_LOCATION":
                    self._op_assign_location(op)
                elif op_type == "UPDATE_REGION":
                    self._op_update_region(op)
                elif op_type == "SET_TIER":
                    self._op_set_tier(op)
                elif op_type == "SET_ICON":
                    self._op_set_icon(op)
                elif op_type == "SET_PARENT":
                    self._op_set_parent(op)
                elif op_type == "NO_CHANGE":
                    pass
                else:
                    logger.warning("Unknown operation type: %s", op_type)
            except Exception:
                logger.warning(
                    "Failed to apply operation %s: %s",
                    op_type, op,
                    exc_info=True,
                )

    def _op_add_region(self, op: dict) -> None:
        assert self.structure is not None
        layer_id = op.get("layer_id", "overworld")
        name = op.get("name", "")
        if not name:
            return

        layer = self._get_layer(layer_id)
        if layer is None:
            logger.warning("ADD_REGION: layer %s not found", layer_id)
            return

        # Skip if region already exists
        if any(r.name == name for r in layer.regions):
            return

        layer.regions.append(WorldRegion(
            name=name,
            cardinal_direction=op.get("cardinal_direction"),
            region_type=op.get("region_type"),
            description=op.get("description", ""),
        ))
        # Also update region map
        self.structure.location_region_map[name] = name

    def _op_add_layer(self, op: dict) -> None:
        assert self.structure is not None
        name = op.get("name", "")
        if not name:
            return

        layer_type_str = op.get("layer_type", "pocket")
        if layer_type_str not in _VALID_LAYER_TYPES:
            layer_type_str = "pocket"

        # Generate layer_id from name
        layer_id = name.lower().replace(" ", "_")
        # Avoid duplicates
        if self._has_layer(layer_id):
            return

        self.structure.layers.append(MapLayer(
            layer_id=layer_id,
            name=name,
            layer_type=LayerType(layer_type_str),
            description=op.get("description", ""),
        ))

    def _op_add_portal(self, op: dict) -> None:
        assert self.structure is not None
        name = op.get("name", "")
        source_layer = op.get("source_layer", "")
        target_layer = op.get("target_layer", "")
        if not name or not source_layer or not target_layer:
            return

        # Skip if user has explicitly deleted this portal
        if ("delete_portal", name) in self._overridden_keys:
            return

        # Validate layers exist
        if not self._has_layer(source_layer) or not self._has_layer(target_layer):
            logger.warning(
                "ADD_PORTAL: source=%s or target=%s layer not found",
                source_layer, target_layer,
            )
            return

        self.structure.portals.append(Portal(
            name=name,
            source_layer=source_layer,
            source_location=op.get("source_location", ""),
            target_layer=target_layer,
            target_location=op.get("target_location", ""),
            is_bidirectional=op.get("is_bidirectional", True),
        ))

    def _op_assign_location(self, op: dict) -> None:
        assert self.structure is not None
        location_name = op.get("location_name", "")
        if not location_name:
            return

        region_name = op.get("region_name")
        layer_id = op.get("layer_id")

        # Skip if user has an override for this location's region
        if region_name and ("location_region", location_name) not in self._overridden_keys:
            self.structure.location_region_map[location_name] = region_name
        # Skip if user has an override for this location's layer
        if layer_id and self._has_layer(layer_id) and ("location_layer", location_name) not in self._overridden_keys:
            self.structure.location_layer_map[location_name] = layer_id

    def _op_update_region(self, op: dict) -> None:
        assert self.structure is not None
        layer_id = op.get("layer_id", "overworld")
        region_name = op.get("region_name", "")
        if not region_name:
            return

        layer = self._get_layer(layer_id)
        if layer is None:
            return

        for region in layer.regions:
            if region.name == region_name:
                if "cardinal_direction" in op and op["cardinal_direction"]:
                    region.cardinal_direction = op["cardinal_direction"]
                if "region_type" in op and op["region_type"]:
                    region.region_type = op["region_type"]
                if "description" in op and op["description"]:
                    region.description = op["description"]
                return

    def _op_set_tier(self, op: dict) -> None:
        assert self.structure is not None
        name = op.get("location_name", "")
        tier = op.get("tier", "")
        if name and tier and tier in {t.value for t in LocationTier}:
            self.structure.location_tiers[name] = tier

    def _op_set_icon(self, op: dict) -> None:
        assert self.structure is not None
        name = op.get("location_name", "")
        icon = op.get("icon", "")
        if name and icon and icon in {i.value for i in LocationIcon}:
            self.structure.location_icons[name] = icon

    def _op_set_parent(self, op: dict) -> None:
        assert self.structure is not None
        loc_name = op.get("location_name", "")
        parent_name = op.get("parent", "")
        if loc_name and parent_name and ("location_parent", loc_name) not in self._overridden_keys:
            self.structure.location_parents[loc_name] = parent_name

    # ── Signal scanning ──────────────────────────────────────────

    def _scan_signals(
        self,
        chapter_num: int,
        chapter_text: str,
        fact: ChapterFact,
    ) -> list[WorldBuildingSignal]:
        """Detect world-building signals from raw text and ChapterFact."""
        signals: list[WorldBuildingSignal] = []

        signals.extend(self._scan_region_division(chapter_num, chapter_text))
        signals.extend(self._scan_layer_transition(chapter_num, chapter_text))
        signals.extend(self._scan_instance_entry(chapter_num, chapter_text))
        signals.extend(self._scan_macro_geography(chapter_num, fact))
        signals.extend(self._scan_world_declarations(chapter_num, fact))

        return signals

    def _scan_region_division(
        self, chapter_num: int, text: str,
    ) -> list[WorldBuildingSignal]:
        signals: list[WorldBuildingSignal] = []

        # Keyword scan
        for kw in _REGION_DIV_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                signals.append(WorldBuildingSignal(
                    signal_type="region_division",
                    chapter=chapter_num,
                    raw_text_excerpt=self._extract_excerpt(text, m.start()),
                    extracted_facts=[f"关键词命中: {kw}"],
                    confidence="medium",
                ))

        # Regex pattern scan
        for m in _REGION_DIV_PATTERN.finditer(text):
            signals.append(WorldBuildingSignal(
                signal_type="region_division",
                chapter=chapter_num,
                raw_text_excerpt=self._extract_excerpt(text, m.start()),
                extracted_facts=[f"模式命中: {m.group()}"],
                confidence="high",
            ))

        return self._dedup_signals(signals)

    def _scan_layer_transition(
        self, chapter_num: int, text: str,
    ) -> list[WorldBuildingSignal]:
        signals: list[WorldBuildingSignal] = []

        for kw in _LAYER_TRANS_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                signals.append(WorldBuildingSignal(
                    signal_type="layer_transition",
                    chapter=chapter_num,
                    raw_text_excerpt=self._extract_excerpt(text, m.start()),
                    extracted_facts=[f"关键词命中: {kw}"],
                    confidence="high",
                ))

        for kw in _LAYER_TRANS_LOC_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                signals.append(WorldBuildingSignal(
                    signal_type="layer_transition",
                    chapter=chapter_num,
                    raw_text_excerpt=self._extract_excerpt(text, m.start()),
                    extracted_facts=[f"地点关键词命中: {kw}"],
                    confidence="medium",
                ))

        return self._dedup_signals(signals)

    def _scan_instance_entry(
        self, chapter_num: int, text: str,
    ) -> list[WorldBuildingSignal]:
        signals: list[WorldBuildingSignal] = []

        for kw in _INSTANCE_ENTRY_KEYWORDS:
            for m in re.finditer(re.escape(kw), text):
                signals.append(WorldBuildingSignal(
                    signal_type="instance_entry",
                    chapter=chapter_num,
                    raw_text_excerpt=self._extract_excerpt(text, m.start()),
                    extracted_facts=[f"关键词命中: {kw}"],
                    confidence="medium",
                ))

        for m in _INSTANCE_TYPE_PATTERN.finditer(text):
            # Only count if surrounded by entry-like context
            start = max(0, m.start() - 10)
            context = text[start:m.end()]
            if any(verb in context for verb in ("进", "入", "闯", "踏")):
                signals.append(WorldBuildingSignal(
                    signal_type="instance_entry",
                    chapter=chapter_num,
                    raw_text_excerpt=self._extract_excerpt(text, m.start()),
                    extracted_facts=[f"类型模式命中: {m.group()}"],
                    confidence="low",
                ))

        return self._dedup_signals(signals)

    def _scan_macro_geography(
        self, chapter_num: int, fact: ChapterFact,
    ) -> list[WorldBuildingSignal]:
        signals: list[WorldBuildingSignal] = []

        for loc in fact.locations:
            loc_type = loc.type or ""
            if any(suffix in loc_type for suffix in _MACRO_GEO_SUFFIXES):
                signals.append(WorldBuildingSignal(
                    signal_type="macro_geography",
                    chapter=chapter_num,
                    raw_text_excerpt=f"{loc.name} (type={loc_type})",
                    extracted_facts=[f"宏观地点: {loc.name}"],
                    confidence="high",
                ))

        return signals

    def _scan_world_declarations(
        self, chapter_num: int, fact: ChapterFact,
    ) -> list[WorldBuildingSignal]:
        """Convert LLM-extracted world_declarations into WorldBuildingSignals."""
        signals: list[WorldBuildingSignal] = []

        for decl in fact.world_declarations:
            # Map declaration_type to signal_type
            signal_type_map = {
                "region_division": "region_division",
                "layer_exists": "layer_transition",
                "portal": "layer_transition",
                "region_position": "macro_geography",
            }
            signal_type = signal_type_map.get(decl.declaration_type)
            if signal_type is None:
                continue

            extracted = []
            if isinstance(decl.content, dict):
                for k, v in decl.content.items():
                    extracted.append(f"{k}: {v}")

            signals.append(WorldBuildingSignal(
                signal_type=signal_type,
                chapter=chapter_num,
                raw_text_excerpt=decl.narrative_evidence[:200],
                extracted_facts=extracted,
                confidence=decl.confidence,
            ))

        return signals

    # ── Heuristic updates ────────────────────────────────────────

    def _apply_heuristic_updates(
        self, chapter_num: int, fact: ChapterFact,
    ) -> None:
        """Apply keyword-based heuristics to assign locations to layers/regions."""
        assert self.structure is not None

        for loc in fact.locations:
            name = loc.name
            loc_type = loc.type or ""

            # ── Layer assignment ─────────────────────────────
            # Skip if user has an override for this location's layer
            if ("location_layer", name) not in self._overridden_keys:
                assigned_layer = self._detect_layer(name, loc_type)
                if assigned_layer is not None:
                    self._ensure_layer_exists(assigned_layer)
                    self.structure.location_layer_map[name] = assigned_layer
                elif name not in self.structure.location_layer_map:
                    # Default to overworld
                    self.structure.location_layer_map[name] = "overworld"

            # ── Instance candidate detection ─────────────────
            if (
                self._is_instance_detection_enabled()
                and any(kw in loc_type for kw in _INSTANCE_TYPE_KEYWORDS)
                and loc.parent
            ):
                # Instance locations get a pocket layer ID derived from name
                layer_id = f"instance_{name}"
                if not self._has_layer(layer_id):
                    self.structure.layers.append(MapLayer(
                        layer_id=layer_id,
                        name=name,
                        layer_type=LayerType.pocket,
                        description=f"副本/洞府: {name}",
                    ))
                self.structure.location_layer_map[name] = layer_id

            # ── Region assignment ────────────────────────────
            # Skip if user has an override for this location's region
            if ("location_region", name) not in self._overridden_keys:
                self._assign_region(name, loc_type, loc.parent)

            # ── Tier classification (only if not already set) ──
            if name not in self.structure.location_tiers:
                parent = loc.parent
                level = 0
                if parent and parent in self.structure.location_layer_map:
                    level = 1  # has a parent → at least level 1
                tier = self._classify_tier(name, loc_type, parent, level)
                self.structure.location_tiers[name] = tier

            # ── Icon classification (only if not already set) ──
            if name not in self.structure.location_icons:
                icon = self._classify_icon(name, loc_type)
                self.structure.location_icons[name] = icon

            # ── Parent vote accumulation ──
            # Skip generic locations — they pollute hierarchy with noise
            if loc.parent and loc.name != loc.parent:
                if not _is_generic_location(loc.name) and not _is_generic_location(loc.parent):
                    self._parent_votes.setdefault(loc.name, Counter())[loc.parent] += 1

        # Accumulate contains relationships as parent votes
        # Contains direction validation: LLM frequently inverts the direction,
        # writing "A contains B" when meaning "A is inside B".
        # Primary signal: suffix rank (name morphology). Fallback: tier comparison.
        for sr in fact.spatial_relationships:
            if sr.relation_type == "contains" and sr.source != sr.target:
                source, target = sr.source, sr.target
                # Skip generic locations
                if _is_generic_location(source) or _is_generic_location(target):
                    continue
                # Defensive weight: reduced from {3,2,1} to {2,1,1}
                # because contains direction is unreliable from LLM
                weight = {"high": 2, "medium": 1, "low": 1}.get(sr.confidence, 1)
                # Direction validation: suffix rank > tier order
                source_suf = _get_suffix_rank(source)
                target_suf = _get_suffix_rank(target)
                if source_suf is not None and target_suf is not None:
                    if source_suf > target_suf:
                        source, target = target, source
                    elif source_suf == target_suf:
                        weight = 1  # Same suffix rank, reduce weight
                else:
                    # Fallback: tier-based direction validation
                    source_tier = self.structure.location_tiers.get(source, "city")
                    target_tier = self.structure.location_tiers.get(target, "city")
                    source_rank = TIER_ORDER.get(source_tier, 4)
                    target_rank = TIER_ORDER.get(target_tier, 4)
                    if source_rank > target_rank:
                        source, target = target, source
                    elif source_rank == target_rank:
                        # Name containment heuristic
                        if source.startswith(target) and len(source) > len(target):
                            source, target = target, source
                        elif not (target.startswith(source) and len(target) > len(source)):
                            weight = 1
                # source is container (parent), target is contained (child)
                self._parent_votes.setdefault(target, Counter())[source] += weight

        # ── Adjacent / Direction / In-between → parent propagation votes ──
        # These non-contains spatial relationships indicate spatial proximity.
        # If A is adjacent/direction to B and B already has a parent candidate C,
        # propagate a weak vote A→C (weight=1). This allows locations near each
        # other to inherit hierarchy from their neighbors.
        for sr in fact.spatial_relationships:
            if sr.relation_type not in ("adjacent", "direction", "in_between"):
                continue
            source, target = sr.source, sr.target
            if source == target:
                continue
            if _is_generic_location(source) or _is_generic_location(target):
                continue
            # Bidirectional propagation: if either has a parent, share with the other
            for from_loc, to_loc in [(source, target), (target, source)]:
                from_votes = self._parent_votes.get(from_loc)
                if not from_votes:
                    continue
                best_parent, best_count = from_votes.most_common(1)[0]
                if best_parent and best_parent != to_loc and best_count >= 2:
                    # Weak vote — must not exceed direct parent declaration weight
                    self._parent_votes.setdefault(to_loc, Counter())[best_parent] += 1

        # ── Name containment parent inference ──
        # If "石圪节公社" and "石圪节" both exist, the longer one is likely
        # the administrative parent of the shorter one (or they're the same).
        # Give implicit votes so hierarchy forms even without explicit parent.
        all_known = set(self.structure.location_tiers.keys())
        for loc in fact.locations:
            name = loc.name
            if _is_generic_location(name):
                continue
            for other in all_known:
                if name == other or _is_generic_location(other):
                    continue
                # Longer name starts with shorter: longer is likely child
                # e.g., "石圪节公社" starts with "石圪节" but is actually the
                # PARENT (公社 > 镇/集镇). Use admin suffix to decide direction.
                if len(name) > len(other) and name.startswith(other):
                    # name is longer, e.g. "石圪节公社" starts with "石圪节"
                    suffix = name[len(other):]
                    if suffix in _ADMIN_TIER_MAP:
                        # suffix is admin term → longer name is admin parent
                        # "石圪节公社" is parent of "石圪节"
                        self._parent_votes.setdefault(other, Counter())[name] += 1
                    else:
                        # suffix is descriptive → longer name is child
                        # "黄原汽车站" is child of "黄原"
                        self._parent_votes.setdefault(name, Counter())[other] += 1

        # ── Learn type hierarchy from parent-child type pairs ──
        self._learn_type_hierarchy(fact)

    def _learn_type_hierarchy(self, fact: ChapterFact) -> None:
        """Learn type hierarchy from parent-child pairs in the current chapter.

        If location A (type=村) has parent B (type=镇), infer that "村" < "镇".
        Stored in WorldStructure.type_hierarchy for use by _classify_tier().
        """
        assert self.structure is not None

        # Vague types that shouldn't participate in type hierarchy learning
        _VAGUE_TYPES = {"区域", "地点", "地方", "位置", "场景"}

        # Build a name → type lookup from this chapter's locations
        loc_type_map: dict[str, str] = {}
        for loc in fact.locations:
            if loc.type and loc.type not in _VAGUE_TYPES:
                loc_type_map[loc.name] = loc.type

        for loc in fact.locations:
            if loc.parent and loc.type and loc.type not in _VAGUE_TYPES:
                parent_type = loc_type_map.get(loc.parent)
                if not parent_type:
                    continue
                if parent_type and parent_type != loc.type:
                    self.structure.type_hierarchy[loc.type] = parent_type

    def _classify_tier(
        self, name: str, loc_type: str, parent: str | None, level: int = 0,
    ) -> str:
        """Classify a location into a spatial tier using multi-signal fusion.

        Layer 0: World-level special cases.
        Layer 1: Name suffix matching (highest priority — name is reliable).
        Layer 2: Explicit type keyword matching (genre-ordered maps, skip vague types).
        Layer 3: Learned type hierarchy (from WorldStructure.type_hierarchy).
        Layer 4: Legacy heuristics (level-based fallback).
        Layer 5: Parent tier constraint (child cannot be >= parent).
        """
        assert self.structure is not None

        # Vague types that LLM uses as catch-all — treat as uninformative
        _VAGUE_TYPES = {"区域", "地点", "地方", "位置", "场景"}
        effective_type = "" if loc_type in _VAGUE_TYPES else loc_type

        # ── Layer 0: world-level special cases ──
        if any(kw in name for kw in ("三界", "天下")) or "世界" in loc_type:
            return LocationTier.world.value

        # ── Layer 1: name suffix matching (name is more reliable than LLM type) ──
        raw_tier: str | None = None
        for suffix, tier in _NAME_SUFFIX_TIER:
            if name.endswith(suffix):
                raw_tier = tier
                break

        # ── Layer 2: explicit type keyword matching ──
        if raw_tier is None and effective_type:
            # Choose map priority order based on genre
            genre = self.structure.novel_genre_hint
            if genre in ("realistic", "urban", "historical", "wuxia"):
                tier_maps = [_ADMIN_TIER_MAP, _FACILITY_TIER_MAP, _FANTASY_TIER_MAP]
            else:
                tier_maps = [_FANTASY_TIER_MAP, _FACILITY_TIER_MAP, _ADMIN_TIER_MAP]

            for tier_map in tier_maps:
                # Try longest-match first to avoid "市" matching inside "城市"
                for kw in sorted(tier_map.keys(), key=len, reverse=True):
                    if kw in effective_type:
                        raw_tier = tier_map[kw]
                        break
                if raw_tier:
                    break

        # ── Layer 3: learned type hierarchy ──
        if raw_tier is None and effective_type and hasattr(self.structure, "type_hierarchy"):
            th = self.structure.type_hierarchy
            if effective_type in th:
                parent_type = th[effective_type]
                parent_type_tier = self._type_to_tier(parent_type)
                if parent_type_tier:
                    raw_tier = self._tier_one_below(parent_type_tier)

        # ── Layer 4: legacy heuristics ──
        if raw_tier is None:
            # "国" in name (kingdom)
            if "国" in name:
                raw_tier = LocationTier.kingdom.value
            # site-level features
            elif any(kw in effective_type for kw in ("洞", "穴", "桥", "渡", "关", "隘", "泉", "潭", "崖")):
                raw_tier = LocationTier.site.value
            elif level >= 2:
                raw_tier = LocationTier.site.value
            # region fallback for top-level locations with informative type
            elif level == 0 and parent is None and effective_type and not any(
                kw in effective_type for kw in ("城", "镇", "都", "村")
            ):
                # Orphan nodes with unrecognized type: default to site instead of
                # region to prevent inflating high-tier counts under 天下.
                raw_tier = LocationTier.site.value
            else:
                raw_tier = LocationTier.city.value

        # ── Layer 5: parent tier constraint ──
        if parent and raw_tier:
            parent_tier = self.structure.location_tiers.get(parent)
            if parent_tier:
                parent_rank = TIER_ORDER.get(parent_tier, 3)
                child_rank = TIER_ORDER.get(raw_tier, 4)
                if child_rank <= parent_rank:
                    # Child should not be bigger than or equal to parent — push down
                    raw_tier = _TIER_NAMES[min(parent_rank + 1, len(_TIER_NAMES) - 1)]

        return raw_tier

    @staticmethod
    def _type_to_tier(loc_type: str) -> str | None:
        """Look up a location type in all tier maps and return its tier."""
        for tier_map in [_ADMIN_TIER_MAP, _FANTASY_TIER_MAP, _FACILITY_TIER_MAP]:
            for kw in sorted(tier_map.keys(), key=len, reverse=True):
                if kw in loc_type:
                    return tier_map[kw]
        return None

    @staticmethod
    def _tier_one_below(tier: str) -> str:
        """Return the tier one level below the given tier."""
        rank = TIER_ORDER.get(tier, 3)
        return _TIER_NAMES[min(rank + 1, len(_TIER_NAMES) - 1)]

    @staticmethod
    def _classify_icon(name: str, loc_type: str) -> str:
        """Classify a location's icon type based on name/type heuristics."""
        # Use name + loc_type for matching, but also check name suffixes
        # when loc_type is vague (区域, 地点, etc.)
        _VAGUE_TYPES = {"区域", "地点", "地方", "位置", "场景"}
        effective_type = "" if loc_type in _VAGUE_TYPES else loc_type
        combined = name + effective_type

        # Name suffix hints (check first — name is reliable)
        if name.endswith(("省", "自治区")) or any(kw in effective_type for kw in ("省", "自治区", "直辖市")):
            return LocationIcon.city.value
        if name.endswith(("市", "州")) or any(kw in effective_type for kw in ("市", "州", "地区", "府", "道")):
            return LocationIcon.city.value
        if name.endswith(("县", "县城")) or any(kw in effective_type for kw in ("县", "区", "郡")):
            return LocationIcon.city.value
        # cities / settlements
        if any(kw in effective_type for kw in ("城", "镇", "都", "公社", "集镇", "街道", "乡")):
            return LocationIcon.city.value
        if name.endswith(("公社", "城")):
            return LocationIcon.city.value
        if any(kw in effective_type for kw in ("村", "寨", "庄", "屯", "大队", "生产队", "自然村")):
            return LocationIcon.village.value
        if name.endswith(("村", "庄")):
            return LocationIcon.village.value
        if any(kw in combined for kw in ("营", "帐")):
            return LocationIcon.camp.value
        # institutions / facilities
        if any(kw in effective_type for kw in ("学校", "医院", "工厂", "公司", "机关")):
            return LocationIcon.temple.value  # temple icon for institutional buildings
        if any(kw in effective_type for kw in ("车站", "码头", "渡口")):
            return LocationIcon.gate.value
        # nature
        if any(kw in combined for kw in ("山", "峰", "岭", "崖")):
            return LocationIcon.mountain.value
        if any(kw in combined for kw in ("林", "森")):
            return LocationIcon.forest.value
        if any(kw in combined for kw in ("海", "河", "湖", "泉", "潭")):
            return LocationIcon.water.value
        if any(kw in combined for kw in ("沙", "漠", "荒")):
            return LocationIcon.desert.value
        # structures
        if any(kw in combined for kw in ("寺", "庙", "观", "庵")):
            return LocationIcon.temple.value
        if any(kw in combined for kw in ("宫", "殿", "府")):
            return LocationIcon.palace.value
        if any(kw in combined for kw in ("洞", "穴", "窑洞")):
            return LocationIcon.cave.value
        if any(kw in combined for kw in ("塔", "阁", "楼")):
            return LocationIcon.tower.value
        if any(kw in combined for kw in ("关", "隘")):
            return LocationIcon.gate.value
        if any(kw in combined for kw in ("传送", "入口")):
            return LocationIcon.portal.value
        if any(kw in combined for kw in ("废", "墟", "遗迹")):
            return LocationIcon.ruins.value
        return LocationIcon.generic.value

    def _detect_spatial_scale(self) -> str:
        """Infer spatial scale from genre + location type distribution."""
        assert self.structure is not None
        genre = self.structure.novel_genre_hint
        if genre == "urban":
            return "urban"
        if genre == "realistic":
            return "national"  # realistic fiction is at least national scale
        if genre in ("wuxia", "historical"):
            return "national"  # wuxia/historical is always national scale

        # Check known tier distribution
        tier_counts = Counter(self.structure.location_tiers.values())
        has_continent = tier_counts.get("continent", 0) > 0
        has_kingdom = tier_counts.get("kingdom", 0) > 0

        # Check for multi-layer (celestial / underworld)
        non_overworld = [l for l in self.structure.layers if l.layer_id != "overworld"]
        has_celestial = any(l.layer_type == LayerType.sky for l in non_overworld)

        if has_celestial and has_continent:
            return "cosmic"
        if has_continent:
            return "continental"
        if has_kingdom:
            return "national"

        # Genre-based fallback
        if genre == "fantasy":
            return "cosmic"
        if genre in ("wuxia", "historical"):
            return "national"

        return "continental"  # safe default

    def _detect_layer(self, name: str, loc_type: str) -> str | None:
        """Return layer_id if the location matches celestial/underworld keywords."""
        for kw in _CELESTIAL_KEYWORDS:
            if kw in name:
                return "celestial"
        for kw in _UNDERWORLD_KEYWORDS:
            if kw in name:
                return "underworld"
        return None

    def _ensure_layer_exists(self, layer_id: str) -> None:
        """Create a layer if it doesn't already exist."""
        assert self.structure is not None
        if self._has_layer(layer_id):
            return

        type_map: dict[str, tuple[LayerType, str]] = {
            "celestial": (LayerType.sky, "天界"),
            "underworld": (LayerType.underground, "冥界/地府"),
        }
        layer_type, layer_name = type_map.get(
            layer_id, (LayerType.pocket, layer_id)
        )
        self.structure.layers.append(MapLayer(
            layer_id=layer_id,
            name=layer_name,
            layer_type=layer_type,
            description=f"自动创建: {layer_name}",
        ))
        logger.info("Auto-created layer: %s (%s)", layer_id, layer_name)

    def _has_layer(self, layer_id: str) -> bool:
        assert self.structure is not None
        return any(l.layer_id == layer_id for l in self.structure.layers)

    def _assign_region(
        self, name: str, loc_type: str, parent: str | None,
    ) -> None:
        """Assign a location to a region based on parent or name heuristics."""
        assert self.structure is not None

        # If parent is a known region, assign to it
        if parent:
            for layer in self.structure.layers:
                for region in layer.regions:
                    if region.name == parent:
                        self.structure.location_region_map[name] = parent
                        return

        # If it's a macro type, create/find a region and infer direction
        if any(suffix in loc_type for suffix in _MACRO_GEO_SUFFIXES):
            direction = self._infer_direction(name, loc_type)
            # Check if region already exists
            overworld = self._get_layer("overworld")
            if overworld is not None:
                existing = [r for r in overworld.regions if r.name == name]
                if not existing:
                    overworld.regions.append(WorldRegion(
                        name=name,
                        cardinal_direction=direction,
                        region_type=loc_type,
                    ))
            # Region maps to itself
            self.structure.location_region_map[name] = name
            return

        # If parent is set but not a known region, still record the mapping
        if parent and parent in self.structure.location_region_map:
            parent_region = self.structure.location_region_map[parent]
            self.structure.location_region_map[name] = parent_region

    def _infer_direction(self, name: str, loc_type: str = "") -> str | None:
        """Infer cardinal direction from location name using hint service."""
        return extract_direction_hint(name, loc_type)

    def _get_layer(self, layer_id: str) -> MapLayer | None:
        assert self.structure is not None
        for layer in self.structure.layers:
            if layer.layer_id == layer_id:
                return layer
        return None

    # ── External vote injection ─────────────────────────

    def inject_external_votes(self, votes: dict[str, Counter]) -> None:
        """Merge externally-produced parent votes into the internal accumulator.

        Used by post-analysis steps (SceneTransitionAnalyzer,
        LocationHierarchyReviewer) to inject additional signals without
        duplicating the resolution logic.
        """
        for child, counter in votes.items():
            if child not in self._parent_votes:
                self._parent_votes[child] = Counter()
            self._parent_votes[child] += counter
        if votes:
            logger.info(
                "Injected external votes: %d children affected",
                len(votes),
            )

    def propagate_sibling_parents(self, sibling_groups: list[list[str]]) -> None:
        """Propagate parent votes within sibling groups identified by SceneTransitionAnalyzer.

        If sibling group {A, B, C} and B has parent=X in votes, give A→X and C→X
        votes (weight=2). Only propagates when the parent is not a group member
        itself (to avoid circular references).
        """
        propagated = 0
        for group in sibling_groups:
            if len(group) < 2:
                continue
            group_set = set(group)
            # Collect all parent candidates from group members' votes
            group_parents: Counter = Counter()
            for member in group:
                member_votes = self._parent_votes.get(member)
                if not member_votes:
                    continue
                for parent, count in member_votes.items():
                    if parent not in group_set:
                        group_parents[parent] += count

            if not group_parents:
                continue

            # Propagate top parent to all members that lack a vote for it
            best_parent = group_parents.most_common(1)[0][0]
            for member in group:
                if member == best_parent:
                    continue
                existing = self._parent_votes.get(member, Counter()).get(best_parent, 0)
                if existing == 0:
                    self._parent_votes.setdefault(member, Counter())[best_parent] += 2
                    propagated += 1

        if propagated:
            logger.info(
                "Sibling parent propagation: %d groups, %d votes propagated",
                len(sibling_groups), propagated,
            )

    # ── Parent vote resolution ────────────────────────

    async def _rebuild_parent_votes(self) -> dict[str, Counter]:
        """Rebuild parent votes from existing parents (baseline) + chapter facts.

        Existing location_parents are injected as baseline votes (weight=2 each)
        so that accumulated knowledge from the original analysis is preserved.
        Chapter fact votes add on top. When chapter_facts are sparse (e.g., analysis
        was interrupted), the baseline prevents all parents from being wiped out.
        """
        from src.db.sqlite_db import get_connection
        import json as _json

        votes: dict[str, Counter] = {}

        # Inject existing parents as baseline votes (weight=2).
        # These represent accumulated knowledge from the original analysis
        # (heuristics, LLM SET_PARENT, etc.) that isn't stored in chapter_facts.
        if self.structure and self.structure.location_parents:
            for child, parent in self.structure.location_parents.items():
                votes.setdefault(child, Counter())[parent] += 2

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
                (self.novel_id,),
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        tiers = self.structure.location_tiers if self.structure else {}

        # Collect spatial neighbor pairs for post-loop propagation (A.1)
        spatial_neighbors: list[tuple[str, str]] = []

        for row in rows:
            data = _json.loads(row["fact_json"])
            for loc in data.get("locations", []):
                parent = loc.get("parent")
                name = loc.get("name", "")
                if parent and name and name != parent:
                    if not _is_generic_location(name) and not _is_generic_location(parent):
                        votes.setdefault(name, Counter())[parent] += 1
            for sr in data.get("spatial_relationships", []):
                rel_type = sr.get("relation_type", "")
                source = sr.get("source", "")
                target = sr.get("target", "")
                if not source or not target or source == target:
                    continue
                if _is_generic_location(source) or _is_generic_location(target):
                    continue

                # Collect adjacent/direction/in_between pairs for propagation
                if rel_type in ("adjacent", "direction", "in_between"):
                    spatial_neighbors.append((source, target))
                    continue

                if rel_type != "contains":
                    continue
                # Defensive weight reduction for contains relationships
                weight = {"high": 2, "medium": 1, "low": 1}.get(sr.get("confidence", "low"), 1)
                # Direction validation: suffix rank (primary) > tier order (fallback)
                source_suf = _get_suffix_rank(source)
                target_suf = _get_suffix_rank(target)
                if source_suf is not None and target_suf is not None:
                    if source_suf > target_suf:
                        source, target = target, source
                    elif source_suf == target_suf:
                        weight = 1
                else:
                    # Fallback: tier-based direction validation
                    source_tier = tiers.get(source, "city")
                    target_tier = tiers.get(target, "city")
                    source_rank = TIER_ORDER.get(source_tier, 4)
                    target_rank = TIER_ORDER.get(target_tier, 4)
                    if source_rank > target_rank:
                        source, target = target, source
                    elif source_rank == target_rank:
                        if source.startswith(target) and len(source) > len(target):
                            source, target = target, source
                        elif not (target.startswith(source) and len(target) > len(source)):
                            weight = 1
                votes.setdefault(target, Counter())[source] += weight

        # ── Spatial neighbor propagation (adjacent/direction/in_between) ──
        # If A is adjacent/near B and B has a confident parent C, propagate A→C.
        # Up to 2 rounds to allow transitive propagation (A→B→C chain).
        if spatial_neighbors:
            total_propagated = 0
            for _round in range(2):
                propagated = 0
                for a, b in spatial_neighbors:
                    for from_loc, to_loc in [(a, b), (b, a)]:
                        from_votes = votes.get(from_loc)
                        if not from_votes:
                            continue
                        best_parent, best_count = from_votes.most_common(1)[0]
                        if best_parent and best_parent != to_loc and best_count >= 2:
                            existing = votes.get(to_loc, Counter()).get(best_parent, 0)
                            if existing == 0:
                                votes.setdefault(to_loc, Counter())[best_parent] += 1
                                propagated += 1
                total_propagated += propagated
                if propagated == 0:
                    break
            if total_propagated:
                logger.info(
                    "Spatial neighbor propagation: %d pairs, %d votes propagated",
                    len(spatial_neighbors), total_propagated,
                )

        return votes

    def _resolve_parents(self) -> dict[str, str]:
        """Resolve authoritative parents from accumulated votes.

        Algorithm:
        1. For each child, pick the parent with the most votes.
        2. Apply name containment heuristic (fix reversed pairs).
        3. Apply direction validation: suffix rank (primary) > tier order (fallback).
           Suffix rank uses Chinese location name morphology (国>城>谷>洞>殿 etc.)
           and is more reliable than LLM-classified tiers.
        4. Skip entries overridden by user.
        5. Detect and break cycles (remove weakest link).
        """
        assert self.structure is not None

        # Build known-locations set: tiers (classified) + vote keys (extracted as locations).
        # Vote keys are children that the LLM extracted as locations needing parents,
        # so they are definitely locations.  This filters out character names (e.g. 五色蛛)
        # that only appear as vote *values* (potential parents) but were never extracted
        # as a location themselves.
        known_locs: set[str] = set()
        if self.structure.location_tiers:
            known_locs.update(self.structure.location_tiers.keys())
        known_locs.update(self._parent_votes.keys())

        raw: dict[str, str] = {}
        for child, votes in self._parent_votes.items():
            if not votes:
                continue
            # Pick the highest-voted parent that is a known location.
            for winner, _count in votes.most_common():
                if winner and winner != child:
                    if not known_locs or winner in known_locs:
                        raw[child] = winner
                        break

        # ── Name containment heuristic ──
        raw = self._apply_name_containment_heuristic(raw)

        # ── Bidirectional conflict resolution ──
        # When both A→B and B→A exist in raw (both claim the other as parent),
        # resolve deterministically to prevent oscillation across rebuilds.
        resolved_bidir: set[tuple[str, str]] = set()
        for child in list(raw):
            parent = raw.get(child)
            if parent and parent in raw and raw[parent] == child:
                pair = tuple(sorted([child, parent]))
                if pair in resolved_bidir:
                    continue
                resolved_bidir.add(pair)
                a, b = pair  # a < b alphabetically
                a_suf = _get_suffix_rank(a)
                b_suf = _get_suffix_rank(b)
                # Use suffix rank to determine correct direction
                if a_suf is not None and b_suf is not None and a_suf != b_suf:
                    if a_suf < b_suf:
                        # a is bigger → a is parent → keep raw[b]=a, remove raw[a]
                        raw.pop(a, None)
                    else:
                        raw.pop(b, None)
                else:
                    # Same rank or unknown: deterministic alphabetical tiebreak.
                    # The name sorting later alphabetically is child.
                    raw.pop(a, None)  # keep raw[b]=a (b is child of a)
                logger.debug(
                    "Bidirectional resolved: %s ↔ %s (suf=%s/%s)",
                    a, b, a_suf, b_suf,
                )

        # ── Direction validation: suffix rank (primary) > tier order (fallback) ──
        # Suffix rank is derived from Chinese location name morphology (e.g.,
        # 国=kingdom, 城=city, 谷=region) and is more reliable than LLM-classified
        # tiers, which are often chaotic (e.g., a valley classified as "continent").
        validated: dict[str, str] = {}
        for child, parent in raw.items():
            child_suf = _get_suffix_rank(child)
            parent_suf = _get_suffix_rank(parent)

            should_flip = False

            if child_suf is not None and parent_suf is not None:
                # Both have suffix ranks — use name morphology for direction
                if parent_suf > child_suf:
                    should_flip = True
                # If equal or parent_suf < child_suf, direction is correct
            else:
                # Fallback: tier-based validation
                child_tier = self.structure.location_tiers.get(child, "city")
                parent_tier = self.structure.location_tiers.get(parent, "city")
                child_rank = TIER_ORDER.get(child_tier, 4)
                parent_rank = TIER_ORDER.get(parent_tier, 4)
                if parent_rank > child_rank:
                    should_flip = True

            if should_flip:
                validated[parent] = child
                logger.debug(
                    "Direction fix: %s ⊂ %s → reversed (suffix=%s/%s)",
                    child, parent, child_suf, parent_suf,
                )
            else:
                validated[child] = parent

        # Skip user-overridden entries
        result: dict[str, str] = {}
        for child, parent in validated.items():
            if ("location_parent", child) not in self._overridden_keys:
                result[child] = parent

        # Cycle detection: for each node, walk the parent chain.
        # If we revisit a node, there's a cycle — break at the weakest link.
        for start in list(result):
            visited: set[str] = set()
            node = start
            while node in result and node not in visited:
                visited.add(node)
                node = result[node]
            if node in visited:
                # Found a cycle — find the weakest edge in the cycle
                cycle_edges: list[tuple[str, str, int]] = []
                cur = node
                while True:
                    parent = result[cur]
                    count = self._parent_votes.get(cur, Counter()).get(parent, 0)
                    cycle_edges.append((cur, parent, count))
                    cur = parent
                    if cur == node:
                        break
                # Remove the edge with the lowest vote count
                weakest = min(cycle_edges, key=lambda e: e[2])
                del result[weakest[0]]

        return result

    @staticmethod
    def _apply_name_containment_heuristic(raw: dict[str, str]) -> dict[str, str]:
        """Fix reversed parent relationships based on name containment.

        If child_name starts with parent_name (e.g., "黄原汽车站" startswith "黄原"),
        the relationship is correct (child ⊂ parent).
        If parent_name starts with child_name (e.g., "黄原" is child of "黄原汽车站"),
        the relationship is reversed and needs to be flipped.
        """
        result: dict[str, str] = {}
        for child, parent in raw.items():
            if len(child) > len(parent) and child.startswith(parent):
                # "黄原汽车站" startswith "黄原" → child⊂parent is correct
                result[child] = parent
            elif len(parent) > len(child) and parent.startswith(child):
                # "黄原" is marked as child of "黄原汽车站" → reversed!
                result[parent] = child
            else:
                result[child] = parent
        return result

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _extract_excerpt(text: str, pos: int) -> str:
        """Extract ≤200 char excerpt centered on pos."""
        start = max(0, pos - _EXCERPT_HALF)
        end = min(len(text), pos + _EXCERPT_HALF)
        return text[start:end]

    @staticmethod
    def _dedup_signals(
        signals: list[WorldBuildingSignal],
    ) -> list[WorldBuildingSignal]:
        """Remove signals with overlapping excerpts of the same type."""
        if len(signals) <= 1:
            return signals
        seen: set[str] = set()
        deduped: list[WorldBuildingSignal] = []
        for sig in signals:
            # Use first 60 chars of excerpt as dedup key
            key = f"{sig.signal_type}:{sig.raw_text_excerpt[:60]}"
            if key not in seen:
                seen.add(key)
                deduped.append(sig)
        return deduped

    @property
    def pending_signals(self) -> list[WorldBuildingSignal]:
        """Signals accumulated across chapters (consumed by LLM step)."""
        return self._pending_signals

    @property
    def llm_call_count(self) -> int:
        """Number of LLM calls made so far."""
        return self._llm_call_count
