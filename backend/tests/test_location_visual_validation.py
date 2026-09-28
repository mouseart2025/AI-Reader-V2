"""Pure validation layers for the location-visual extraction pass.

The LLM side is not exercised here on purpose — what this pass can get wrong in a
way that silently reaches the map is the *validation*: an out-of-enum or
fabricated value that gets through becomes a real terrain influence point.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.models.location_visual import Landmark, LocationVisual, Terrain
from src.services.location_influence import influence_classes
from src.services.location_visual_extractor import (
    _norm_enum,
    _validate,
    evidence_ok,
    summarize,
)

# ── enum normalisation ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("mountain", "mountain"),
        ("  Mountain ", "mountain"),
        (None, None),
        ("", None),
        # The measured failure mode: a JSON null written as the string "null".
        ("null", None),
        ("None", None),
        ("无", None),
        # Out-of-enum must collapse to None rather than pass through.
        ("皇宫大殿", None),
        ("volcano", None),
        # Explicit Chinese aliases are honoured...
        ("山地", "mountain"),
        ("海滨", "coastal"),
    ],
)
def test_norm_enum_terrain(value, expected):
    from src.models.location_visual import TERRAIN_VALUES
    from src.services.location_visual_extractor import TERRAIN_ALIASES

    assert _norm_enum(value, TERRAIN_ALIASES, TERRAIN_VALUES) == expected


def test_norm_enum_landmark_rejects_the_measured_out_of_enum_value():
    from src.models.location_visual import LANDMARK_VALUES
    from src.services.location_visual_extractor import LANDMARK_ALIASES

    assert _norm_enum("皇宫大殿", LANDMARK_ALIASES, LANDMARK_VALUES) is None
    assert _norm_enum("宫殿", LANDMARK_ALIASES, LANDMARK_VALUES) == "palace"


# ── evidence grounding ───────────────────────────────────────────────────────

DESC = "那山正当中有一块石碣，上写花果山福地，水帘洞洞天。"


def test_evidence_must_be_verbatim():
    assert evidence_ok("花果山福地", DESC, "花果山") is True
    # Whitespace differences do not count as rewriting.
    assert evidence_ok("花果山 福地", DESC, "花果山") is True
    # A paraphrase is not evidence.
    assert evidence_ok("这座山很有名", DESC, "花果山") is False


def test_evidence_cannot_just_restate_the_name():
    assert evidence_ok("花果山", DESC, "花果山") is False


def test_evidence_rejects_empty_and_trivial():
    assert evidence_ok("", DESC, "花果山") is False
    assert evidence_ok(None, DESC, "花果山") is False
    assert evidence_ok("山", DESC, "花果山") is False  # too short to mean anything
    assert evidence_ok("花果山福地", "", "花果山") is False  # no description to ground in


# ── the batch-level validation ───────────────────────────────────────────────

BATCH = [{"name": "花果山", "type": "山", "description": DESC}]


def test_validate_keeps_raw_so_the_loss_is_measurable():
    raw = {
        "花果山": {"terrain": "mountain", "landmark": "皇宫大殿", "evidence": "花果山福地"}
    }
    (v,) = _validate(raw, BATCH)
    assert v.terrain is Terrain.MOUNTAIN
    assert v.landmark is None  # rejected
    assert "皇宫大殿" in v.raw  # ...but the model's word is preserved for audit
    assert v.evidence == "花果山福地"


def test_validate_handles_string_null_and_missing_entries():
    raw = {"花果山": {"terrain": "null", "landmark": None, "evidence": "不是原文"}}
    (v,) = _validate(raw, BATCH)
    assert v.terrain is None and v.landmark is None and v.evidence == ""

    # A location the model simply skipped still yields a row (asked, nothing back).
    (v2,) = _validate({}, BATCH)
    assert v2.name == "花果山" and v2.terrain is None


def test_validate_does_not_invent_rows_for_unknown_names():
    raw = {"花果山": {"terrain": "mountain"}, "不存在的山": {"terrain": "mountain"}}
    out = _validate(raw, BATCH)
    assert [v.name for v in out] == ["花果山"]


def test_summarize_counts_rejections_separately():
    visuals = [
        LocationVisual(name="A", terrain=Terrain.MOUNTAIN, evidence="x" * 8),
        LocationVisual(name="B", landmark=Landmark.TEMPLE, evidence="y" * 8),
        LocationVisual(
            name="C", raw='{"terrain": "volcano", "landmark": null, "evidence": ""}'
        ),
        LocationVisual(name="D"),
    ]
    s = summarize(visuals)
    assert s["locations"] == 4
    assert s["terrain_hit"] == 1
    assert s["landmark_hit"] == 1
    assert s["evidence_ok"] == 2
    assert s["both_empty"] == 2
    assert s["terrain_rejected"] == 1  # volcano was offered and refused
    assert s["terrain_dist"] == {"mountain": 1}


# ── the classifier the bake shares with the audit script ─────────────────────


def test_influence_classes_covers_every_branch():
    assert influence_classes("花果山", "", "") == frozenset({"mountain"})
    assert influence_classes("碧波潭", "", "") == frozenset({"water"})
    assert influence_classes("黑松林", "", "") == frozenset({"forest"})
    assert influence_classes("某个地方", "", "mountain") == frozenset({"mountain"})
    assert influence_classes("某个地方", "河流", "") == frozenset({"water"})
    assert influence_classes("某个地方", "", "island") == frozenset({"water"})
    assert influence_classes("傲来国", "城市", "city") == frozenset()
    # A place can be more than one thing.
    assert influence_classes("花果山", "山", "mountain") >= {"mountain"}


def test_influence_classes_keeps_the_suffix_rule_that_fixed_false_positives():
    # v0.67.1: substring matching on the NAME produced these. They must stay clean.
    assert "water" not in influence_classes("水帘洞", "", "")
    assert "water" not in influence_classes("城池", "", "")
    assert "water" not in influence_classes("南海普陀山", "", "")
    # ...while the type channel is still allowed to say water.
    assert "water" in influence_classes("碧波潭龙宫", "水池", "")


# ── bake parity anchor ───────────────────────────────────────────────────────

# The classifier was extracted out of `generate_terrain` into `location_influence`
# so that the audit script shares ONE implementation. That refactor had to be
# behaviour-preserving, and the only honest way to show it is the bake's own bytes.
#
# ⚠️ Changing the terrain RECIPE on purpose means updating this constant AND
# bumping `_TERRAIN_VERSION` — otherwise the cache keeps serving the old product
# (that trap is documented in the project notes).
_PARITY_SHA256 = "71ec494d1d0af57100965f9f9410f0baf8268aef104debbac51dc097b6ca8669"


def _bake(novel="_terrain_parity") -> bytes:
    # The novel id seeds the noise fields, so the anchor below is only valid for
    # this exact id — it is the one the pre-refactor bake was measured with.
    from src.services.map_layout_service import generate_terrain

    locs = [
        {"name": "花果山", "type": "山", "icon": "mountain"},
        {"name": "水帘洞", "type": "洞府", "icon": "cave"},
        {"name": "流沙河", "type": "河流", "icon": "water"},
        {"name": "黑松林", "type": "树林", "icon": "forest"},
        {"name": "碧波潭龙宫", "type": "宫殿", "icon": "palace"},
        {"name": "傲来国", "type": "城市", "icon": "city"},
        {"name": "南海普陀山", "type": "山", "icon": "mountain"},
        {"name": "乌鸡国", "type": "区域", "icon": ""},  # deliberately not in layout
    ]
    layout = {
        loc["name"]: (100.0 + 90 * i, 80.0 + 70 * ((i * 3) % 5))
        for i, loc in enumerate(locs)
        if loc["name"] != "乌鸡国"
    }
    path = generate_terrain(
        locs, layout, novel, size=256, canvas_width=2560, canvas_height=1440
    )
    return Path(path).read_bytes()


def test_terrain_bake_matches_the_parity_anchor():
    got = hashlib.sha256(_bake()).hexdigest()
    assert got == _PARITY_SHA256, (
        "terrain bake changed. If that was intentional, bump _TERRAIN_VERSION and "
        "update _PARITY_SHA256; if it was not, the influence classifier drifted."
    )


def test_terrain_bake_is_deterministic():
    assert _bake("parity-probe-a") == _bake("parity-probe-a")
