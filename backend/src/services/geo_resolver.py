"""GeoResolver — match novel location names to real-world GeoNames coordinates.

Supports multiple geographic datasets:
  - "cn"    → GeoNames CN.zip  (comprehensive Chinese locations, ~10MB)
  - "world" → GeoNames cities15000.zip (global cities with pop > 15000, ~2MB)

Auto-detects which dataset to use based on novel genre and location name
characteristics. Provides geo_type detection (realistic/mixed/fantasy) and
Mercator projection to canvas coordinates.

Architecture is extensible: add a new GeoDatasetConfig entry for custom
datasets (e.g., game worlds with a hand-crafted TSV).
"""

from __future__ import annotations

import io
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from src.infra.config import GEONAMES_DIR

logger = logging.getLogger(__name__)

# ── Dataset configuration ────────────────────────────────


@dataclass(frozen=True)
class GeoDatasetConfig:
    """Configuration for a single geographic dataset."""
    key: str                # unique identifier: "cn", "world", ...
    url: str                # download URL
    zip_member: str         # expected filename inside the zip
    description: str = ""


# Built-in datasets
DATASET_CN = GeoDatasetConfig(
    key="cn",
    url="https://download.geonames.org/export/dump/CN.zip",
    zip_member="CN.txt",
    description="GeoNames China — comprehensive Chinese locations",
)

DATASET_WORLD = GeoDatasetConfig(
    key="world",
    url="https://download.geonames.org/export/dump/cities15000.zip",
    zip_member="cities15000.txt",
    description="GeoNames cities15000 — global cities with pop > 15000",
)

DATASET_REGISTRY: dict[str, GeoDatasetConfig] = {
    "cn": DATASET_CN,
    "world": DATASET_WORLD,
}


# ── Constants ────────────────────────────────────────────

# Common Chinese geographic suffixes to strip for fuzzy matching
_GEO_SUFFIXES = re.compile(
    r"(城|府|州|县|镇|村|寨|山|河|湖|泊|谷|寺|庙|宫|殿|关|岭|峰|洞|岛|港|塘|坊|营|堡|隘|驿)$"
)

# Feature codes that represent administrative/populated places (preferred in disambiguation)
_ADMIN_CODES = frozenset({
    "PPLC",   # capital
    "PPLA",   # seat of first-order admin
    "PPLA2",  # seat of second-order admin
    "PPLA3",
    "PPLA4",
    "PPL",    # populated place
    "ADM1",   # first-order admin
    "ADM2",
    "ADM3",
    "ADM4",
})

# Genre hints that are definitively fantasy → skip geo resolution entirely
_FANTASY_GENRES = frozenset({"fantasy"})


# ── Data model ───────────────────────────────────────────


@dataclass(slots=True)
class GeoEntry:
    """A single GeoNames record."""
    lat: float
    lng: float
    feature_code: str
    population: int
    name: str  # primary name for logging


# ── GeoResolver ──────────────────────────────────────────


class GeoResolver:
    """Resolve place names to real-world coordinates via GeoNames.

    Supports multiple datasets. Index is cached at class level per dataset key
    to avoid redundant parsing across requests.
    """

    # Class-level index caches: {dataset_key: {name: [GeoEntry, ...]}}
    _indexes: dict[str, dict[str, list[GeoEntry]]] = {}

    def __init__(self, dataset_key: str = "cn") -> None:
        if dataset_key not in DATASET_REGISTRY:
            raise ValueError(f"Unknown geo dataset: {dataset_key!r}")
        self.dataset_key = dataset_key
        self.config = DATASET_REGISTRY[dataset_key]

    # ── Data download & loading ──────────────────────────

    def _tsv_path(self) -> Path:
        return GEONAMES_DIR / self.config.zip_member

    async def _ensure_data(self) -> None:
        """Download the dataset zip from GeoNames if the TSV doesn't exist."""
        tsv = self._tsv_path()
        if tsv.exists():
            return
        GEONAMES_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Downloading GeoNames dataset [%s] from %s ...",
            self.dataset_key, self.config.url,
        )
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(self.config.url)
            resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for member in zf.namelist():
                if member.endswith(".txt"):
                    zf.extract(member, GEONAMES_DIR)
                    logger.info("Extracted %s to %s", member, GEONAMES_DIR)
                    break
        if not tsv.exists():
            raise FileNotFoundError(f"Expected {tsv} after extraction")
        logger.info("GeoNames dataset [%s] ready at %s", self.dataset_key, tsv)

    def _load_index(self) -> dict[str, list[GeoEntry]]:
        """Parse the GeoNames TSV into an in-memory lookup dict.

        Key = place name (primary + Chinese/CJK alternate names).
        Value = list of GeoEntry (multiple entries can share the same name).

        GeoNames TSV columns (tab-separated, 19 fields):
          0:geonameid  1:name  2:asciiname  3:alternatenames
          4:latitude  5:longitude  6:feature_class  7:feature_code
          8:country_code  9:cc2  10:admin1  11:admin2  12:admin3  13:admin4
          14:population  15:elevation  16:dem  17:timezone  18:modification_date
        """
        if self.dataset_key in GeoResolver._indexes:
            return GeoResolver._indexes[self.dataset_key]

        tsv = self._tsv_path()
        logger.info("Loading GeoNames index [%s] from %s ...", self.dataset_key, tsv)
        index: dict[str, list[GeoEntry]] = {}
        count = 0

        with open(tsv, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 15:
                    continue
                try:
                    lat = float(parts[4])
                    lng = float(parts[5])
                    feature_code = parts[7]
                    population = int(parts[14]) if parts[14] else 0
                except (ValueError, IndexError):
                    continue

                primary_name = parts[1].strip()
                entry = GeoEntry(
                    lat=lat, lng=lng,
                    feature_code=feature_code,
                    population=population,
                    name=primary_name,
                )

                # Index by primary name
                if primary_name:
                    index.setdefault(primary_name, []).append(entry)

                # Index by alternate names (focus on CJK names for Chinese lookup)
                alt_names = parts[3] if len(parts) > 3 else ""
                if alt_names:
                    for alt in alt_names.split(","):
                        alt = alt.strip()
                        if not alt or alt == primary_name:
                            continue
                        # For "cn" dataset: only index CJK alternate names
                        # For "world" dataset: index all alternate names
                        #   (catches Chinese translations like 伦敦, 巴黎, etc.)
                        if self.dataset_key == "cn" and not _has_cjk(alt):
                            continue
                        index.setdefault(alt, []).append(entry)
                count += 1

        GeoResolver._indexes[self.dataset_key] = index
        logger.info(
            "GeoNames index [%s] loaded: %d records, %d unique lookup keys",
            self.dataset_key, count, len(index),
        )
        return index

    # ── Name resolution ──────────────────────────────────

    async def ensure_ready(self) -> None:
        """Ensure dataset is downloaded and index is loaded."""
        await self._ensure_data()
        self._load_index()

    def resolve_names(
        self, names: list[str],
    ) -> dict[str, tuple[float, float]]:
        """Resolve a list of place names to (lat, lng) coordinates.

        Three-level matching:
          1. Exact match
          2. Suffix stripping (remove common Chinese geographic suffixes)
          3. Disambiguation: prefer higher population / admin feature codes

        Returns dict of {name: (lat, lng)} for successfully resolved names.
        """
        index = self._load_index()
        result: dict[str, tuple[float, float]] = {}

        for name in names:
            if not name or len(name) < 2:
                continue

            # Level 1: exact match
            entries = index.get(name)

            # Level 2: suffix stripping
            if not entries:
                stripped = _GEO_SUFFIXES.sub("", name)
                if stripped and stripped != name and len(stripped) >= 2:
                    entries = index.get(stripped)

            if not entries:
                continue

            # Disambiguation: pick best entry
            best = _pick_best_entry(entries)
            result[name] = (best.lat, best.lng)

        logger.info(
            "GeoResolver[%s]: resolved %d / %d names (%.0f%%)",
            self.dataset_key, len(result), len(names),
            100 * len(result) / max(len(names), 1),
        )
        return result

    def detect_geo_type(self, names: list[str]) -> str:
        """Detect whether the novel's locations are realistic, mixed, or fantasy.

        Returns:
          - "realistic": >= 50% matched
          - "mixed": >= 25% matched
          - "fantasy": < 25% matched
        """
        if not names:
            return "fantasy"

        resolved = self.resolve_names(names)
        ratio = len(resolved) / len(names)

        if ratio >= 0.5:
            geo_type = "realistic"
        elif ratio >= 0.25:
            geo_type = "mixed"
        else:
            geo_type = "fantasy"

        logger.info(
            "GeoResolver[%s]: geo_type=%s (matched %d/%d = %.0f%%)",
            self.dataset_key, geo_type, len(resolved), len(names), ratio * 100,
        )
        return geo_type

    # ── Mercator projection ──────────────────────────────

    def project_to_canvas(
        self,
        resolved: dict[str, tuple[float, float]],
        locations: list[dict],
        canvas_w: int,
        canvas_h: int,
        *,
        padding: float = 0.08,
    ) -> list[dict]:
        """Project resolved lat/lng to canvas coordinates using Mercator projection.

        Returns a layout list compatible with layout_to_list() output format:
          [{"name": str, "x": float, "y": float, "radius": int}, ...]

        Only includes resolved locations. Unresolved locations are handled
        separately by place_unresolved_near_neighbors().
        """
        if not resolved:
            return []

        # Mercator projection: lng → x, lat → y via log(tan)
        projected: dict[str, tuple[float, float]] = {}
        for name, (lat, lng) in resolved.items():
            mx = lng  # longitude maps linearly to x
            my = math.degrees(
                math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
            )
            projected[name] = (mx, my)

        # Compute bounding box of projected points
        xs = [p[0] for p in projected.values()]
        ys = [p[1] for p in projected.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        # Avoid division by zero if all points are at the same location
        span_x = max_x - min_x or 1.0
        span_y = max_y - min_y or 1.0

        # Fit to canvas with padding, preserving aspect ratio
        pad_x = canvas_w * padding
        pad_y = canvas_h * padding
        usable_w = canvas_w - 2 * pad_x
        usable_h = canvas_h - 2 * pad_y

        scale = min(usable_w / span_x, usable_h / span_y)
        # Center the map
        offset_x = pad_x + (usable_w - span_x * scale) / 2
        offset_y = pad_y + (usable_h - span_y * scale) / 2

        # Build location lookup for radius calculation
        loc_by_name = {loc["name"]: loc for loc in locations}

        result: list[dict] = []
        for name, (mx, my) in projected.items():
            cx = offset_x + (mx - min_x) * scale
            # Invert Y axis (canvas Y increases downward, latitude increases upward)
            cy = offset_y + (max_y - my) * scale

            loc = loc_by_name.get(name, {})
            mention = loc.get("mention_count", 1)
            level = loc.get("level", 0)
            radius = max(15, min(60, 10 + mention * 2 + (3 - level) * 5))

            result.append({
                "name": name,
                "x": round(cx, 1),
                "y": round(cy, 1),
                "radius": radius,
            })

        return result


# ── Geo scope detection ──────────────────────────────────


def detect_geo_scope(
    genre_hint: str | None,
    location_names: list[str],
) -> str:
    """Determine which geo dataset to use for a novel.

    Returns:
      - "cn"    — primarily Chinese locations (historical, wuxia, realistic, urban)
      - "world" — international / global locations (adventure, translated novels)
      - "none"  — fantasy / xianxia (skip geo resolution)

    Detection strategy:
      1. If genre is known fantasy → "none"
      2. If genre is known Chinese type → "cn"
      3. Otherwise, analyze location names:
         - If > 60% of names are CJK-only → "cn"
         - If any names match common world city patterns → "world"
         - Default → "cn" (most uploaded novels are Chinese)
    """
    genre = (genre_hint or "").lower()

    # Definite fantasy → skip
    if genre in _FANTASY_GENRES:
        return "none"

    # Known Chinese genre → CN dataset
    if genre in ("historical", "wuxia", "realistic", "urban"):
        return "cn"

    # For unknown/adventure/other genres: analyze location name characteristics
    if not location_names:
        return "cn"  # default

    cjk_only_count = 0
    for name in location_names:
        if _is_cjk_only(name):
            cjk_only_count += 1

    cjk_ratio = cjk_only_count / len(location_names)

    if cjk_ratio > 0.6:
        return "cn"
    else:
        # Mix of CJK and non-CJK, or mostly translated names → world dataset
        return "world"


async def auto_resolve(
    genre_hint: str | None,
    location_names: list[str],
    major_names: list[str],
) -> tuple[str, str, GeoResolver | None, dict[str, tuple[float, float]]]:
    """High-level entry point: detect scope, load dataset, resolve names.

    Args:
        genre_hint: WorldStructure.novel_genre_hint
        location_names: all location names for resolution
        major_names: major location names (level <= 3) for geo_type detection

    Returns:
        (geo_scope, geo_type, resolver_or_none, resolved_coords)
    """
    geo_scope = detect_geo_scope(genre_hint, location_names)

    if geo_scope == "none":
        return geo_scope, "fantasy", None, {}

    resolver = GeoResolver(dataset_key=geo_scope)
    await resolver.ensure_ready()

    geo_type = resolver.detect_geo_type(major_names)

    # If CN dataset matches poorly, try world dataset as fallback
    if geo_type == "fantasy" and geo_scope == "cn":
        logger.info("CN dataset matched poorly, trying world dataset as fallback")
        resolver_world = GeoResolver(dataset_key="world")
        await resolver_world.ensure_ready()
        geo_type_world = resolver_world.detect_geo_type(major_names)
        if geo_type_world != "fantasy":
            # World dataset matched better — use it
            resolved = resolver_world.resolve_names(location_names)
            return "world", geo_type_world, resolver_world, resolved

    if geo_type == "fantasy":
        return geo_scope, "fantasy", None, {}

    resolved = resolver.resolve_names(location_names)
    return geo_scope, geo_type, resolver, resolved


# ── Module-level helpers ─────────────────────────────────


def _has_cjk(text: str) -> bool:
    """Check if text contains any CJK Unified Ideograph characters."""
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            return True
    return False


def _is_cjk_only(text: str) -> bool:
    """Check if text consists only of CJK characters (no Latin/digits)."""
    for ch in text:
        cp = ord(ch)
        if not (0x4E00 <= cp <= 0x9FFF):
            return False
    return True


def _pick_best_entry(entries: list[GeoEntry]) -> GeoEntry:
    """Pick the best entry when multiple GeoNames records share the same name.

    Priority:
      1. Administrative/populated place feature codes (PPL*, ADM*)
      2. Highest population
    """
    if len(entries) == 1:
        return entries[0]

    # Separate admin-type entries
    admin_entries = [e for e in entries if e.feature_code in _ADMIN_CODES]
    pool = admin_entries if admin_entries else entries

    # Pick highest population
    return max(pool, key=lambda e: e.population)
