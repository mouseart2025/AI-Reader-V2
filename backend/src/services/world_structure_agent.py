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
from src.infra.config import LLM_PROVIDER
from src.infra.llm_client import LLMClient, get_llm_client
from src.models.chapter_fact import ChapterFact
from src.services.location_hint_service import extract_direction_hint
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
        "修炼", "修仙", "灵气", "法宝", "丹药", "阵法", "飞升", "渡劫",
        "妖", "仙", "魔", "天宫", "天庭", "龙宫", "地府", "结丹", "元婴",
        "灵根", "功法", "法术", "御剑", "遁光", "神通", "洞府", "仙人",
    ],
    "wuxia": [
        "江湖", "门派", "武功", "内力", "武林", "侠", "剑法", "掌法",
        "轻功", "暗器", "镖局", "帮", "盟", "掌门", "弟子", "比武",
    ],
    "historical": [
        "朝廷", "皇帝", "太监", "丞相", "将军", "知府", "知县",
        "年号", "国号", "殿下", "陛下", "圣旨", "科举",
    ],
    "urban": [
        "公司", "学校", "大学", "手机", "电脑", "网络", "办公室",
        "警察", "医院", "地铁", "出租车", "餐厅",
    ],
}

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
                            "SET_TIER", "SET_ICON",
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
        # Urban novels: disable instance detection
        if genre == "urban":
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
        if genre in ("urban", "historical"):
            prompt += (
                "\n\n**重要: 本小说为现实题材，不要创建奇幻/神话类的区域"
                "（如仙界、魔域等）。区域应基于现实地理（省份、城市、地区等）。**"
            )
        elif genre == "fantasy":
            prompt += "\n\n**本小说为奇幻题材，区域可以包含虚构的大陆、界域等。**"

        system = "你是一个小说世界观构建专家。请严格按照 JSON 格式输出。"

        _is_cloud = LLM_PROVIDER == "openai"
        result = await self._llm.generate(
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

    @staticmethod
    def _classify_tier(name: str, loc_type: str, parent: str | None, level: int = 0) -> str:
        """Classify a location into a spatial tier based on name/type heuristics."""
        # world
        if any(kw in name for kw in ("三界", "天下")) or "世界" in loc_type:
            return LocationTier.world.value
        # continent
        if any(kw in name for kw in ("洲", "大陆", "大海")) or any(
            kw in loc_type for kw in ("洲", "域", "界")
        ):
            return LocationTier.continent.value
        # kingdom
        if any(kw in loc_type for kw in ("国", "王国")) or "国" in name:
            return LocationTier.kingdom.value
        # building (check before region fallback to avoid misclassification)
        if any(kw in loc_type for kw in ("殿", "堂", "阁", "楼", "房", "室", "厅")):
            return LocationTier.building.value
        # site
        if any(kw in loc_type for kw in ("洞", "穴", "桥", "渡", "关", "隘", "泉", "潭", "崖")):
            return LocationTier.site.value
        if level >= 2:
            return LocationTier.site.value
        # region — mountains, seas, sects
        if any(kw in loc_type for kw in ("山", "海", "域", "林", "宗", "门", "派")):
            return LocationTier.region.value
        # city
        if any(kw in loc_type for kw in ("城", "镇", "都", "村", "寨", "庄", "寺", "庙", "观", "庵")):
            return LocationTier.city.value
        # region fallback: level-0 locations that aren't kingdoms/continents
        if level == 0 and parent is None and loc_type and not any(
            kw in loc_type for kw in ("城", "镇", "都", "村")
        ):
            return LocationTier.region.value
        # default
        return LocationTier.city.value

    @staticmethod
    def _classify_icon(name: str, loc_type: str) -> str:
        """Classify a location's icon type based on name/type heuristics."""
        combined = name + loc_type
        # cities / settlements
        if any(kw in loc_type for kw in ("城", "镇", "都")):
            return LocationIcon.city.value
        if any(kw in loc_type for kw in ("村", "寨", "庄")):
            return LocationIcon.village.value
        if any(kw in combined for kw in ("营", "帐")):
            return LocationIcon.camp.value
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
        if any(kw in combined for kw in ("洞", "穴")):
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
        if genre == "wuxia":
            return "national"
        if genre == "historical":
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
