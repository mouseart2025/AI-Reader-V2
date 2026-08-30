"""实体级可见性 override(entity_hide / entity_retype)的读时应用 — issue #66 Epic 1。

与 alias_resolver 的用户 override 层同一原则:只读 entity_overrides 表,
ChapterFact.fact_json 字节级不变;无任何 override 时各消费端输出与纯自动
结果逐字节一致。override key 为写入时的实体 canonical 名;应用点自行用
alias_map 展开(entity_rename 之后旧 key 仍能命中新 canonical)。
"""

from __future__ import annotations

from src.db import entity_override_store

# entity_retype 允许的目标类型(FR-1.2)
VALID_RETYPES: frozenset[str] = frozenset(
    {"person", "location", "item", "org", "concept"}
)


async def get_visibility_overrides(
    novel_id: str,
) -> tuple[set[str], dict[str, str]]:
    """Return (hidden canonical names, retype map canonical -> target type).

    entity_hide: 软删 — 数据在,所有视图不渲染。
    entity_retype: override_json = {"from": old_type, "to": new_type};
    目标类型非法的记录忽略(防御性,写入端已校验)。
    单次 SELECT,不做缓存 — 表小且有索引,避免缓存失效复杂度。
    """
    hidden: set[str] = set()
    retype: dict[str, str] = {}
    for ov in await entity_override_store.load_overrides(novel_id):
        if ov["override_type"] == "entity_hide":
            hidden.add(ov["override_key"])
        elif ov["override_type"] == "entity_retype":
            target = (ov.get("override_json") or {}).get("to")
            if target in VALID_RETYPES:
                retype[ov["override_key"]] = target
    return hidden, retype


def expand_hidden(alias_map: dict[str, str], hidden: set[str]) -> set[str]:
    """Expand hide keys through alias_map (entity_rename 后旧名仍命中)。"""
    return set(hidden) | {alias_map.get(k, k) for k in hidden}


def expand_retype(
    alias_map: dict[str, str], retype: dict[str, str]
) -> dict[str, str]:
    """同上,retype map 的 key 经 alias_map 展开(原 key 与新 canonical 都保留)。"""
    expanded = dict(retype)
    for k, v in retype.items():
        expanded[alias_map.get(k, k)] = v
    return expanded
