"""SpatialCompletionAgent: cross-chapter spatial gap detection + LLM completion.

Phase B of the two-phase spatial enhancement:
- Phase A (in visualization_service._enhance_constraints): no-LLM, real-time
- Phase B (this module): LLM-assisted, background task (6-15 min)

Three-stage gap detection:
  B1: Trajectory graph gaps — adjacent locations on character paths missing direction
  B2: Hierarchy neighbor gaps — siblings/parent-child missing direction
  B3: High-frequency co-occurrence gaps — catch-all for remaining high-value pairs

Results stored in WorldStructure.completed_spatial_relations (top 500 cap).
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path

from src.db import world_structure_store
from src.db.sqlite_db import get_connection
from src.infra.context_budget import get_budget
from src.infra.llm_client import get_llm_client
from src.models.chapter_fact import ChapterFact

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "extraction" / "prompts"

# Maximum number of completed relations to persist (prevents JSON bloat)
MAX_COMPLETED_RELATIONS = 500

# Maximum LLM batches to prevent runaway costs
MAX_LLM_BATCHES = 10

# Pairs per LLM batch
PAIRS_PER_BATCH = 10

# Direction opposites for contradiction detection
_DIRECTION_OPPOSITES: dict[str, str] = {
    "north_of": "south_of", "south_of": "north_of",
    "east_of": "west_of", "west_of": "east_of",
    "northeast_of": "southwest_of", "southwest_of": "northeast_of",
    "northwest_of": "southeast_of", "southeast_of": "northwest_of",
}

# Tier rank for filtering
_TIER_RANK: dict[str, int] = {
    "world": 0, "continent": 1, "kingdom": 2, "region": 3,
    "city": 4, "site": 5, "building": 6,
}

# LLM output schema for spatial completion
_COMPLETION_SCHEMA = {
    "type": "object",
    "properties": {
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "relation_type": {
                        "type": "string",
                        "enum": ["direction", "distance"],
                    },
                    "value": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["source", "target", "relation_type", "value", "confidence", "reason"],
            },
        },
    },
    "required": ["relations"],
}

# Schema for semantic layer review
_LAYER_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "layer": {
                        "type": "string",
                        "enum": ["overworld", "celestial", "underworld", "underwater", "pocket"],
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["location", "layer", "confidence", "reason"],
            },
        },
    },
    "required": ["assignments"],
}


class SpatialCompletionAgent:
    """Cross-chapter spatial relation completion via LLM."""

    def __init__(self, novel_id: str):
        self.novel_id = novel_id
        self._prompt_template = _PROMPTS_DIR / "spatial_completion.txt"

    async def run(self, progress_callback=None) -> dict:
        """Run full spatial completion pipeline.

        Args:
            progress_callback: async callable(stage, message, **extra) for SSE progress

        Returns:
            dict with completion stats
        """
        async def _emit(stage: str, message: str, **extra):
            if progress_callback:
                await progress_callback(stage, message, **extra)

        await _emit("init", "加载分析数据...")

        # Load all chapter facts
        facts = await self._load_all_facts()
        if not facts:
            await _emit("done", "无章节数据，跳过空间补全")
            return {"status": "skipped", "reason": "no_facts"}

        # Load world structure
        ws = await world_structure_store.load(self.novel_id)
        if ws is None:
            await _emit("done", "无世界结构数据，跳过空间补全")
            return {"status": "skipped", "reason": "no_world_structure"}

        # Build location context
        loc_context = self._build_location_context(facts, ws)
        existing_relations = self._collect_existing_relations(facts)

        await _emit("gaps", f"检测空间关系缺口... ({len(loc_context['locations'])} 个地点)")

        # Three-stage gap detection
        gaps = self._detect_gaps(facts, loc_context, existing_relations, ws)
        total_gaps = len(gaps)
        await _emit("gaps", f"发现 {total_gaps} 个需要补全的地点对")

        if not gaps:
            await _emit("done", "无需补全")
            return {"status": "completed", "gaps_found": 0, "relations_added": 0}

        # LLM completion
        await _emit("llm", f"LLM 空间关系补全 (最多 {min(MAX_LLM_BATCHES, (total_gaps + PAIRS_PER_BATCH - 1) // PAIRS_PER_BATCH)} 批)...")

        new_relations = await self._llm_complete(
            gaps, loc_context, existing_relations, _emit,
        )

        await _emit("filter", f"矛盾检测 + 置信度过滤... ({len(new_relations)} 条候选)")

        # Filter: contradiction detection + confidence threshold
        filtered = self._filter_relations(new_relations, existing_relations)

        # Cap at MAX_COMPLETED_RELATIONS
        if len(filtered) > MAX_COMPLETED_RELATIONS:
            # Sort by confidence (high first), then by evidence count
            conf_rank = {"high": 3, "medium": 2, "low": 1}
            filtered.sort(key=lambda r: conf_rank.get(r["confidence"], 0), reverse=True)
            filtered = filtered[:MAX_COMPLETED_RELATIONS]

        # Semantic layer review (only if there are unassigned locations)
        layer_changes = {}
        unassigned = self._find_unassigned_locations(ws, loc_context)
        if unassigned:
            await _emit("layers", f"语义分层审校... ({len(unassigned)} 个待审地点)")
            layer_changes = await self._review_layers(unassigned, loc_context, _emit)

        # Persist results
        ws.completed_spatial_relations = [
            {
                "source": r.get("source", ""),
                "target": r.get("target", ""),
                "relation_type": r.get("relation_type", ""),
                "value": r.get("value", ""),
                "confidence": r.get("confidence", "medium"),
                "evidence_chapters": r.get("evidence_chapters", []),
            }
            for r in filtered
        ]

        # Apply layer changes
        for loc_name, layer_id in layer_changes.items():
            ws.location_layer_map[loc_name] = layer_id

        await world_structure_store.save(self.novel_id, ws)

        stats = {
            "status": "completed",
            "gaps_found": total_gaps,
            "relations_added": len(filtered),
            "layer_changes": len(layer_changes),
        }
        await _emit("done", f"空间补全完成: 新增 {len(filtered)} 条关系, {len(layer_changes)} 个层调整",
                     result=stats)
        return stats

    async def _load_all_facts(self) -> list[ChapterFact]:
        """Load all chapter facts for this novel."""
        conn = await get_connection()
        try:
            cursor = await conn.execute(
                """
                SELECT cf.fact_json, c.chapter_num
                FROM chapter_facts cf
                JOIN chapters c ON cf.chapter_id = c.id AND cf.novel_id = c.novel_id
                WHERE cf.novel_id = ?
                ORDER BY c.chapter_num
                """,
                (self.novel_id,),
            )
            rows = await cursor.fetchall()
            facts = []
            for row in rows:
                data = json.loads(row["fact_json"])
                data["chapter_id"] = row["chapter_num"]
                data["novel_id"] = self.novel_id
                facts.append(ChapterFact.model_validate(data))
            return facts
        finally:
            await conn.close()

    def _build_location_context(self, facts: list[ChapterFact], ws) -> dict:
        """Build location metadata for gap detection and LLM prompt."""
        loc_chapters: dict[str, set[int]] = defaultdict(set)
        loc_descriptions: dict[str, list[str]] = defaultdict(list)
        loc_cooccurrence: Counter = Counter()
        trajectories: dict[str, list[dict]] = defaultdict(list)

        for fact in facts:
            ch = fact.chapter_id
            chapter_locs = set()
            for loc in fact.locations:
                loc_chapters[loc.name].add(ch)
                if loc.description:
                    loc_descriptions[loc.name].append(loc.description)
                chapter_locs.add(loc.name)

            # Co-occurrence counting
            loc_list = sorted(chapter_locs)
            for i in range(len(loc_list)):
                for j in range(i + 1, len(loc_list)):
                    loc_cooccurrence[(loc_list[i], loc_list[j])] += 1

            # Trajectories
            for char in fact.characters:
                for loc_name in char.locations_in_chapter:
                    trajectories[char.name].append({
                        "location": loc_name,
                        "chapter": ch,
                    })

        return {
            "locations": set(loc_chapters.keys()),
            "loc_chapters": dict(loc_chapters),
            "loc_descriptions": dict(loc_descriptions),
            "loc_cooccurrence": loc_cooccurrence,
            "trajectories": dict(trajectories),
            "tiers": getattr(ws, "location_tiers", {}),
            "parents": getattr(ws, "location_parents", {}),
        }

    def _collect_existing_relations(self, facts: list[ChapterFact]) -> dict[tuple[str, str, str], dict]:
        """Collect all existing spatial relations from chapter facts."""
        existing: dict[tuple[str, str, str], dict] = {}
        for fact in facts:
            for sr in fact.spatial_relationships:
                key = (sr.source, sr.target, sr.relation_type)
                existing[key] = {
                    "source": sr.source,
                    "target": sr.target,
                    "relation_type": sr.relation_type,
                    "value": sr.value,
                    "confidence": sr.confidence,
                }
        return existing

    def _detect_gaps(
        self,
        facts: list[ChapterFact],
        loc_context: dict,
        existing: dict,
        ws,
    ) -> list[dict]:
        """Three-stage gap detection: trajectory → hierarchy → co-occurrence."""
        gaps: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()
        existing_pairs: set[tuple[str, str]] = set()
        for key in existing:
            existing_pairs.add((key[0], key[1]))
            existing_pairs.add((key[1], key[0]))

        tiers = loc_context["tiers"]
        parents = loc_context["parents"]
        loc_chapters = loc_context["loc_chapters"]
        loc_cooccurrence = loc_context["loc_cooccurrence"]
        trajectories = loc_context["trajectories"]

        # ── B1: Trajectory gaps ──
        for _person, path in trajectories.items():
            for i in range(len(path) - 1):
                a, b = path[i]["location"], path[i + 1]["location"]
                if a == b:
                    continue
                pair = tuple(sorted([a, b]))
                if pair in seen_pairs or pair in existing_pairs:
                    continue
                # Tier gap check
                tier_a = _TIER_RANK.get(tiers.get(a, "city"), 4)
                tier_b = _TIER_RANK.get(tiers.get(b, "city"), 4)
                if abs(tier_a - tier_b) > 1:
                    continue
                seen_pairs.add(pair)
                # Compute priority
                co = loc_cooccurrence.get(pair, 0) + loc_cooccurrence.get((pair[1], pair[0]), 0)
                mention = len(loc_chapters.get(a, set())) + len(loc_chapters.get(b, set()))
                priority = co * mention
                gaps.append({
                    "source": pair[0], "target": pair[1],
                    "gap_type": "trajectory",
                    "priority": priority,
                    "evidence_chapters": sorted(
                        loc_chapters.get(a, set()) & loc_chapters.get(b, set())
                    )[:5],
                })

        # ── B2: Hierarchy neighbor gaps (siblings + parent-child) ──
        children_of: dict[str, list[str]] = defaultdict(list)
        for child, parent in parents.items():
            children_of[parent].append(child)

        for parent, children in children_of.items():
            for i in range(len(children)):
                for j in range(i + 1, len(children)):
                    a, b = children[i], children[j]
                    pair = tuple(sorted([a, b]))
                    if pair in seen_pairs or pair in existing_pairs:
                        continue
                    # Co-occurrence threshold
                    co = loc_cooccurrence.get(pair, 0) + loc_cooccurrence.get((pair[1], pair[0]), 0)
                    if co < 2:
                        continue
                    seen_pairs.add(pair)
                    mention = len(loc_chapters.get(a, set())) + len(loc_chapters.get(b, set()))
                    gaps.append({
                        "source": pair[0], "target": pair[1],
                        "gap_type": "hierarchy",
                        "priority": co * mention,
                        "evidence_chapters": sorted(
                            loc_chapters.get(a, set()) & loc_chapters.get(b, set())
                        )[:5],
                    })

        # ── B3: High-frequency co-occurrence catch-all ──
        top_pairs = loc_cooccurrence.most_common(200)
        for (a, b), co in top_pairs:
            if co < 2:
                break
            pair = tuple(sorted([a, b]))
            if pair in seen_pairs or pair in existing_pairs:
                continue
            # Tier gap check
            tier_a = _TIER_RANK.get(tiers.get(a, "city"), 4)
            tier_b = _TIER_RANK.get(tiers.get(b, "city"), 4)
            if abs(tier_a - tier_b) > 2:
                continue
            seen_pairs.add(pair)
            mention = len(loc_chapters.get(a, set())) + len(loc_chapters.get(b, set()))
            gaps.append({
                "source": pair[0], "target": pair[1],
                "gap_type": "cooccurrence",
                "priority": co * mention,
                "evidence_chapters": sorted(
                    loc_chapters.get(a, set()) & loc_chapters.get(b, set())
                )[:5],
            })

        # Sort by priority descending, cap total to avoid excessive LLM calls
        gaps.sort(key=lambda g: g["priority"], reverse=True)
        max_gaps = MAX_LLM_BATCHES * PAIRS_PER_BATCH
        return gaps[:max_gaps]

    async def _llm_complete(
        self,
        gaps: list[dict],
        loc_context: dict,
        existing: dict,
        emit,
    ) -> list[dict]:
        """Run LLM completion in batches."""
        llm = get_llm_client()
        budget = get_budget()
        logger.info("SpatialCompletion LLM client: %s, model=%s",
                     type(llm).__name__, getattr(llm, 'model', '?'))
        template = self._prompt_template.read_text(encoding="utf-8")

        all_results: list[dict] = []
        batches = [gaps[i:i + PAIRS_PER_BATCH] for i in range(0, len(gaps), PAIRS_PER_BATCH)]
        batches = batches[:MAX_LLM_BATCHES]

        # Build known relations text (for context)
        known_lines = []
        for (src, tgt, rtype), rel in list(existing.items())[:100]:
            known_lines.append(f"  {src} → {tgt}: {rtype}={rel['value']} ({rel['confidence']})")
        known_text = "\n".join(known_lines) if known_lines else "（无）"

        for batch_idx, batch in enumerate(batches):
            await emit("llm", f"LLM 补全批次 {batch_idx + 1}/{len(batches)} ({len(batch)} 对)...")

            # Build pairs text with evidence
            pairs_lines = []
            for gap in batch:
                src, tgt = gap["source"], gap["target"]
                descs_src = loc_context["loc_descriptions"].get(src, [])
                descs_tgt = loc_context["loc_descriptions"].get(tgt, [])
                desc_text = ""
                if descs_src:
                    desc_text += f"  {src} 描述: {descs_src[0][:100]}\n"
                if descs_tgt:
                    desc_text += f"  {tgt} 描述: {descs_tgt[0][:100]}\n"
                chapters = gap.get("evidence_chapters", [])
                ch_text = f"  共现章节: {chapters}" if chapters else ""
                pairs_lines.append(f"- {src} ↔ {tgt} (类型: {gap['gap_type']})\n{desc_text}{ch_text}")

            pairs_text = "\n".join(pairs_lines)
            prompt = template.format(pairs_text=pairs_text, known_relations=known_text)

            try:
                result, _usage = await llm.generate(
                    system="你是一个小说地理分析专家。请严格按照 JSON 格式输出。",
                    prompt=prompt,
                    format=_COMPLETION_SCHEMA,
                    temperature=0.1,
                    max_tokens=budget.ws_max_tokens,
                    timeout=max(budget.ws_timeout, 180),  # at least 3 min per batch
                )

                if isinstance(result, str):
                    result = json.loads(result)

                # Handle both {relations: [...]} and direct [...] formats
                if isinstance(result, list):
                    relations = result
                else:
                    relations = result.get("relations", [])
                # Tag with evidence chapters from gap detection
                gap_map = {(g["source"], g["target"]): g for g in batch}
                gap_map.update({(g["target"], g["source"]): g for g in batch})
                for rel in relations:
                    if not isinstance(rel, dict):
                        continue
                    src = rel.get("source", "")
                    tgt = rel.get("target", "")
                    if src and tgt:
                        gap_info = gap_map.get((src, tgt))
                        if gap_info:
                            rel["evidence_chapters"] = gap_info.get("evidence_chapters", [])
                all_results.extend(relations)
                if relations:
                    logger.debug("Sample LLM relation: %s", relations[0])

                logger.info(
                    "Spatial completion batch %d/%d: %d relations",
                    batch_idx + 1, len(batches), len(relations),
                )
            except Exception as exc:
                logger.warning(
                    "Spatial completion batch %d failed: %s",
                    batch_idx + 1, exc,
                    exc_info=True,
                )
                await emit("llm", f"批次 {batch_idx + 1} 失败: {type(exc).__name__}: {str(exc)[:80]}")

        return all_results

    def _filter_relations(
        self,
        new_relations: list[dict],
        existing: dict,
    ) -> list[dict]:
        """Filter out contradictions and low-confidence results."""
        filtered = []
        existing_directions: dict[tuple[str, str], str] = {}
        for (src, tgt, rtype), rel in existing.items():
            if rtype == "direction":
                existing_directions[(src, tgt)] = rel["value"]

        skipped_malformed = 0
        skipped_low_conf = 0
        skipped_unknown = 0
        skipped_contradiction = 0
        for rel in new_relations:
            # Skip malformed entries
            if not isinstance(rel, dict):
                skipped_malformed += 1
                continue
            if not rel.get("source") or not rel.get("target"):
                skipped_malformed += 1
                continue
            # Normalize: some LLMs use "type" instead of "relation_type"
            if "relation_type" not in rel and "type" in rel:
                rel["relation_type"] = rel["type"]
            # Fix: LLM sometimes puts direction value in relation_type
            _direction_values = {
                "north_of", "south_of", "east_of", "west_of",
                "northeast_of", "northwest_of", "southeast_of", "southwest_of",
            }
            if rel.get("relation_type") in _direction_values:
                rel["value"] = rel["relation_type"]
                rel["relation_type"] = "direction"
            # Fix: LLM sometimes puts distance value in relation_type
            _distance_values = {"adjacent", "near", "far", "contains"}
            if rel.get("relation_type") in _distance_values:
                rel["value"] = rel["relation_type"]
                rel["relation_type"] = "distance"
            if not rel.get("relation_type"):
                skipped_malformed += 1
                continue
            # Skip low confidence
            if rel.get("confidence") == "low":
                skipped_low_conf += 1
                continue
            # Skip unknown/unresolvable
            if rel.get("value") in ("unknown", "无法推断", "无法判断"):
                skipped_unknown += 1
                continue

            src, tgt = rel["source"], rel["target"]

            # Direction contradiction check
            if rel["relation_type"] == "direction":
                opposite = _DIRECTION_OPPOSITES.get(rel["value"])
                # Check if reverse direction already exists
                existing_dir = existing_directions.get((tgt, src))
                if existing_dir and existing_dir == rel["value"]:
                    skipped_contradiction += 1
                    continue  # Contradicts: A→B=north but B→A=north
                if existing_dir == opposite:
                    skipped_contradiction += 1
                    continue  # Redundant: already have the reverse
                # Check self-contradiction within new relations
                existing_new = existing_directions.get((src, tgt))
                if existing_new and existing_new != rel["value"]:
                    skipped_contradiction += 1
                    continue  # Contradicts previous new relation

                existing_directions[(src, tgt)] = rel["value"]

            filtered.append(rel)

        logger.info(
            "Filter stats: %d input → %d passed, %d malformed, %d low_conf, %d unknown, %d contradiction",
            len(new_relations), len(filtered), skipped_malformed, skipped_low_conf,
            skipped_unknown, skipped_contradiction,
        )
        if new_relations and not filtered:
            # Log sample of what was filtered for debugging
            sample = new_relations[:3]
            logger.warning("All relations filtered! Sample: %s", sample)
        return filtered

    def _find_unassigned_locations(self, ws, loc_context: dict) -> list[str]:
        """Find locations still on overworld that might belong to a different layer."""
        unassigned = []
        for loc_name in loc_context["locations"]:
            layer = ws.location_layer_map.get(loc_name, "overworld")
            if layer == "overworld":
                # Check if parent is on a non-overworld layer
                parent = ws.location_parents.get(loc_name)
                if parent and ws.location_layer_map.get(parent, "overworld") != "overworld":
                    unassigned.append(loc_name)
        return unassigned

    async def _review_layers(
        self,
        unassigned: list[str],
        loc_context: dict,
        emit,
    ) -> dict[str, str]:
        """LLM review of layer assignments for ambiguous locations."""
        if not unassigned:
            return {}

        llm = get_llm_client()
        budget = get_budget()

        # Build location list with descriptions
        loc_lines = []
        for name in unassigned[:50]:  # Cap to prevent huge prompts
            descs = loc_context["loc_descriptions"].get(name, [])
            desc = descs[0][:100] if descs else "（无描述）"
            loc_lines.append(f"- {name}: {desc}")

        prompt = (
            "以下地点目前被分配在主世界(overworld)层，但它们的父级地点在其他层。\n"
            "请判断这些地点应该属于哪个层：\n\n"
            + "\n".join(loc_lines)
            + "\n\n层选项：overworld（地表主世界）、celestial（天界/天空）、"
            "underworld（冥界/地下）、underwater（海底/龙宫）、pocket（秘境/副本）\n\n"
            "默认原则：除非有明确叙事证据，否则保持 overworld。"
        )

        try:
            result, _usage = await llm.generate(
                system="你是一个小说世界观分析专家。请严格按照 JSON 格式输出。",
                prompt=prompt,
                format=_LAYER_REVIEW_SCHEMA,
                temperature=0.1,
                max_tokens=2048,
                timeout=budget.hierarchy_timeout,
            )

            if isinstance(result, str):
                result = json.loads(result)

            changes: dict[str, str] = {}
            for assignment in result.get("assignments", []):
                if assignment["confidence"] in ("high", "medium"):
                    new_layer = assignment["layer"]
                    if new_layer != "overworld":
                        changes[assignment["location"]] = new_layer

            logger.info("Layer review: %d changes from %d candidates", len(changes), len(unassigned))
            return changes
        except Exception:
            logger.warning("Layer review LLM failed, skipping", exc_info=True)
            await emit("layers", "层审校 LLM 失败，跳过")
            return {}
