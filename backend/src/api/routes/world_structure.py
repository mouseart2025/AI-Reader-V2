"""WorldStructure API endpoints."""

import json
import logging

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

            # 1. Rebuild parent votes
            yield _sse("votes", "正在从章节事实重建投票数据...")
            agent._parent_votes = await agent._rebuild_parent_votes()

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
                    from src.services.location_hierarchy_reviewer import LocationHierarchyReviewer
                    reviewer = LocationHierarchyReviewer()
                    review_votes = await reviewer.review(
                        ws.location_tiers,
                        temp_parents,
                        scene_analysis,
                        ws.novel_genre_hint,
                    )
                    if review_votes:
                        agent.inject_external_votes(review_votes)
                        llm_review_used = True
                        yield _sse("llm", f"LLM 审查完成，获得 {len(review_votes)} 条建议")
                    else:
                        yield _sse("llm", "LLM 审查完成，无新建议")
                except Exception:
                    logger.warning("LLM hierarchy review failed for %s", novel_id, exc_info=True)
                    yield _sse("llm", "LLM 审查失败，使用纯算法结果")
            else:
                yield _sse("llm", f"根节点仅 {temp_root_count} 个，跳过 LLM 审查")

            # 4. Final resolution
            yield _sse("consolidate", "正在合并与优化层级结构...")
            new_parents = agent._resolve_parents()

            from src.services.hierarchy_consolidator import consolidate_hierarchy
            new_parents, new_tiers = consolidate_hierarchy(
                new_parents,
                dict(ws.location_tiers),
                novel_genre_hint=ws.novel_genre_hint,
                parent_votes=agent._parent_votes,
            )

            # 5. Compute diff (use new_tiers as known location set for validation)
            old_parents = dict(ws.location_parents)
            known_locations = set(new_tiers.keys())
            changes = _compute_hierarchy_diff(old_parents, new_parents, known_locations)

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

    new_count = len(ws.location_parents)
    root_count, final_roots = _count_roots(ws.location_parents)

    await world_structure_store.save(novel_id, ws)

    # Invalidate layout cache since hierarchy changed
    await world_structure_store.delete_layer_layouts(novel_id)

    # Clear map coordinate overrides for affected locations so they get
    # repositioned by the constraint solver based on the new hierarchy.
    affected_locs = {c.location for c in body.changes}
    if affected_locs:
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            placeholders = ",".join("?" for _ in affected_locs)
            await conn.execute(
                f"DELETE FROM map_user_overrides WHERE novel_id = ? "
                f"AND location_name IN ({placeholders})",
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
