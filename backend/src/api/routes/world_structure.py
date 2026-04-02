"""WorldStructure API endpoints."""

import json
import logging
from collections import Counter

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.db import novel_store, world_structure_store, world_structure_override_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/novels/{novel_id}/world-structure", tags=["world-structure"])


async def _redetect_genre(novel_id: str, agent) -> None:
    """Re-detect genre from first 10 chapter texts using updated keyword lists.

    This fixes genre misdetection for novels analyzed with older keyword lists
    (e.g., Water Margin classified as 'fantasy' due to broad single-char keywords).
    """
    from src.db.sqlite_db import get_connection
    import json as _json
    from src.models.chapter_fact import ChapterFact

    ws = agent.structure
    if ws is None:
        return

    # Reset genre detection state
    ws.novel_genre_hint = None
    if hasattr(agent, "_genre_scores"):
        del agent._genre_scores

    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """SELECT c.chapter_num, c.content, cf.fact_json
            FROM chapters c
            JOIN chapter_facts cf ON c.id = cf.chapter_id AND c.novel_id = cf.novel_id
            WHERE c.novel_id = ? AND c.chapter_num <= 10
            ORDER BY c.chapter_num""",
            (novel_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    for row in rows:
        chapter_text = row["content"] or ""
        data = _json.loads(row["fact_json"])
        fact = ChapterFact.model_validate({**data, "chapter_id": row["chapter_num"], "novel_id": novel_id})
        agent._detect_genre(chapter_text, fact)

    # Also recalculate spatial scale
    ws.spatial_scale = agent._detect_spatial_scale()

    logger.info(
        "Re-detected genre=%s, scale=%s for novel %s",
        ws.novel_genre_hint, ws.spatial_scale, novel_id,
    )


class OverrideItem(BaseModel):
    override_type: str
    override_key: str
    override_json: dict


class OverridesBatch(BaseModel):
    overrides: list[OverrideItem]


@router.get("")
async def get_world_structure(novel_id: str):
    """Return the WorldStructure for a novel (with overrides applied)."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    ws = await world_structure_store.load_with_overrides(novel_id)
    return ws.model_dump()


@router.get("/overrides")
async def get_overrides(novel_id: str):
    """Return all user overrides for this novel's world structure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    overrides = await world_structure_override_store.load_overrides(novel_id)
    return {"overrides": overrides}


@router.put("/overrides")
async def save_overrides(novel_id: str, body: OverridesBatch):
    """Save a batch of overrides and return the merged WorldStructure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    valid_types = {"location_region", "location_layer", "location_parent", "location_tier", "add_portal", "delete_portal"}
    for item in body.overrides:
        if item.override_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 override_type: {item.override_type}",
            )

    for item in body.overrides:
        await world_structure_override_store.save_override(
            novel_id, item.override_type, item.override_key, item.override_json,
        )

    # Invalidate layout cache since structure changed
    await world_structure_store.delete_layer_layouts(novel_id)

    ws = await world_structure_store.load_with_overrides(novel_id)
    return ws.model_dump()


def _compute_hierarchy_diff(
    old: dict[str, str],
    new: dict[str, str],
    known_locations: set[str] | None = None,
) -> list[dict]:
    """Compute the diff between old and new location_parents mappings.

    Each change includes ``auto_select`` indicating whether it should be
    checked by default in the UI.  Rules:
    - "removed" → always false (removals are usually regressions)
    - "changed" where the child name contains the old parent → false
      (name-containment relationships should not be broken)
    - "added" / "changed" where new_parent is not a known location → false
      (prevents character names and non-location entities from becoming parents)
    """
    changes: list[dict] = []
    all_keys = sorted(set(old) | set(new))
    for loc in all_keys:
        old_p = old.get(loc)
        new_p = new.get(loc)
        if old_p == new_p:
            continue
        if old_p is None:
            change_type = "added"
        elif new_p is None:
            change_type = "removed"
        else:
            change_type = "changed"

        # Determine auto_select
        auto_select = True
        reason = ""
        if change_type == "removed":
            auto_select = False
            reason = "移除现有父级关系通常是回退"
        elif change_type == "changed" and old_p and old_p in loc:
            # Child name contains old parent (e.g., 中心区的南边 contains 中心区)
            auto_select = False
            reason = f"地点名包含原父级「{old_p}」"
        elif new_p and known_locations and new_p not in known_locations:
            auto_select = False
            reason = f"新父级「{new_p}」不是已知地点"

        changes.append({
            "location": loc,
            "old_parent": old_p,
            "new_parent": new_p,
            "change_type": change_type,
            "auto_select": auto_select,
            "reason": reason,
        })

    # Sort: added first, then changed, then removed
    type_order = {"added": 0, "changed": 1, "removed": 2}
    changes.sort(key=lambda c: (type_order.get(c["change_type"], 9), c["location"]))
    return changes


def _count_roots(parents: dict[str, str]) -> tuple[int, list[str]]:
    """Count root nodes (parents that are not children of anything)."""
    children = set(parents.keys())
    parent_set = set(parents.values())
    roots = sorted(parent_set - children)
    return len(roots), roots


class HierarchyChangeItem(BaseModel):
    location: str
    new_parent: str | None


class HierarchyChangesRequest(BaseModel):
    changes: list[HierarchyChangeItem]
    location_tiers: dict[str, str] | None = None


def _sse(stage: str, message: str, **extra) -> str:
    """Format an SSE event line."""
    payload = {"stage": stage, "message": message, **extra}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/rebuild-hierarchy")
async def rebuild_hierarchy(novel_id: str):
    """Preview hierarchy rebuild with SSE progress streaming.

    Integrates SceneTransitionAnalyzer and LocationHierarchyReviewer for
    improved hierarchy quality. Streams progress events, then returns a
    diff for user confirmation as the final 'done' event.
    """
    # Validate upfront (before entering the generator) so errors return proper HTTP status
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    ws = await world_structure_store.load(novel_id)
    if not ws:
        raise HTTPException(status_code=404, detail="WorldStructure 不存在，请先分析小说")

    async def event_stream():
        try:
            from src.services.world_structure_agent import WorldStructureAgent
            from src.infra.config import get_model_name, LLM_PROVIDER

            agent = WorldStructureAgent(novel_id)
            agent.structure = ws

            yield _sse("init", "正在重新检测小说类型...")

            # Re-detect genre from chapter texts
            await _redetect_genre(novel_id, agent)

            # Load user overrides so they take precedence
            overrides = await world_structure_override_store.load_overrides(novel_id)
            for ov in overrides:
                agent._overridden_keys.add((ov["override_type"], ov["override_key"]))

            # 0.5. Re-classify stale tiers using current (fixed) classification logic
            # Fixes locations that got "city" tier from old Layer 4 fallback
            from src.services.world_structure_agent import _get_suffix_rank
            retier_count = 0
            for loc_name in list(ws.location_tiers.keys()):
                if ("location_tier", loc_name) in agent._overridden_keys:
                    continue
                parent = ws.location_parents.get(loc_name)
                level = 1 if parent else 0
                new_tier = agent._classify_tier(loc_name, "", parent, level)
                old_tier = ws.location_tiers[loc_name]
                if new_tier != old_tier:
                    ws.location_tiers[loc_name] = new_tier
                    retier_count += 1
            if retier_count:
                yield _sse("init", f"修正 {retier_count} 个过时的层级分类")

            # 0.7. Re-detect layers (realm/instance assignment)
            # Clears stale instance_* layers and re-runs detection with current keywords
            yield _sse("init", "正在重新检测世界层级...")
            from src.services.world_structure_agent import (
                _CELESTIAL_KEYWORDS as _ck,
                _UNDERWORLD_KEYWORDS as _uk,
                _REALM_LAYER_KEYWORDS,
                _INSTANCE_NAME_KEYWORDS,
                _INSTANCE_TYPE_KEYWORDS as _itk,
            )
            # Remove stale instance_* layers and their assignments
            stale_instance_ids = {
                l.layer_id for l in ws.layers
                if l.layer_id.startswith("instance_")
            }
            if stale_instance_ids:
                ws.layers = [l for l in ws.layers if l.layer_id not in stale_instance_ids]
                for loc_name, lid in list(ws.location_layer_map.items()):
                    if lid in stale_instance_ids:
                        ws.location_layer_map[loc_name] = "overworld"

            # Re-run layer detection on all known locations
            layer_reassign_count = 0
            all_loc_names = set(ws.location_tiers.keys())
            # Build a simple type lookup from the latest chapter_facts
            loc_types: dict[str, str] = {}
            try:
                from src.db import chapter_fact_store as _cfs
                import asyncio as _aio_layer
                all_facts = await _cfs.get_all_chapter_facts(novel_id)
                for _f in all_facts:
                    for _loc in _f.get("locations", []):
                        _ln = _loc.get("name", "")
                        if _ln and _ln not in loc_types:
                            loc_types[_ln] = _loc.get("type", "")
            except Exception:
                pass

            for loc_name in all_loc_names:
                if ("location_layer", loc_name) in agent._overridden_keys:
                    continue
                loc_type = loc_types.get(loc_name, "")
                new_layer = agent._detect_layer(loc_name, loc_type)
                if new_layer:
                    agent._ensure_layer_exists(new_layer)
                    old_layer = ws.location_layer_map.get(loc_name, "overworld")
                    if old_layer != new_layer:
                        ws.location_layer_map[loc_name] = new_layer
                        layer_reassign_count += 1
                elif ws.location_layer_map.get(loc_name) != "overworld":
                    # Check instance keywords
                    is_instance = (
                        agent._is_instance_detection_enabled()
                        and (
                            any(kw in loc_name for kw in _INSTANCE_NAME_KEYWORDS)
                            or any(kw in loc_type for kw in _itk)
                        )
                    )
                    if is_instance:
                        from src.models.world_structure import MapLayer, LayerType
                        _pk_id = "pockets"
                        if not agent._has_layer(_pk_id):
                            ws.layers.append(MapLayer(
                                layer_id=_pk_id, name="副本/秘境",
                                layer_type=LayerType.pocket,
                                description="秘境、禁地、洞天、幻境等独立空间",
                            ))
                        ws.location_layer_map[loc_name] = _pk_id
                        layer_reassign_count += 1
                    else:
                        # Reset non-matching to overworld
                        old_layer = ws.location_layer_map.get(loc_name)
                        if old_layer and old_layer != "overworld":
                            ws.location_layer_map[loc_name] = "overworld"
                            layer_reassign_count += 1

            if layer_reassign_count or stale_instance_ids:
                yield _sse("init", f"层级重检: 清理 {len(stale_instance_ids)} 个旧副本, 重新分配 {layer_reassign_count} 个地点")

            # 1. Rebuild parent votes
            yield _sse("votes", "正在从章节事实重建投票数据...")
            agent._parent_votes = await agent._rebuild_parent_votes()

            # Report frequency stats
            _freq = agent._location_frequencies
            _n_core = sum(1 for c in _freq.values() if c >= 10)
            _n_regular = sum(1 for c in _freq.values() if 3 <= c <= 9)
            _n_micro = sum(1 for c in _freq.values() if c <= 2)
            yield _sse("votes", f"频率分级: {_n_core} 核心(≥10) + {_n_regular} 常规(3-9) + {_n_micro} 微观(≤2)")

            # 1.1 Location alias normalization (Story 2.3)
            alias_merge_map = agent.normalize_location_aliases(agent._parent_votes)
            if alias_merge_map:
                yield _sse("votes", f"合并 {len(alias_merge_map)} 组地点名变体（如{'、'.join(f'{k}→{v}' for k, v in list(alias_merge_map.items())[:3])}）")

            # 1.5 Macro-skeleton generation
            yield _sse("skeleton", "正在生成宏观地理骨架...")
            skeleton_synonyms: list[tuple[str, str]] = []
            skeleton_directions: list[dict] = []
            skeleton_success = False  # Track if skeleton produced useful output
            try:
                import asyncio as _asyncio_skel
                from src.services.macro_skeleton_generator import MacroSkeletonGenerator
                skel_gen = MacroSkeletonGenerator()
                skeleton_votes, skeleton_synonyms, skeleton_directions = await _asyncio_skel.wait_for(
                    skel_gen.generate(
                        novel_title=novel.get("title", ""),
                        novel_genre_hint=ws.novel_genre_hint,
                        location_tiers=ws.location_tiers,
                        current_parents=ws.location_parents,
                        location_frequencies=agent._location_frequencies,
                    ),
                    timeout=300.0,  # v0.67: 150→300s for deeper 4-5 level skeleton prompts
                )
                parts = []
                if skeleton_votes:
                    agent.inject_external_votes(skeleton_votes)
                    parts.append(f"{len(skeleton_votes)} 个地点获得锚定")
                if skeleton_synonyms:
                    parts.append(f"{len(skeleton_synonyms)} 组同义合并")
                if skeleton_directions:
                    existing_keys = {
                        (r["source"], r["target"], r.get("value", ""))
                        for r in ws.completed_spatial_relations
                    }
                    for d in skeleton_directions:
                        key = (d["source"], d["target"], d["value"])
                        if key not in existing_keys:
                            ws.completed_spatial_relations.append(d)
                    parts.append(f"{len(skeleton_directions)} 组方位锚定")
                if parts:
                    skeleton_success = True
                    # Cache successful skeleton for reuse on future timeouts
                    ws.cached_skeleton = {
                        "votes": {k: dict(v) for k, v in skeleton_votes.items()} if skeleton_votes else {},
                        "synonyms": skeleton_synonyms,
                        "directions": skeleton_directions,
                    }
                    # Persist cache immediately so future timeouts can reuse
                    await world_structure_store.save(novel_id, ws)
                    yield _sse("skeleton", f"骨架生成完成，{'，'.join(parts)}")
                else:
                    # LLM returned empty result (internal error or no suggestions)
                    # Fall through to cache check below
                    raise ValueError("骨架生成无建议")
            except (_asyncio_skel.TimeoutError, Exception) as _skel_err:
                is_timeout = isinstance(_skel_err, _asyncio_skel.TimeoutError)
                # v0.63.0: Reuse cached skeleton on timeout/failure
                if ws.cached_skeleton and ws.cached_skeleton.get("votes"):
                    cached = ws.cached_skeleton
                    cached_votes_raw = cached["votes"]
                    # Rebuild Counter objects from cached dict
                    skeleton_votes_cached: dict[str, Counter] = {}
                    for k, v in cached_votes_raw.items():
                        skeleton_votes_cached[k] = Counter(v)
                    if skeleton_votes_cached:
                        agent.inject_external_votes(skeleton_votes_cached)
                    skeleton_synonyms = cached.get("synonyms", [])
                    skeleton_directions = cached.get("directions", [])
                    if skeleton_directions:
                        existing_keys = {
                            (r["source"], r["target"], r.get("value", ""))
                            for r in ws.completed_spatial_relations
                        }
                        for d in skeleton_directions:
                            key = (d["source"], d["target"], d["value"])
                            if key not in existing_keys:
                                ws.completed_spatial_relations.append(d)
                    skeleton_success = True
                    label = "超时" if is_timeout else "失败"
                    yield _sse("skeleton", f"骨架生成{label}，使用缓存骨架（{len(cached_votes_raw)} 个锚定）")
                    logger.info(
                        "Skeleton %s, using cached skeleton (%d votes) for %s",
                        "timed out" if is_timeout else "failed",
                        len(cached_votes_raw), novel_id,
                    )
                else:
                    if is_timeout:
                        logger.warning("Macro skeleton generation timed out for %s (no cache)", novel_id)
                        yield _sse("skeleton", "骨架生成超时，无缓存可用")
                    else:
                        logger.warning("Macro skeleton generation failed for %s (no cache)", novel_id, exc_info=True)
                        yield _sse("skeleton", "骨架生成失败，无缓存可用")

            # 2. Scene transition analysis
            scene_analysis_used = False
            scene_analysis: dict = {}
            try:
                from src.db import chapter_fact_store
                all_scenes = await chapter_fact_store.get_all_scenes(novel_id)
                if all_scenes:
                    yield _sse("scene", f"正在分析场景转换 ({len(all_scenes)} 个场景)...")
                    from src.services.scene_transition_analyzer import SceneTransitionAnalyzer
                    analyzer = SceneTransitionAnalyzer()
                    scene_votes, scene_analysis = analyzer.analyze(all_scenes)
                    if scene_votes:
                        agent.inject_external_votes(scene_votes)
                        scene_analysis_used = True
                    # Propagate parent votes within sibling groups
                    if scene_analysis.get("sibling_groups"):
                        agent.propagate_sibling_parents(scene_analysis["sibling_groups"])
                else:
                    yield _sse("scene", "无场景数据，跳过场景分析")
            except Exception:
                logger.warning("Scene transition analysis failed for %s", novel_id, exc_info=True)
                yield _sse("scene", "场景分析失败，已跳过")

            # 3. LLM hierarchy review (only if orphan roots >= 3)
            llm_review_used = False
            temp_parents = agent._resolve_parents()
            temp_root_count, _ = _count_roots(temp_parents)
            if temp_root_count >= 3:
                model_name = get_model_name()
                provider_label = "Cloud" if LLM_PROVIDER == "openai" else "Ollama"
                yield _sse(
                    "llm",
                    f"正在调用 LLM 审查层级 ({provider_label}: {model_name})...",
                    model=model_name,
                    provider=provider_label,
                )
                try:
                    import asyncio as _asyncio_review
                    from src.services.location_hierarchy_reviewer import LocationHierarchyReviewer
                    reviewer = LocationHierarchyReviewer()
                    review_votes = await _asyncio_review.wait_for(
                        reviewer.review(
                            ws.location_tiers,
                            temp_parents,
                            scene_analysis,
                            ws.novel_genre_hint,
                        ),
                        timeout=240.0,  # v0.67: 120→240s for cloud models with larger hierarchies
                    )
                    if review_votes:
                        agent.inject_external_votes(review_votes)
                        llm_review_used = True
                        yield _sse("llm", f"LLM 审查完成，获得 {len(review_votes)} 条建议")
                    else:
                        yield _sse("llm", "LLM 审查完成，无新建议")
                except _asyncio_review.TimeoutError:
                    logger.warning("LLM hierarchy review timed out for %s", novel_id)
                    yield _sse("llm", "LLM 审查超时(>90s)，使用纯算法结果")
                except Exception:
                    logger.warning("LLM hierarchy review failed for %s", novel_id, exc_info=True)
                    yield _sse("llm", "LLM 审查失败，使用纯算法结果")
            else:
                yield _sse("llm", f"根节点仅 {temp_root_count} 个，跳过 LLM 审查")

            # 3.5 Close-vote detection (Story 3.1 FR9) — feed into suspicious pairs
            _close_vote_pairs = 0
            for child, counter in agent._parent_votes.items():
                if len(counter) >= 2:
                    top2 = counter.most_common(2)
                    first_votes = top2[0][1]
                    second_votes = top2[1][1]
                    if first_votes > 0 and second_votes / first_votes > 0.8:
                        agent._suspicious_pairs.append({
                            "child": child,
                            "parent": top2[0][0],
                            "reasons": ["close_votes"],
                            "child_tier": ws.location_tiers.get(child, "unknown"),
                            "parent_tier": ws.location_tiers.get(top2[0][0], "unknown"),
                            "alternative_parent": top2[1][0],
                        })
                        _close_vote_pairs += 1
            if _close_vote_pairs:
                yield _sse("votes", f"检测到 {_close_vote_pairs} 对并列票地点，将交由 LLM 裁决")

            # 4. Final resolution
            yield _sse("consolidate", "正在合并与优化层级结构...")
            new_parents = agent._resolve_parents()

            from src.services.hierarchy_consolidator import consolidate_hierarchy
            new_parents, new_tiers = consolidate_hierarchy(
                new_parents,
                dict(ws.location_tiers),
                novel_genre_hint=ws.novel_genre_hint,
                parent_votes=agent._parent_votes,
                saved_parents=dict(ws.location_parents),
                synonym_pairs=skeleton_synonyms,
            )

            # 4.1a. Transitivity check (Story 2.1)
            from src.services.world_structure_agent import WorldStructureAgent as _WSA
            transitivity_violations = _WSA._check_transitivity(new_parents)
            if transitivity_violations:
                fixed = _WSA.fix_transitivity_violations(new_parents, transitivity_violations)
                yield _sse("transitivity_check", f"传递性校验：检测到 {len(transitivity_violations)} 处违背，修复 {fixed} 处")
                # Feed violations into suspicious pairs for LLM reflection
                for ancestor, descendant in transitivity_violations:
                    agent._suspicious_pairs.append({
                        "child": descendant, "parent": ancestor,
                        "reasons": ["transitivity_violation"],
                        "child_tier": new_tiers.get(descendant, "unknown"),
                        "parent_tier": new_tiers.get(ancestor, "unknown"),
                    })
            else:
                yield _sse("transitivity_check", "传递性校验：层级链一致，无违背")

            # 4.1b. Continent protection: continents must be direct children of uber_root
            _uber = None
            for loc, tier in new_tiers.items():
                if tier == "world":
                    _uber = loc
                    break
            if _uber:
                _fixed_continents = 0
                for loc, tier in new_tiers.items():
                    if tier == "continent" and new_parents.get(loc) not in (_uber, None):
                        logger.info("Continent protection: %s parent %s → %s",
                                    loc, new_parents.get(loc), _uber)
                        new_parents[loc] = _uber
                        _fixed_continents += 1
                if _fixed_continents:
                    yield _sse("consolidate",
                               f"修正 {_fixed_continents} 个大洲归属（大洲必须是顶级子节点）")

            # 4.2. LLM Reflection on suspicious parent-child pairs
            try:
                import asyncio as _asyncio_reflect
                from src.services.location_hierarchy_reviewer import LocationHierarchyReviewer as _ReflReviewer
                suspicious = agent._suspicious_pairs
                if suspicious:
                    yield _sse("reflection", f"LLM 反思验证 {len(suspicious)} 对可疑关系...")
                    _reflect_reviewer = _ReflReviewer()
                    reflections = await _asyncio_reflect.wait_for(
                        _reflect_reviewer.reflect_suspicious(
                            novel.get("title", ""), suspicious,
                        ),
                        timeout=180.0,  # v0.67: 60→180s
                    )
                    reflection_applied = 0
                    for r in reflections:
                        child = r.get("child", "")
                        parent = r.get("parent", "")
                        verdict = r.get("verdict", "")
                        if not child or not parent:
                            continue
                        if verdict in ("correct", "uncertain", ""):
                            continue  # Story 3.2: uncertain = no action
                        if verdict == "sibling":
                            from src.services.world_structure_agent import _find_common_parent
                            known_locs = set(new_tiers.keys())
                            common = _find_common_parent(
                                child, parent, agent._parent_votes, known_locs,
                            )
                            if common:
                                new_parents[child] = common
                                new_parents[parent] = common
                                reflection_applied += 1
                                logger.info(
                                    "Reflection sibling: %s ↔ %s → common parent %s",
                                    child, parent, common,
                                )
                        elif verdict == "reverse":
                            if child in new_parents:
                                new_parents[parent] = new_parents[child]  # B gets A's old parent
                                del new_parents[child]  # A becomes root
                                reflection_applied += 1
                                logger.info(
                                    "Reflection reverse: %s → %s (was %s → %s)",
                                    parent, child, child, parent,
                                )
                    yield _sse(
                        "reflection",
                        f"反思完成：{len(reflections)} 条建议，{reflection_applied} 条应用",
                    )
                else:
                    yield _sse("reflection", "无可疑关系，跳过反思")
            except _asyncio_reflect.TimeoutError:
                logger.warning("LLM reflection timed out for %s", novel_id)
                yield _sse("reflection", "LLM 反思超时，已跳过")
            except Exception:
                logger.warning("LLM reflection failed for %s", novel_id, exc_info=True)
                yield _sse("reflection", "LLM 反思失败，已跳过")

            # 4.5. LLM hierarchy validation (post-consolidation)
            try:
                import asyncio as _asyncio
                from src.services.location_hierarchy_reviewer import LocationHierarchyReviewer
                yield _sse("validate", "正在进行 LLM 层级合理性验证...")
                _val_reviewer = LocationHierarchyReviewer()
                corrections = await _asyncio.wait_for(
                    _val_reviewer.validate_hierarchy(
                        new_parents, new_tiers, ws.novel_genre_hint,
                    ),
                    timeout=180.0,  # v0.67: 60→180s
                )
                if corrections:
                    for corr in corrections:
                        new_parents[corr["child"]] = corr["correct_parent"]
                    yield _sse("validate", f"LLM 验证修正 {len(corrections)} 处")
                else:
                    yield _sse("validate", "LLM 验证完成，层级结构合理")
            except _asyncio.TimeoutError:
                logger.warning("LLM hierarchy validation timed out for %s", novel_id)
                yield _sse("validate", "LLM 层级验证超时，已跳过")
            except Exception:
                logger.warning("LLM hierarchy validation failed for %s", novel_id, exc_info=True)
                yield _sse("validate", "LLM 层级验证失败，已跳过")

            # 4.9. Clean phantom parents — parent values that don't exist as known locations
            # (e.g., "薛姨妈室、里间" which is in location_parents but not location_tiers)
            _uber_root = agent._find_uber_root(new_parents) if new_parents else None
            phantom_cleaned = 0
            for _child, _parent in list(new_parents.items()):
                if _parent not in new_tiers and _parent != _uber_root:
                    del new_parents[_child]
                    phantom_cleaned += 1
            if phantom_cleaned:
                logger.info("Cleaned %d phantom parent entries", phantom_cleaned)
                yield _sse("validate", f"清理 {phantom_cleaned} 个幻影父节点")

            # 4.95. Final cycle detection — catch cycles introduced by reflection/validation
            _final_cycles = agent._detect_and_break_cycles(new_parents)
            if _final_cycles:
                logger.warning("Final cycle sweep broke %d cycles after LLM mutations", _final_cycles)
                yield _sse("validate", f"最终检查：修复 {_final_cycles} 个循环依赖")

            # 5. Compute diff (use new_tiers as known location set for validation)
            old_parents = dict(ws.location_parents)
            known_locations = set(new_tiers.keys())
            changes = _compute_hierarchy_diff(old_parents, new_parents, known_locations)

            # v0.63.0 Safety: downgrade lateral "changed" auto_select when LLM review
            # is unavailable. Even when skeleton succeeded, lateral moves (between
            # non-uber_root parents) are risky without LLM validation.
            if not llm_review_used:
                _uber_for_safety = agent._find_uber_root(new_parents) if new_parents else None
                downgraded = 0
                for c in changes:
                    if c["change_type"] == "changed" and c["auto_select"]:
                        old_p = c.get("old_parent", "")
                        new_p = c.get("new_parent", "")
                        # Allow: moves FROM uber_root to a specific parent (consolidate/skeleton rescue)
                        if old_p == _uber_for_safety and new_p != _uber_for_safety:
                            continue
                        # Allow: moves FROM None/missing to a parent (orphan adoption)
                        if not old_p:
                            continue
                        # v0.67.1: Do NOT auto-approve moves TO uber_root.
                        # Previously allowed as "cleanup", but this flattens the
                        # hierarchy when LLM review fails — locations get pulled
                        # from specific parents to uber_root, destroying depth.
                        c["auto_select"] = False
                        c["reason"] = "LLM审查未成功，横向修改需人工确认"
                        downgraded += 1
                if downgraded:
                    logger.info(
                        "Safety downgrade: %d lateral 'changed' auto_select → false (no LLM review)",
                        downgraded,
                    )
                    yield _sse("consolidate", f"安全保护：{downgraded} 项横向修改降级为手动确认（LLM审查未生效）")

            old_root_count, _ = _count_roots(old_parents)
            new_root_count, _ = _count_roots(new_parents)

            added = sum(1 for c in changes if c["change_type"] == "added")
            changed = sum(1 for c in changes if c["change_type"] == "changed")
            removed = sum(1 for c in changes if c["change_type"] == "removed")

            logger.info(
                "Hierarchy rebuild preview for %s: %d changes (added=%d changed=%d removed=%d), "
                "roots %d→%d, scene=%s, llm=%s",
                novel_id, len(changes), added, changed, removed,
                old_root_count, new_root_count, scene_analysis_used, llm_review_used,
            )

            result = {
                "changes": changes,
                "location_tiers": new_tiers,
                "summary": {
                    "added": added,
                    "changed": changed,
                    "removed": removed,
                    "total": len(changes),
                    "old_root_count": old_root_count,
                    "new_root_count": new_root_count,
                    "scene_analysis_used": scene_analysis_used,
                    "llm_review_used": llm_review_used,
                },
            }
            yield _sse("done", "重建完成", result=result)

        except Exception as e:
            logger.error("Hierarchy rebuild failed for %s", novel_id, exc_info=True)
            yield _sse("error", f"重建失败: {str(e)[:200]}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/apply-hierarchy-changes")
async def apply_hierarchy_changes(novel_id: str, body: HierarchyChangesRequest):
    """Apply user-selected hierarchy changes to the WorldStructure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    ws = await world_structure_store.load(novel_id)
    if not ws:
        raise HTTPException(status_code=404, detail="WorldStructure 不存在，请先分析小说")

    old_count = len(ws.location_parents)

    for change in body.changes:
        if change.new_parent:
            ws.location_parents[change.location] = change.new_parent
        elif change.location in ws.location_parents:
            del ws.location_parents[change.location]

    # Save consolidated tiers to keep them in sync with new parents
    if body.location_tiers is not None:
        ws.location_tiers = body.location_tiers

    # ── Re-detect layers (clean stale instances, detect realms/pockets) ──
    from src.services.world_structure_agent import (
        WorldStructureAgent,
        _INSTANCE_NAME_KEYWORDS,
        _INSTANCE_TYPE_KEYWORDS as _itk_apply,
    )
    from src.models.world_structure import MapLayer, LayerType

    _apply_agent = WorldStructureAgent(novel_id)
    _apply_agent.structure = ws

    # Load user overrides to respect them
    _apply_overrides = await world_structure_override_store.load_overrides(novel_id)
    for ov in _apply_overrides:
        _apply_agent._overridden_keys.add((ov["override_type"], ov["override_key"]))

    # Remove stale instance_* layers AND orphaned LLM-created duplicate layers
    # (e.g., LLM creates layer "灵界" while keyword detection creates "lingworld")
    _keyword_layer_ids = set()
    from src.services.world_structure_agent import _REALM_LAYER_KEYWORDS
    for _, (lid, _) in _REALM_LAYER_KEYWORDS.items():
        _keyword_layer_ids.add(lid)
    _keyword_layer_ids.update({"celestial", "underworld", "overworld"})

    # Collect IDs of layers to remove: stale instances + orphaned Chinese-named realms
    _active_layer_ids = set(ws.location_layer_map.values())
    stale_ids: set[str] = set()
    for l in ws.layers:
        if l.layer_id.startswith("instance_"):
            stale_ids.add(l.layer_id)
        elif (
            l.layer_id not in _keyword_layer_ids
            and l.layer_id != "overworld"
            and l.layer_id not in _active_layer_ids
        ):
            # Orphaned layer (no locations assigned to it)
            stale_ids.add(l.layer_id)

    if stale_ids:
        ws.layers = [l for l in ws.layers if l.layer_id not in stale_ids]
        for _ln, _lid in list(ws.location_layer_map.items()):
            if _lid in stale_ids:
                ws.location_layer_map[_ln] = "overworld"

    # Build location type lookup from chapter_facts
    _loc_types: dict[str, str] = {}
    try:
        from src.db import chapter_fact_store as _cfs_apply
        _all_facts = await _cfs_apply.get_all_chapter_facts(novel_id)
        for _f in _all_facts:
            for _loc in _f.get("locations", []):
                _ln = _loc.get("name", "")
                if _ln and _ln not in _loc_types:
                    _loc_types[_ln] = _loc.get("type", "")
    except Exception:
        pass

    # Re-detect all locations
    _layer_changes = 0
    for _ln in set(ws.location_tiers.keys()):
        if ("location_layer", _ln) in _apply_agent._overridden_keys:
            continue
        _lt = _loc_types.get(_ln, "")
        _new_layer = _apply_agent._detect_layer(_ln, _lt)
        if _new_layer:
            _apply_agent._ensure_layer_exists(_new_layer)
            if ws.location_layer_map.get(_ln) != _new_layer:
                ws.location_layer_map[_ln] = _new_layer
                _layer_changes += 1
        else:
            # Check instance keywords (name or type)
            _is_inst = (
                _apply_agent._is_instance_detection_enabled()
                and (
                    any(kw in _ln for kw in _INSTANCE_NAME_KEYWORDS)
                    or any(kw in _lt for kw in _itk_apply)
                )
            )
            if _is_inst:
                _pockets_id = "pockets"
                if not _apply_agent._has_layer(_pockets_id):
                    ws.layers.append(MapLayer(
                        layer_id=_pockets_id, name="副本/秘境",
                        layer_type=LayerType.pocket,
                        description="秘境、禁地、洞天、幻境等独立空间",
                    ))
                if ws.location_layer_map.get(_ln) != _pockets_id:
                    ws.location_layer_map[_ln] = _pockets_id
                    _layer_changes += 1
            elif ws.location_layer_map.get(_ln, "overworld") != "overworld":
                ws.location_layer_map[_ln] = "overworld"
                _layer_changes += 1

    logger.info("Layer re-detection during apply: %d stale cleared, %d reassigned",
                len(stale_ids), _layer_changes)

    # Cycle detection — user-selected changes may create cycles
    _apply_cycles = _apply_agent._detect_and_break_cycles(ws.location_parents)
    if _apply_cycles:
        logger.warning("Apply: broke %d cycles from user-selected changes", _apply_cycles)

    # Parent layer propagation — child inherits parent's non-overworld layer.
    # Also corrects mismatches: if child's keyword-detected layer differs from
    # parent's layer, parent's layer wins (e.g., "三颗太阳" keyword→solarsystem
    # but parent="三体星系"→trisolaris, so child should be trisolaris).
    _layer_propagated = 0
    _prop_changed = True
    _prop_passes = 5
    while _prop_changed and _prop_passes > 0:
        _prop_changed = False
        _prop_passes -= 1
        for _child, _parent in ws.location_parents.items():
            c_layer = ws.location_layer_map.get(_child, "overworld")
            p_layer = ws.location_layer_map.get(_parent, "overworld")
            if p_layer != "overworld" and c_layer != p_layer:
                ws.location_layer_map[_child] = p_layer
                _prop_changed = True
                _layer_propagated += 1
    if _layer_propagated:
        logger.info("Layer propagation during apply: %d locations inherited parent layer",
                    _layer_propagated)

    new_count = len(ws.location_parents)
    root_count, final_roots = _count_roots(ws.location_parents)

    await world_structure_store.save(novel_id, ws)

    # Invalidate layout cache since hierarchy changed
    await world_structure_store.delete_layer_layouts(novel_id)

    # Clear map coordinate overrides for affected locations so they get
    # repositioned by the constraint solver based on the new hierarchy.
    # Skip locked overrides (constraint_type='locked') — those are user-pinned.
    affected_locs = {c.location for c in body.changes}
    if affected_locs:
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            placeholders = ",".join("?" for _ in affected_locs)
            await conn.execute(
                f"DELETE FROM map_user_overrides WHERE novel_id = ? "
                f"AND location_name IN ({placeholders}) "
                f"AND (constraint_type IS NULL OR constraint_type != 'locked')",
                (novel_id, *affected_locs),
            )
            await conn.commit()
        finally:
            await conn.close()

    logger.info(
        "Applied %d hierarchy changes for %s: %d → %d parents, %d roots",
        len(body.changes), novel_id, old_count, new_count, root_count,
    )
    return {
        "status": "ok",
        "old_parent_count": old_count,
        "new_parent_count": new_count,
        "root_count": root_count,
        "roots": final_roots[:20],
    }


@router.delete("/overrides/{override_id}")
async def delete_override(novel_id: str, override_id: int):
    """Delete a specific override and return the updated WorldStructure."""
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    deleted = await world_structure_override_store.delete_override(novel_id, override_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Override 不存在")

    # Invalidate layout cache
    await world_structure_store.delete_layer_layouts(novel_id)

    ws = await world_structure_store.load_with_overrides(novel_id)
    return ws.model_dump()


@router.post("/spatial-completion")
async def spatial_completion(novel_id: str):
    """Run spatial completion agent with SSE progress streaming.

    Detects cross-chapter spatial gaps and uses LLM to fill them.
    Results stored in WorldStructure.completed_spatial_relations.
    """
    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    ws = await world_structure_store.load(novel_id)
    if not ws:
        raise HTTPException(status_code=404, detail="WorldStructure 不存在，请先分析小说")

    async def event_stream():
        try:
            from src.services.spatial_completion_agent import SpatialCompletionAgent

            agent = SpatialCompletionAgent(novel_id)
            collected: list[str] = []

            async def collect_emit(stage: str, message: str, **extra):
                collected.append(_sse(stage, message, **extra))

            await agent.run(progress_callback=collect_emit)

            for event in collected:
                yield event

            # Invalidate layout cache so next map load uses new spatial data
            await world_structure_store.delete_layer_layouts(novel_id)

        except Exception as e:
            logger.error("Spatial completion failed for %s", novel_id, exc_info=True)
            yield _sse("error", f"空间补全失败: {str(e)[:200]}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Topology quality metrics (v0.63.0 Story 1.3) ────────────────────


@router.get("/topology-metrics")
async def get_topology_metrics(novel_id: str):
    """Compute topology quality metrics against golden standard datasets.

    Returns metrics comparing the novel's location_parents with the
    golden standard (if the novel matches a known golden standard title).
    Always returns root_count and orphan_rate even without golden standard.
    """
    import json as _json
    from pathlib import Path
    from src.utils.topology_metrics import compute_topology_metrics

    ws = await world_structure_store.load(novel_id)
    if not ws or not ws.location_parents:
        raise HTTPException(status_code=404, detail="无层级数据")

    novel = await novel_store.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")

    title = novel.get("title", "")
    predicted = dict(ws.location_parents)

    # Try to match a golden standard dataset
    fixtures_dir = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures"
    golden_file = None
    if "西游" in title:
        golden_file = fixtures_dir / "golden_standard_journey_to_west.json"
    elif "红楼" in title:
        golden_file = fixtures_dir / "golden_standard_dream_of_red_chamber.json"

    if golden_file and golden_file.exists():
        with open(golden_file, encoding="utf-8") as f:
            golden_data = _json.load(f)
        golden_locations = golden_data["locations"]
        metrics = compute_topology_metrics(predicted, golden_locations)
        metrics["golden_standard"] = golden_data.get("_meta", {}).get("novel", "unknown")
        metrics["golden_location_count"] = len(golden_locations)
    else:
        # No golden standard: compute structural metrics only
        all_children = set(predicted.keys())
        all_locations = all_children | set(predicted.values())
        roots = all_locations - all_children
        metrics = {
            "parent_precision": None,
            "parent_recall": None,
            "chain_accuracy": None,
            "root_count": len(roots),
            "orphan_rate": None,
            "golden_standard": None,
            "golden_location_count": 0,
        }

    metrics["predicted_location_count"] = len(predicted)
    return metrics
