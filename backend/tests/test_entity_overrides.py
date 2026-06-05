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
