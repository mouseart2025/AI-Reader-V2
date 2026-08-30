"""User entity override endpoints — alias merge/split/rename, concept edits,
and entity-level hide/retype (issue #66 Epic 1).

Writes to the entity_overrides table; the override layer is applied in
alias_resolver._apply_user_overrides (alias-level) and entity_visibility
(entity-level, read at aggregation/view boundaries), so a single write
propagates to every consumer (entity cards, graph, map, reading highlight,
encyclopedia, export). Every write invalidates the aggregation + alias +
map-response caches so the change is visible immediately.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import entity_override_store, novel_store
from src.services import entity_aggregator, visualization_service
from src.services.alias_resolver import build_alias_map
from src.services.entity_visibility import VALID_RETYPES

router = APIRouter(
    prefix="/api/novels/{novel_id}/entity-overrides", tags=["entity-overrides"]
)


class MergeRequest(BaseModel):
    members: list[str]       # all aliases to unify (incl. former canonicals)
    canonical: str           # user-chosen display name; must be in members


class SplitRequest(BaseModel):
    source: str              # canonical the aliases are currently merged under
    aliases: list[str]       # aliases to detach from source
    to: str | None = None    # destination canonical; None => new independent entity


class RenameRequest(BaseModel):
    source: str              # current canonical/display name
    to: str                  # new display name (user-typed; may be brand new)


class ConceptEditRequest(BaseModel):
    name: str                # concept's current (display) name
    to: str | None = None    # new name (rename) or new category (recategory)


class HideRequest(BaseModel):
    name: str                # entity name (alias OK — resolved to canonical)


class RetypeRequest(BaseModel):
    name: str                # entity name (alias OK — resolved to canonical)
    to: str                  # person | location | item | org | concept


async def _require_novel(novel_id: str) -> None:
    if not await novel_store.get_novel(novel_id):
        raise HTTPException(status_code=404, detail="小说不存在")


def _invalidate_views(novel_id: str) -> None:
    """实体级 override 写入后的缓存失效:聚合/别名缓存 + 地图响应缓存。"""
    entity_aggregator.invalidate_cache(novel_id)
    visualization_service.invalidate_map_response_cache(novel_id)


def _snapshot(alias_map: dict[str, str], names: list[str]) -> dict[str, str]:
    """Capture current automatic resolution for drift detection (FR7)."""
    return {n: alias_map.get(n, n) for n in names}


@router.get("")
async def list_overrides(novel_id: str):
    """All user alias overrides for the novel — backs the "我的修正" list."""
    await _require_novel(novel_id)
    overrides = await entity_override_store.load_overrides(novel_id)
    # FR7 式漂移检测(entity_hide / entity_retype, Epic 1):与自动基线
    # (不含可见性 override 的实体列表)对比,非破坏标记,override 仍生效。
    if any(
        o["override_type"] in ("entity_hide", "entity_retype") for o in overrides
    ):
        alias_map = await build_alias_map(novel_id)
        auto_entities = await entity_aggregator.get_all_entities(
            novel_id, apply_visibility=False
        )
        auto_types = {e.name: e.type for e in auto_entities}
        for o in overrides:
            if o["override_type"] not in ("entity_hide", "entity_retype"):
                continue
            resolved = alias_map.get(o["override_key"], o["override_key"])
            auto_type = auto_types.get(resolved)
            if auto_type is None:
                o["conflict"] = True
                o["conflict_reason"] = "实体在当前分析结果中已不存在"
            elif o["override_type"] == "entity_retype":
                snap_from = (o.get("override_json") or {}).get("from")
                if snap_from and snap_from != auto_type:
                    o["conflict"] = True
                    o["conflict_reason"] = (
                        f"自动识别类型已变为 {auto_type}(修正仍生效)"
                    )
    return {"overrides": overrides}


@router.post("/merge")
async def merge_aliases(novel_id: str, body: MergeRequest):
    await _require_novel(novel_id)
    members = [m.strip() for m in body.members if m.strip()]
    canonical = body.canonical.strip()
    if len(members) < 2:
        raise HTTPException(status_code=400, detail="合并至少需要 2 个名称")
    if not canonical or canonical not in members:
        raise HTTPException(status_code=400, detail="显示名必须是合并名称之一")

    alias_map = await build_alias_map(novel_id)
    oid = await entity_override_store.save_override(
        novel_id,
        "alias_merge",
        canonical,
        {"members": members, "canonical": canonical,
         "auto_snapshot": _snapshot(alias_map, members)},
    )
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok", "override_id": oid}


@router.post("/split")
async def split_aliases(novel_id: str, body: SplitRequest):
    await _require_novel(novel_id)
    source = body.source.strip()
    aliases = [a.strip() for a in body.aliases if a.strip()]
    to = body.to.strip() if body.to else None
    if not source:
        raise HTTPException(status_code=400, detail="缺少源实体")
    if not aliases:
        raise HTTPException(status_code=400, detail="拆分至少需要 1 个别名")
    if to == source:
        raise HTTPException(status_code=400, detail="拆分目标不能与源实体相同")
    if source in aliases:
        raise HTTPException(status_code=400, detail="不能拆出源实体的显示名")

    # Composite key so multiple splits from one source to different destinations
    # don't collide on the UNIQUE(novel_id, type, key) constraint.
    override_key = f"{source}→{to or '(独立)'}"
    alias_map = await build_alias_map(novel_id)
    oid = await entity_override_store.save_override(
        novel_id,
        "alias_split",
        override_key,
        {"source": source, "aliases": aliases, "to": to,
         "auto_snapshot": _snapshot(alias_map, aliases)},
    )
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok", "override_id": oid}


@router.post("/rename")
async def rename_entity(novel_id: str, body: RenameRequest):
    await _require_novel(novel_id)
    source = body.source.strip()
    to = body.to.strip()
    if not source or not to:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if to == source:
        raise HTTPException(status_code=400, detail="新名称与当前名称相同")

    alias_map = await build_alias_map(novel_id)
    oid = await entity_override_store.save_override(
        novel_id,
        "entity_rename",
        source,
        {"to": to, "auto_snapshot": _snapshot(alias_map, [source])},
    )
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok", "override_id": oid}


@router.post("/concept-rename")
async def concept_rename(novel_id: str, body: ConceptEditRequest):
    await _require_novel(novel_id)
    name = body.name.strip()
    to = (body.to or "").strip()
    if not name or not to:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if to == name:
        raise HTTPException(status_code=400, detail="新名称与当前名称相同")
    oid = await entity_override_store.save_override(
        novel_id, "concept_rename", name, {"to": to},
    )
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok", "override_id": oid}


@router.post("/concept-recategory")
async def concept_recategory(novel_id: str, body: ConceptEditRequest):
    await _require_novel(novel_id)
    name = body.name.strip()
    to = (body.to or "").strip()
    if not name or not to:
        raise HTTPException(status_code=400, detail="名称/分类不能为空")
    oid = await entity_override_store.save_override(
        novel_id, "concept_recategory", name, {"to": to},
    )
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok", "override_id": oid}


@router.post("/concept-delete")
async def concept_delete(novel_id: str, body: ConceptEditRequest):
    await _require_novel(novel_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    oid = await entity_override_store.save_override(
        novel_id, "concept_delete", name, {},
    )
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok", "override_id": oid}


async def _auto_type_of(novel_id: str, canonical: str) -> str | None:
    """实体在自动基线(不含可见性 override)下的类型;不存在返回 None。"""
    entities = await entity_aggregator.get_all_entities(
        novel_id, apply_visibility=False
    )
    return next((e.type for e in entities if e.name == canonical), None)


@router.post("/hide")
async def hide_entity(novel_id: str, body: HideRequest):
    """FR-1.1: 隐藏误识别实体(软删 — 数据在,视图不渲染,删 override 即恢复)。"""
    await _require_novel(novel_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    alias_map = await build_alias_map(novel_id)
    canonical = alias_map.get(name, name)
    auto_type = await _auto_type_of(novel_id, canonical)
    if auto_type is None:
        raise HTTPException(status_code=400, detail="实体不存在")
    oid = await entity_override_store.save_override(
        novel_id,
        "entity_hide",
        canonical,
        {"auto_snapshot": {"type": auto_type}},
    )
    _invalidate_views(novel_id)
    return {"status": "ok", "override_id": oid}


@router.post("/retype")
async def retype_entity(novel_id: str, body: RetypeRequest):
    """FR-1.2: 修改实体类型(五类目标),不触发别名重算。"""
    await _require_novel(novel_id)
    name = body.name.strip()
    to = body.to.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if to not in VALID_RETYPES:
        raise HTTPException(status_code=400, detail=f"不支持的实体类型: {to}")
    alias_map = await build_alias_map(novel_id)
    canonical = alias_map.get(name, name)
    auto_type = await _auto_type_of(novel_id, canonical)
    if auto_type is None:
        raise HTTPException(status_code=400, detail="实体不存在")
    # 与当前生效类型比较(可能已被改型过一次),避免无意义的重复写入
    from src.services.entity_visibility import get_visibility_overrides

    _, retype_map = await get_visibility_overrides(novel_id)
    effective = retype_map.get(canonical, auto_type)
    if to == effective:
        raise HTTPException(status_code=400, detail="新类型与当前类型相同")
    oid = await entity_override_store.save_override(
        novel_id,
        "entity_retype",
        canonical,
        {"from": auto_type, "to": to, "auto_snapshot": {"type": auto_type}},
    )
    _invalidate_views(novel_id)
    return {"status": "ok", "override_id": oid}


@router.delete("/{override_id}")
async def delete_override(novel_id: str, override_id: int):
    """Undo a single override (FR5) — entity reverts to automatic resolution."""
    await _require_novel(novel_id)
    if not await entity_override_store.delete_override(novel_id, override_id):
        raise HTTPException(status_code=404, detail="修正记录不存在")
    _invalidate_views(novel_id)
    return {"status": "ok"}
