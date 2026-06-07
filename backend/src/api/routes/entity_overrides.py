"""User alias override endpoints — manual merge/split of entity aliases.

Writes to the entity_overrides table; the override layer is applied in
alias_resolver._apply_user_overrides on top of the automatic alias map, so a
single write propagates to every consumer (entity cards, graph, reading
highlight, semantic search, encyclopedia). Every write invalidates the
aggregation + alias caches so the change is visible immediately.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import entity_override_store, novel_store
from src.services import entity_aggregator
from src.services.alias_resolver import build_alias_map

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


async def _require_novel(novel_id: str) -> None:
    if not await novel_store.get_novel(novel_id):
        raise HTTPException(status_code=404, detail="小说不存在")


def _snapshot(alias_map: dict[str, str], names: list[str]) -> dict[str, str]:
    """Capture current automatic resolution for drift detection (FR7)."""
    return {n: alias_map.get(n, n) for n in names}


@router.get("")
async def list_overrides(novel_id: str):
    """All user alias overrides for the novel — backs the "我的修正" list."""
    await _require_novel(novel_id)
    return {"overrides": await entity_override_store.load_overrides(novel_id)}


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


@router.delete("/{override_id}")
async def delete_override(novel_id: str, override_id: int):
    """Undo a single override (FR5) — entity reverts to automatic resolution."""
    await _require_novel(novel_id)
    if not await entity_override_store.delete_override(novel_id, override_id):
        raise HTTPException(status_code=404, detail="修正记录不存在")
    entity_aggregator.invalidate_cache(novel_id)
    return {"status": "ok"}
