"""Tests for the user alias override layer (manual merge/split).

Covers:
- entity_override_store CRUD round-trip + UPSERT + delete (Story 1.1)
- alias_resolver._apply_user_overrides merge/split semantics, canonical-not-self
  invariant, idempotency, survives-rebuild, conflict detection, empty no-op,
  and the 沙僧/八戒 over-merge correction (Story 1.2 / SC5).
"""

import pytest
from unittest.mock import patch

from src.db import entity_override_store
from src.services import alias_resolver
from src.services.alias_resolver import (
    _apply_user_overrides,
    get_alias_conflicts,
    invalidate_alias_cache,
)

NOVEL = "novel-test"


# ── Store CRUD (Story 1.1) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_store_roundtrip_and_upsert(memory_db):
    # memory_db.close is managed by the fixture; entity_override_store closes its
    # connection in finally, so hand it a proxy whose close() is a no-op.
    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def _proxy_factory():
        return _NonClosing(memory_db)

    # A novel row is required by the FK.
    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)",
        (NOVEL, "西游记"),
    )
    await memory_db.commit()

    with patch("src.db.entity_override_store.get_connection", _proxy_factory):
        oid = await entity_override_store.save_override(
            NOVEL, "alias_merge", "沙僧",
            {"members": ["沙僧", "沙悟净"], "canonical": "沙僧"},
        )
        assert oid > 0

        rows = await entity_override_store.load_overrides(NOVEL)
        assert len(rows) == 1
        assert rows[0]["override_type"] == "alias_merge"
        assert rows[0]["override_key"] == "沙僧"
        # JSON round-trips with Chinese intact (ensure_ascii=False).
        assert rows[0]["override_json"]["canonical"] == "沙僧"
        assert "沙悟净" in rows[0]["override_json"]["members"]

        # UPSERT on the same (type, key) replaces, does not duplicate.
        await entity_override_store.save_override(
            NOVEL, "alias_merge", "沙僧",
            {"members": ["沙僧", "沙悟净", "卷帘大将"], "canonical": "沙僧"},
        )
        rows = await entity_override_store.load_overrides(NOVEL)
        assert len(rows) == 1
        assert "卷帘大将" in rows[0]["override_json"]["members"]

        # Delete.
        assert await entity_override_store.delete_override(NOVEL, rows[0]["id"]) is True
        assert await entity_override_store.load_overrides(NOVEL) == []


# ── _apply_user_overrides logic (Story 1.2) ─────────────────────


def _patch_overrides(overrides):
    """Patch entity_override_store.load_overrides to return canned overrides."""

    async def _load(_novel_id):
        return overrides

    return patch("src.db.entity_override_store.load_overrides", _load)


@pytest.mark.asyncio
async def test_empty_overrides_is_noop():
    """No overrides → map unchanged, byte-identical (gold baseline protection)."""
    invalidate_alias_cache(NOVEL)
    base = {"猴王": "孙悟空", "行者": "孙悟空"}
    with _patch_overrides([]):
        out = await _apply_user_overrides(NOVEL, dict(base))
    assert out == base
    assert get_alias_conflicts(NOVEL) == set()


@pytest.mark.asyncio
async def test_merge_points_members_at_chosen_canonical():
    invalidate_alias_cache(NOVEL)
    amap = {}
    ov = [{
        "override_type": "alias_merge",
        "override_key": "沙僧",
        "override_json": {"members": ["沙僧", "沙悟净", "卷帘大将"], "canonical": "沙僧"},
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    assert out["沙悟净"] == "沙僧"
    assert out["卷帘大将"] == "沙僧"
    # Invariant: canonical must not map to itself.
    assert "沙僧" not in out


@pytest.mark.asyncio
async def test_merge_locks_user_canonical_over_auto():
    """D1: user-chosen canonical overrides whatever auto picked."""
    invalidate_alias_cache(NOVEL)
    # Auto picked 八戒 as canonical (higher freq); user forces 猪八戒.
    amap = {"猪悟能": "八戒", "天蓬元帅": "八戒"}
    ov = [{
        "override_type": "alias_merge",
        "override_key": "猪八戒",
        "override_json": {
            "members": ["八戒", "猪八戒", "猪悟能", "天蓬元帅"],
            "canonical": "猪八戒",
        },
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    assert out["八戒"] == "猪八戒"
    assert out["猪悟能"] == "猪八戒"
    assert out["天蓬元帅"] == "猪八戒"
    assert "猪八戒" not in out


@pytest.mark.asyncio
async def test_split_reassign_to_existing_entity():
    """SC5: 沙僧 aliases wrongly merged into 八戒 → split + reassign to 沙僧."""
    invalidate_alias_cache(NOVEL)
    amap = {"沙悟净": "八戒", "卷帘大将": "八戒", "猪悟能": "八戒"}
    ov = [{
        "override_type": "alias_split",
        "override_key": "八戒",
        "override_json": {"aliases": ["沙悟净", "卷帘大将"], "to": "沙僧"},
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    assert out["沙悟净"] == "沙僧"
    assert out["卷帘大将"] == "沙僧"
    assert out["猪悟能"] == "八戒"  # untouched alias stays


@pytest.mark.asyncio
async def test_split_marks_source_and_destination_edited():
    """Both the source (aliases removed) and destination get edit markers."""
    invalidate_alias_cache(NOVEL)
    amap = {"沙悟净": "八戒"}
    ov = [{
        "override_type": "alias_split",
        "override_key": "八戒→沙僧",
        "override_json": {"source": "八戒", "aliases": ["沙悟净"], "to": "沙僧"},
    }]
    with _patch_overrides(ov):
        await _apply_user_overrides(NOVEL, amap)
    targets = alias_resolver._alias_override_targets[NOVEL]
    assert "沙悟净" in targets.get("八戒", set())   # source marked
    assert "沙悟净" in targets.get("沙僧", set())   # destination marked


@pytest.mark.asyncio
async def test_split_records_detached_from_source():
    """Detached aliases are tracked per source so aggregation drops them even
    for to=None splits (where alias_map can't express the removal)."""
    from src.services.alias_resolver import get_detached_aliases

    for to in ("沙僧", None):
        invalidate_alias_cache(NOVEL)
        ov = [{
            "override_type": "alias_split",
            "override_key": f"八戒→{to or '(独立)'}",
            "override_json": {"source": "八戒", "aliases": ["沙悟净"], "to": to},
        }]
        with _patch_overrides(ov):
            await _apply_user_overrides(NOVEL, {"沙悟净": "八戒"})
        assert "沙悟净" in get_detached_aliases(NOVEL).get("八戒", set())


@pytest.mark.asyncio
async def test_split_to_new_independent_entity():
    """to=None → alias detaches and resolves to itself (removed from map)."""
    invalidate_alias_cache(NOVEL)
    amap = {"沙悟净": "八戒"}
    ov = [{
        "override_type": "alias_split",
        "override_key": "八戒",
        "override_json": {"aliases": ["沙悟净"], "to": None},
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    assert "沙悟净" not in out  # resolves to itself now


@pytest.mark.asyncio
async def test_split_to_same_name_does_not_self_map():
    """Splitting alias X with destination == X means 'X is its own entity' —
    detach without violating the canonical-not-self-map invariant."""
    invalidate_alias_cache(NOVEL)
    amap = {"沙僧": "八戒"}
    ov = [{
        "override_type": "alias_split",
        "override_key": "八戒→沙僧",
        "override_json": {"source": "八戒", "aliases": ["沙僧"], "to": "沙僧"},
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    assert "沙僧" not in out  # no 沙僧 -> 沙僧 self-map


@pytest.mark.asyncio
async def test_rename_relabels_entity_and_its_group():
    """entity_rename moves the whole group (canonical + aliases) to a new name."""
    invalidate_alias_cache(NOVEL)
    amap = {"少侠": "少年", "少年郎": "少年"}  # 少年 is canonical of a group
    ov = [{
        "override_type": "entity_rename",
        "override_key": "少年",
        "override_json": {"to": "杨过"},
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    assert out["少年"] == "杨过"       # old canonical now an alias of new
    assert out["少侠"] == "杨过"       # group members follow
    assert out["少年郎"] == "杨过"
    assert "杨过" not in out          # new canonical not self-mapped
    assert "少年" in alias_resolver._alias_override_targets[NOVEL].get("杨过", set())


@pytest.mark.asyncio
async def test_rename_noop_when_same_name():
    invalidate_alias_cache(NOVEL)
    ov = [{"override_type": "entity_rename", "override_key": "杨过", "override_json": {"to": "杨过"}}]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, {"过儿": "杨过"})
    assert out == {"过儿": "杨过"}     # unchanged


@pytest.mark.asyncio
async def test_apply_is_idempotent():
    """NFR4: applying the same overrides twice yields the same map."""
    invalidate_alias_cache(NOVEL)
    ov = [{
        "override_type": "alias_merge",
        "override_key": "沙僧",
        "override_json": {"members": ["沙僧", "沙悟净"], "canonical": "沙僧"},
    }]
    with _patch_overrides(ov):
        once = await _apply_user_overrides(NOVEL, {})
        twice = await _apply_user_overrides(NOVEL, dict(once))
    assert once == twice


@pytest.mark.asyncio
async def test_conflict_detected_via_snapshot():
    """FR7: auto result drifted from the snapshot at override-creation time."""
    invalidate_alias_cache(NOVEL)
    # At creation, 卷帘大将 auto-resolved to 八戒; now it resolves to 牛魔王.
    amap = {"卷帘大将": "牛魔王"}
    ov = [{
        "override_type": "alias_split",
        "override_key": "八戒",
        "override_json": {
            "aliases": ["卷帘大将"], "to": "沙僧",
            "auto_snapshot": {"卷帘大将": "八戒"},
        },
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, amap)
    # Override still wins (non-destructive)…
    assert out["卷帘大将"] == "沙僧"
    # …but the drift is flagged.
    assert "卷帘大将" in get_alias_conflicts(NOVEL)


@pytest.mark.asyncio
async def test_no_conflict_when_snapshot_matches():
    invalidate_alias_cache(NOVEL)
    amap = {"卷帘大将": "八戒"}
    ov = [{
        "override_type": "alias_split",
        "override_key": "八戒",
        "override_json": {
            "aliases": ["卷帘大将"], "to": "沙僧",
            "auto_snapshot": {"卷帘大将": "八戒"},
        },
    }]
    with _patch_overrides(ov):
        await _apply_user_overrides(NOVEL, amap)
    assert get_alias_conflicts(NOVEL) == set()


@pytest.mark.asyncio
async def test_invalidate_clears_conflicts():
    invalidate_alias_cache(NOVEL)
    alias_resolver._alias_conflicts[NOVEL] = {"x"}
    alias_resolver._alias_override_targets[NOVEL] = {"x": {"y"}}
    invalidate_alias_cache(NOVEL)
    assert get_alias_conflicts(NOVEL) == set()
    assert NOVEL not in alias_resolver._alias_override_targets


# ── LLM 决策 (llm_merge) 与手动 override 优先级 (Epic 2, FR-2.4) ──


@pytest.mark.asyncio
async def test_llm_merge_applies_like_manual_merge():
    """llm_merge 与手动 alias_merge 走同一 override 通道。"""
    invalidate_alias_cache(NOVEL)
    ov = [{
        "override_type": "llm_merge",
        "override_key": "观音菩萨",
        "override_json": {
            "members": ["观音菩萨", "南海观世音"],
            "canonical": "观音菩萨",
            "reason": "同一人物的不同尊称",
            "prompt_version": "er-cluster-v1",
        },
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, {})
    assert out["南海观世音"] == "观音菩萨"
    assert "观音菩萨" not in out  # canonical 不自映射


@pytest.mark.asyncio
async def test_manual_split_overrides_llm_merge():
    """FR-2.4: 手动 split 优先级高于 LLM 合并决策(无论写入顺序)。"""
    invalidate_alias_cache(NOVEL)
    # LLM 决策写入时间晚于手动 split,但手动仍然胜出。
    ov = [
        {
            "override_type": "alias_split",
            "override_key": "观音菩萨→观世音",
            "override_json": {"source": "观音菩萨", "aliases": ["南海观世音"],
                              "to": "观世音"},
        },
        {
            "override_type": "llm_merge",
            "override_key": "观音菩萨",
            "override_json": {"members": ["观音菩萨", "南海观世音"],
                              "canonical": "观音菩萨"},
        },
    ]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, {})
    # 手动 split 是 last writer:南海观世音 不并入 观音菩萨
    assert out["南海观世音"] == "观世音"


@pytest.mark.asyncio
async def test_manual_merge_locks_canonical_over_llm_decision():
    """FR-2.4: 手动 merge 选择的 canonical 覆盖 LLM 决策的 canonical。"""
    invalidate_alias_cache(NOVEL)
    ov = [
        {
            "override_type": "llm_merge",
            "override_key": "观音",
            "override_json": {"members": ["观音菩萨", "南海观世音", "观音"],
                              "canonical": "观音"},
        },
        {
            "override_type": "alias_merge",
            "override_key": "观音菩萨",
            "override_json": {"members": ["观音菩萨", "南海观世音", "观音"],
                              "canonical": "观音菩萨"},
        },
    ]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, {})
    assert out["南海观世音"] == "观音菩萨"
    assert out["观音"] == "观音菩萨"
    assert "观音菩萨" not in out


@pytest.mark.asyncio
async def test_llm_merge_survives_rebuild():
    """llm_merge 存于 entity_overrides,每次 build 重新应用 — survives-rebuild。"""
    invalidate_alias_cache(NOVEL)
    ov = [{
        "override_type": "llm_merge",
        "override_key": "观音菩萨",
        "override_json": {"members": ["观音菩萨", "南海观世音"],
                          "canonical": "观音菩萨"},
    }]
    with _patch_overrides(ov):
        first = await _apply_user_overrides(NOVEL, {})
        # 模拟 rebuild:自动层结果变化,override 重新应用结果不变
        second = await _apply_user_overrides(NOVEL, {"齐天大圣": "孙悟空"})
    assert first["南海观世音"] == "观音菩萨"
    assert second["南海观世音"] == "观音菩萨"
    assert second["齐天大圣"] == "孙悟空"  # 自动层结果保留


# ── Edit markers on profiles (Story 2.2) ────────────────────────


def _patch_markers(targets, conflicts):
    async def _targets(_novel_id):
        return targets

    def _conflicts(_novel_id):
        return conflicts

    return (
        patch("src.services.alias_resolver.get_override_targets", _targets),
        patch("src.services.alias_resolver.get_alias_conflicts", _conflicts),
    )


@pytest.mark.asyncio
async def test_edit_markers_stamp_person_profile():
    from src.models.entity_profiles import AliasEntry, PersonProfile
    from src.services.entity_aggregator import _apply_edit_markers

    profile = PersonProfile(
        name="沙僧",
        aliases=[AliasEntry(name="沙悟净", first_chapter=22),
                 AliasEntry(name="悟净", first_chapter=22)],
    )
    p_t, p_c = _patch_markers({"沙僧": {"沙悟净"}}, set())
    with p_t, p_c:
        await _apply_edit_markers(profile, NOVEL)

    assert profile.edit_status == "edited"
    assert profile.conflict is False
    edited = {a.name for a in profile.aliases if a.edited}
    assert edited == {"沙悟净"}  # only the override-attributed alias is marked


@pytest.mark.asyncio
async def test_edit_markers_set_conflict():
    from src.models.entity_profiles import AliasEntry, PersonProfile
    from src.services.entity_aggregator import _apply_edit_markers

    profile = PersonProfile(name="沙僧", aliases=[AliasEntry(name="卷帘大将", first_chapter=22)])
    p_t, p_c = _patch_markers({"沙僧": {"卷帘大将"}}, {"卷帘大将"})
    with p_t, p_c:
        await _apply_edit_markers(profile, NOVEL)
    assert profile.edit_status == "edited"
    assert profile.conflict is True


@pytest.mark.asyncio
async def test_edit_markers_noop_for_unedited_entity():
    from src.models.entity_profiles import PersonProfile
    from src.services.entity_aggregator import _apply_edit_markers

    profile = PersonProfile(name="孙悟空")
    p_t, p_c = _patch_markers({"沙僧": {"沙悟净"}}, set())
    with p_t, p_c:
        await _apply_edit_markers(profile, NOVEL)
    assert profile.edit_status == ""
    assert profile.conflict is False


# ── API router (Story 3.1) ──────────────────────────────────────


def _patch_route(saved_id=7):
    """Patch the route's novel check + store write + cache invalidation."""
    async def _get_novel(_novel_id):
        return {"id": _novel_id}

    async def _build_map(_novel_id):
        return {"沙悟净": "八戒"}

    async def _save(*_a, **_k):
        return saved_id

    return [
        patch("src.db.novel_store.get_novel", _get_novel),
        patch("src.api.routes.entity_overrides.build_alias_map", _build_map),
        patch("src.api.routes.entity_overrides.entity_override_store.save_override", _save),
        patch("src.api.routes.entity_overrides.entity_aggregator.invalidate_cache", lambda _n: None),
    ]


@pytest.mark.asyncio
async def test_route_merge_happy_path():
    from src.api.routes.entity_overrides import MergeRequest, merge_aliases

    patches = _patch_route(saved_id=42)
    for p in patches:
        p.start()
    try:
        res = await merge_aliases(NOVEL, MergeRequest(members=["沙僧", "沙悟净"], canonical="沙僧"))
    finally:
        for p in patches:
            p.stop()
    assert res == {"status": "ok", "override_id": 42}


@pytest.mark.asyncio
async def test_route_merge_rejects_canonical_not_in_members():
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import MergeRequest, merge_aliases

    patches = _patch_route()
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await merge_aliases(NOVEL, MergeRequest(members=["沙僧", "沙悟净"], canonical="八戒"))
        assert exc.value.status_code == 400
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_route_split_rejects_to_equals_source():
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import SplitRequest, split_aliases

    patches = _patch_route()
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await split_aliases(NOVEL, SplitRequest(source="八戒", aliases=["沙悟净"], to="八戒"))
        assert exc.value.status_code == 400
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_load_concept_overrides_parses_types():
    from src.services.encyclopedia_service import _load_concept_overrides

    ov = [
        {"override_type": "concept_rename", "override_key": "灵根", "override_json": {"to": "仙根"}},
        {"override_type": "concept_recategory", "override_key": "筋斗云", "override_json": {"to": "功法"}},
        {"override_type": "concept_delete", "override_key": "废话概念", "override_json": {}},
    ]
    async def _load(_n):
        return ov
    with patch("src.db.entity_override_store.load_overrides", _load):
        renames, recat, deleted = await _load_concept_overrides(NOVEL)
    assert renames == {"灵根": "仙根"}
    assert recat == {"筋斗云": "功法"}
    assert deleted == {"废话概念"}


@pytest.mark.asyncio
async def test_route_concept_rename_and_delete():
    from src.api.routes.entity_overrides import ConceptEditRequest, concept_rename, concept_delete

    async def _get_novel(_n):
        return {"id": _n}

    async def _save(*_a, **_k):
        return 5

    patches = [
        patch("src.db.novel_store.get_novel", _get_novel),
        patch("src.api.routes.entity_overrides.entity_override_store.save_override", _save),
        patch("src.api.routes.entity_overrides.entity_aggregator.invalidate_cache", lambda _n: None),
    ]
    for p in patches:
        p.start()
    try:
        r1 = await concept_rename(NOVEL, ConceptEditRequest(name="灵根", to="仙根"))
        r2 = await concept_delete(NOVEL, ConceptEditRequest(name="废话概念"))
    finally:
        for p in patches:
            p.stop()
    assert r1["status"] == "ok" and r2["status"] == "ok"


@pytest.mark.asyncio
async def test_route_concept_rename_rejects_same_name():
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import ConceptEditRequest, concept_rename

    async def _get_novel(_n):
        return {"id": _n}
    with patch("src.db.novel_store.get_novel", _get_novel):
        with pytest.raises(HTTPException) as exc:
            await concept_rename(NOVEL, ConceptEditRequest(name="灵根", to="灵根"))
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_route_rename_happy_path():
    from src.api.routes.entity_overrides import RenameRequest, rename_entity

    patches = _patch_route(saved_id=11)
    for p in patches:
        p.start()
    try:
        res = await rename_entity(NOVEL, RenameRequest(source="少年", to="杨过"))
    finally:
        for p in patches:
            p.stop()
    assert res == {"status": "ok", "override_id": 11}


@pytest.mark.asyncio
async def test_route_rename_rejects_same_name():
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import RenameRequest, rename_entity

    patches = _patch_route()
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await rename_entity(NOVEL, RenameRequest(source="杨过", to="杨过"))
        assert exc.value.status_code == 400
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_route_split_happy_path():
    from src.api.routes.entity_overrides import SplitRequest, split_aliases

    patches = _patch_route(saved_id=9)
    for p in patches:
        p.start()
    try:
        res = await split_aliases(NOVEL, SplitRequest(source="八戒", aliases=["沙悟净", "卷帘大将"], to="沙僧"))
    finally:
        for p in patches:
            p.stop()
    assert res == {"status": "ok", "override_id": 9}


# ── 实体级 override:entity_hide / entity_retype(issue #66 Epic 1)──


@pytest.mark.asyncio
async def test_store_hide_and_retype_roundtrip(memory_db):
    """新 override_type 走同一 (novel_id, type, key) UPSERT 契约。"""

    class _NonClosing:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def close(self):
            pass

    async def _proxy_factory():
        return _NonClosing(memory_db)

    await memory_db.execute(
        "INSERT INTO novels (id, title) VALUES (?, ?)", (NOVEL, "西游记"),
    )
    await memory_db.commit()

    with patch("src.db.entity_override_store.get_connection", _proxy_factory):
        await entity_override_store.save_override(
            NOVEL, "entity_hide", "那道人", {"auto_snapshot": {"type": "person"}},
        )
        await entity_override_store.save_override(
            NOVEL, "entity_retype", "花果山",
            {"from": "location", "to": "org", "auto_snapshot": {"type": "location"}},
        )
        rows = await entity_override_store.load_overrides(NOVEL)
        assert [r["override_type"] for r in rows] == ["entity_hide", "entity_retype"]
        assert rows[1]["override_json"]["to"] == "org"

        # 同 key UPSERT 改型,不重复
        await entity_override_store.save_override(
            NOVEL, "entity_retype", "花果山",
            {"from": "location", "to": "concept"},
        )
        rows = await entity_override_store.load_overrides(NOVEL)
        assert len(rows) == 2
        assert rows[1]["override_json"]["to"] == "concept"


@pytest.mark.asyncio
async def test_retype_marks_entity_edited_in_targets():
    """entity_retype 不改 alias_map,只把实体标记为 edited(FR6)。"""
    invalidate_alias_cache(NOVEL)
    amap = {"猴王": "孙悟空"}
    ov = [{
        "override_type": "entity_retype",
        "override_key": "花果山",
        "override_json": {"from": "location", "to": "org"},
    }]
    with _patch_overrides(ov):
        out = await _apply_user_overrides(NOVEL, dict(amap))
    assert out == amap  # alias_map 逐字节不变
    assert "花果山" in alias_resolver._alias_override_targets[NOVEL].get("花果山", set())


def _patch_visibility_route(saved_id=7, auto_entities=(), retype_map=None):
    """Patch hide/retype 端点的依赖:novel 检查、alias_map、自动类型、存储、缓存。"""
    from src.models.entity_profiles import EntitySummary

    async def _get_novel(_n):
        return {"id": _n}

    async def _build_map(_n):
        return {}

    async def _all_entities(_n, *, apply_visibility=True):
        return [
            EntitySummary(name=n, type=t, chapter_count=2, first_chapter=1)
            for n, t in auto_entities
        ]

    async def _save(*_a, **_k):
        return saved_id

    async def _vis(_n):
        return set(), (retype_map or {})

    return [
        patch("src.db.novel_store.get_novel", _get_novel),
        patch("src.api.routes.entity_overrides.build_alias_map", _build_map),
        patch("src.api.routes.entity_overrides.entity_aggregator.get_all_entities", _all_entities),
        patch("src.api.routes.entity_overrides.entity_override_store.save_override", _save),
        patch("src.api.routes.entity_overrides.entity_aggregator.invalidate_cache", lambda _n: None),
        patch(
            "src.api.routes.entity_overrides.visualization_service.invalidate_map_response_cache",
            lambda _n: None,
        ),
        patch("src.services.entity_visibility.get_visibility_overrides", _vis),
    ]


@pytest.mark.asyncio
async def test_route_hide_happy_path():
    from src.api.routes.entity_overrides import HideRequest, hide_entity

    patches = _patch_visibility_route(saved_id=21, auto_entities=[("那道人", "person")])
    for p in patches:
        p.start()
    try:
        res = await hide_entity(NOVEL, HideRequest(name="那道人"))
    finally:
        for p in patches:
            p.stop()
    assert res == {"status": "ok", "override_id": 21}


@pytest.mark.asyncio
async def test_route_hide_rejects_unknown_entity():
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import HideRequest, hide_entity

    patches = _patch_visibility_route(auto_entities=[("孙悟空", "person")])
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await hide_entity(NOVEL, HideRequest(name="不存在的人"))
        assert exc.value.status_code == 400
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_route_retype_happy_path():
    from src.api.routes.entity_overrides import RetypeRequest, retype_entity

    patches = _patch_visibility_route(saved_id=33, auto_entities=[("花果山", "location")])
    for p in patches:
        p.start()
    try:
        res = await retype_entity(NOVEL, RetypeRequest(name="花果山", to="org"))
    finally:
        for p in patches:
            p.stop()
    assert res == {"status": "ok", "override_id": 33}


@pytest.mark.asyncio
async def test_route_retype_rejects_invalid_type():
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import RetypeRequest, retype_entity

    patches = _patch_visibility_route(auto_entities=[("花果山", "location")])
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await retype_entity(NOVEL, RetypeRequest(name="花果山", to="alien"))
        assert exc.value.status_code == 400
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_route_retype_rejects_same_effective_type():
    """与当前生效类型(含已存在的改型)相同 → 400,不重复写入。"""
    from fastapi import HTTPException
    from src.api.routes.entity_overrides import RetypeRequest, retype_entity

    patches = _patch_visibility_route(
        auto_entities=[("花果山", "location")], retype_map={"花果山": "org"},
    )
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await retype_entity(NOVEL, RetypeRequest(name="花果山", to="org"))
        assert exc.value.status_code == 400
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_list_overrides_flags_retype_drift():
    """FR7 式漂移标记:自动类型与快照 from 不一致 → conflict=True(非破坏)。"""
    from src.api.routes.entity_overrides import list_overrides
    from src.models.entity_profiles import EntitySummary

    async def _get_novel(_n):
        return {"id": _n}

    async def _load(_n):
        return [{
            "id": 1, "override_type": "entity_retype", "override_key": "花果山",
            "override_json": {"from": "location", "to": "org"},
            "created_at": "2026-08-30",
        }]

    async def _build_map(_n):
        return {}

    async def _all_entities(_n, *, apply_visibility=True):
        # 重建后自动识别已变成 org — 与快照 from=location 漂移
        return [EntitySummary(name="花果山", type="org", chapter_count=3, first_chapter=1)]

    patches = [
        patch("src.db.novel_store.get_novel", _get_novel),
        patch("src.api.routes.entity_overrides.entity_override_store.load_overrides", _load),
        patch("src.api.routes.entity_overrides.build_alias_map", _build_map),
        patch("src.api.routes.entity_overrides.entity_aggregator.get_all_entities", _all_entities),
    ]
    for p in patches:
        p.start()
    try:
        res = await list_overrides(NOVEL)
    finally:
        for p in patches:
            p.stop()
    ov = res["overrides"][0]
    assert ov["conflict"] is True
    assert "org" in ov["conflict_reason"]


@pytest.mark.asyncio
async def test_list_overrides_flags_vanished_entity():
    """隐藏目标在重建后不存在 → conflict=True,override 不静默失效。"""
    from src.api.routes.entity_overrides import list_overrides

    async def _get_novel(_n):
        return {"id": _n}

    async def _load(_n):
        return [{
            "id": 2, "override_type": "entity_hide", "override_key": "那道人",
            "override_json": {"auto_snapshot": {"type": "person"}},
            "created_at": "2026-08-30",
        }]

    async def _build_map(_n):
        return {}

    async def _all_entities(_n, *, apply_visibility=True):
        return []

    patches = [
        patch("src.db.novel_store.get_novel", _get_novel),
        patch("src.api.routes.entity_overrides.entity_override_store.load_overrides", _load),
        patch("src.api.routes.entity_overrides.build_alias_map", _build_map),
        patch("src.api.routes.entity_overrides.entity_aggregator.get_all_entities", _all_entities),
    ]
    for p in patches:
        p.start()
    try:
        res = await list_overrides(NOVEL)
    finally:
        for p in patches:
            p.stop()
    assert res["overrides"][0]["conflict"] is True
