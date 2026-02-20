#!/usr/bin/env python3
"""Build zh_geonames.tsv — Chinese alternate name → coordinate index.

Joins GeoNames alternateNamesV2.txt (Chinese entries) with cities5000.txt
to produce a lightweight TSV lookup table for GeoResolver.

Usage:
    python scripts/build_zh_geonames.py \
        --alternate-names-path /path/to/alternateNamesV2.txt \
        --cities-path /path/to/cities5000.txt \
        [--output-path backend/data/zh_geonames.tsv]

Download source files:
    https://download.geonames.org/export/dump/alternateNamesV2.zip
    https://download.geonames.org/export/dump/cities5000.zip
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import opencc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────
ALTERNATE_NAMES_URL = "https://download.geonames.org/export/dump/alternateNamesV2.zip"
CITIES_URL = "https://download.geonames.org/export/dump/cities5000.zip"

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "backend" / "data" / "zh_geonames.tsv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build zh_geonames.tsv from GeoNames alternate names + cities data.",
        epilog=(
            "Source files:\n"
            f"  alternateNamesV2: {ALTERNATE_NAMES_URL}\n"
            f"  cities5000:       {CITIES_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--alternate-names-path",
        type=Path,
        required=True,
        help="Path to alternateNamesV2.txt (unzipped)",
    )
    parser.add_argument(
        "--cities-path",
        type=Path,
        required=True,
        help="Path to cities5000.txt (unzipped)",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output TSV path (default: {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


# ── Step 1: Load cities5000 ───────────────────────────────

def load_cities(cities_path: Path) -> dict[int, tuple[float, float, int, str, str]]:
    """Load cities5000.txt → {geonameid: (lat, lng, population, feature_code, country_code)}."""
    cities: dict[int, tuple[float, float, int, str, str]] = {}
    line_count = 0

    with open(cities_path, encoding="utf-8") as f:
        for line in f:
            line_count += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 15:
                continue
            try:
                gid = int(parts[0])
                lat = float(parts[4])
                lng = float(parts[5])
                feature_code = parts[7]
                country_code = parts[8]
                pop = int(parts[14])
            except (ValueError, IndexError):
                continue
            cities[gid] = (lat, lng, pop, feature_code, country_code)

    logger.info("cities5000: %d 行读取, %d 条目加载", line_count, len(cities))
    return cities


# ── Step 2: Stream alternateNamesV2 ──────────────────────

def stream_zh_alternates(
    alt_path: Path,
    cities: dict[int, tuple[float, float, int, str, str]],
) -> tuple[list[tuple[str, float, float, int, str, str, int]], dict[str, int]]:
    """Stream alternateNamesV2.txt, extract zh* entries, JOIN with cities.

    Returns:
        (rows, stats) where rows = [(zh_name, lat, lng, pop, feat, cc, gid), ...]
        and stats = {"total_lines", "zh_entries", "join_hits", "pre_dedup_rows"}
    """
    stats = {"total_lines": 0, "zh_entries": 0, "join_hits": 0, "pre_dedup_rows": 0}

    # Intermediate: {(zh_name, geonameid): (row_tuple, is_preferred)}
    best: dict[tuple[str, int], tuple[tuple[str, float, float, int, str, str, int], bool]] = {}

    with open(alt_path, encoding="utf-8") as f:
        for line in f:
            stats["total_lines"] += 1
            if stats["total_lines"] % 5_000_000 == 0:
                logger.info("  ... 已处理 %dM 行", stats["total_lines"] // 1_000_000)

            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue

            iso_lang = parts[2]
            if not iso_lang.startswith("zh"):
                continue
            stats["zh_entries"] += 1

            try:
                gid = int(parts[1])
            except ValueError:
                continue

            if gid not in cities:
                continue
            stats["join_hits"] += 1

            zh_name = parts[3].strip()
            if not zh_name:
                continue

            is_preferred = len(parts) > 4 and parts[4] == "1"
            lat, lng, pop, feat, cc = cities[gid]
            row = (zh_name, lat, lng, pop, feat, cc, gid)

            key = (zh_name, gid)
            existing = best.get(key)
            if existing is None:
                best[key] = (row, is_preferred)
            elif is_preferred and not existing[1]:
                # Prefer the isPreferredName=1 entry
                best[key] = (row, is_preferred)

    rows = [entry[0] for entry in best.values()]
    stats["pre_dedup_rows"] = len(rows)
    return rows, stats


# ── Step 2.5: Generate simplified Chinese variants ───────

# Reusable converter (Traditional → Simplified)
_T2S = opencc.OpenCC("t2s")


def add_simplified_variants(
    rows: list[tuple[str, float, float, int, str, str, int]],
) -> tuple[list[tuple[str, float, float, int, str, str, int]], int]:
    """For names containing traditional characters, add simplified variants.

    GeoNames zh-Hant entries often only have traditional forms (e.g. 紐約).
    Mainland-Chinese translated novels use simplified forms (纽约).
    This step ensures both forms are present.

    Returns (extended_rows, added_count).
    """
    # Build set of existing (name, geonameid) for dedup
    existing: set[tuple[str, int]] = set()
    for zh_name, _lat, _lng, _pop, _feat, _cc, gid in rows:
        existing.add((zh_name, gid))

    added = 0
    new_rows: list[tuple[str, float, float, int, str, str, int]] = []
    for zh_name, lat, lng, pop, feat, cc, gid in rows:
        simplified = _T2S.convert(zh_name)
        if simplified != zh_name and (simplified, gid) not in existing:
            new_rows.append((simplified, lat, lng, pop, feat, cc, gid))
            existing.add((simplified, gid))
            added += 1

    rows.extend(new_rows)
    return rows, added


# ── Step 3: Write TSV output ─────────────────────────────

def write_tsv(rows: list[tuple[str, float, float, int, str, str, int]], output_path: Path) -> None:
    """Write rows to TSV file. Format: zh_name\\tlat\\tlng\\tpop\\tfeature_code\\tcountry_code\\tgeonameid"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Sort by population descending (most important cities first) then by name
    rows.sort(key=lambda r: (-r[3], r[0]))

    with open(output_path, "w", encoding="utf-8") as f:
        for zh_name, lat, lng, pop, feat, cc, gid in rows:
            f.write(f"{zh_name}\t{lat:.5f}\t{lng:.5f}\t{pop}\t{feat}\t{cc}\t{gid}\n")

    size_kb = output_path.stat().st_size / 1024
    logger.info("输出: %s (%.1f KB, %d 行)", output_path, size_kb, len(rows))


# ── Main ─────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # Validate input files exist
    if not args.alternate_names_path.exists():
        logger.error(
            "文件不存在: %s\n"
            "请下载并解压: %s",
            args.alternate_names_path,
            ALTERNATE_NAMES_URL,
        )
        sys.exit(1)

    if not args.cities_path.exists():
        logger.error(
            "文件不存在: %s\n"
            "请下载并解压: %s",
            args.cities_path,
            CITIES_URL,
        )
        sys.exit(1)

    t0 = time.time()

    # Step 1: Load cities
    logger.info("=== Step 1/3: 加载 cities5000 ===")
    cities = load_cities(args.cities_path)

    # Step 2: Stream and filter alternate names
    logger.info("=== Step 2/3: 流式过滤 alternateNamesV2 (zh*) ===")
    rows, stats = stream_zh_alternates(args.alternate_names_path, cities)

    # Step 2.5: Generate simplified Chinese variants
    logger.info("=== Step 2.5: 繁→简变体生成 ===")
    rows, simplified_added = add_simplified_variants(rows)

    # Step 3: Write output
    logger.info("=== Step 3/3: 写入 TSV ===")
    write_tsv(rows, args.output_path)

    elapsed = time.time() - t0

    # Summary
    logger.info("=== 完成 (%.1f 秒) ===", elapsed)
    logger.info("  alternateNamesV2 总行数: %d", stats["total_lines"])
    logger.info("  zh* 条目数:              %d", stats["zh_entries"])
    logger.info("  JOIN 命中数:             %d", stats["join_hits"])
    logger.info("  去重后输出行数:          %d", stats["pre_dedup_rows"])
    logger.info("  繁→简新增行数:          %d", simplified_added)
    logger.info("  最终输出行数:            %d", len(rows))


if __name__ == "__main__":
    main()
