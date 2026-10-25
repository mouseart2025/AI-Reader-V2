"""geo scope/type genre overrides (urban + CN-only historical aliases).

白夜行 (urban, cached geo_type=fantasy, 42/352 real names) used to stay on
the layered map because "urban" was not in the genre override list. The
world dataset then resolved 东京 to Tokyo instead of the curated Song-era
Kaifeng coordinate.
"""

from unittest.mock import patch

import pytest

from src.services.geo_resolver import auto_resolve


class _FakeResolver:
    async def ensure_ready(self):
        return None

    def resolve_names(self, names, parent_map=None, type_map=None):
        return {"东京": (35.6895, 139.69171)}


@pytest.mark.asyncio
async def test_urban_cached_fantasy_is_probed_as_mixed():
    with (
        patch("src.services.geo_resolver.detect_geo_scope", return_value="world"),
        patch("src.services.geo_resolver.GeoResolver", return_value=_FakeResolver()),
    ):
        _scope, geo_type, resolver, resolved = await auto_resolve(
            "urban", ["东京", "大阪"], ["东京"],
            known_geo_type="fantasy",
        )

    assert geo_type == "mixed"
    assert resolver is not None
    assert resolved["东京"][0] > 35


@pytest.mark.asyncio
async def test_wuxia_cached_fantasy_stays_layered():
    """wuxia is deliberately excluded: xianxia novels are often mislabeled."""
    _scope, geo_type, resolver, resolved = await auto_resolve(
        "wuxia", ["青牛镇"], ["青牛镇"], known_geo_type="fantasy",
    )
    assert geo_type == "fantasy"
    assert resolver is None
    assert resolved == {}


class _RatioResolver:
    def __init__(self, ratio: float):
        self.ratio = ratio

    async def ensure_ready(self):
        return None

    def resolve_names(self, names, parent_map=None, type_map=None):
        count = int(len(names) * self.ratio)
        return {name: (30.0, 110.0) for name in names[:count]}


@pytest.mark.asyncio
async def test_cached_fantasy_probe_upgrades_high_resolution():
    """边城: 29/94 names resolve, but the cached type is fantasy."""
    names = [f"地名{i}" for i in range(100)]
    with (
        patch("src.services.geo_resolver.detect_geo_scope", return_value="cn"),
        patch(
            "src.services.geo_resolver.GeoResolver",
            return_value=_RatioResolver(0.30),
        ),
    ):
        _scope, geo_type, resolver, resolved = await auto_resolve(
            None, names, names, known_geo_type="fantasy",
        )
    assert geo_type == "mixed"
    assert resolver is not None
    assert len(resolved) == 30


@pytest.mark.asyncio
async def test_cached_fantasy_probe_keeps_low_resolution_layered():
    names = [f"地名{i}" for i in range(100)]
    with (
        patch("src.services.geo_resolver.detect_geo_scope", return_value="cn"),
        patch(
            "src.services.geo_resolver.GeoResolver",
            return_value=_RatioResolver(0.05),
        ),
    ):
        _scope, geo_type, resolver, resolved = await auto_resolve(
            None, names, names, known_geo_type="fantasy",
        )
    assert geo_type == "fantasy"
    assert resolver is None
    assert resolved == {}


@pytest.mark.asyncio
async def test_explicit_fantasy_genre_is_never_probed():
    names = [f"地名{i}" for i in range(100)]
    with (
        patch("src.services.geo_resolver.detect_geo_scope", return_value="cn"),
        patch(
            "src.services.geo_resolver.GeoResolver",
            return_value=_RatioResolver(1.0),
        ),
    ):
        _scope, geo_type, resolver, _resolved = await auto_resolve(
            "fantasy", names, names, known_geo_type="fantasy",
        )
    assert geo_type == "fantasy"
    assert resolver is None
