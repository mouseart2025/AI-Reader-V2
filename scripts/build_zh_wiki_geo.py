#!/usr/bin/env python3
"""Build zh_wiki_geo.tsv — Chinese Wikipedia geo index (title/redirect → coordinates).

Wikipedia titles are editor-curated, so this layer fixes classes of errors that
GeoNames alternateNamesV2 cannot:
  - non-city features with proper zh names (涅瓦河/西伯利亚/秦岭/太湖)
  - famous places whose GeoNames zh alias is missing or misattached
    (彼得堡 → Peterborough CA in GeoNames)

Sources (public, CC-BY-SA / Wikimedia dumps):
    https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-geo_tags.sql.gz   (~9MB)
    https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-page.sql.gz       (~290MB)
    https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-redirect.sql.gz   (~19MB)

Usage:
    uv run python scripts/build_zh_wiki_geo.py
    uv run python scripts/build_zh_wiki_geo.py --input-dir /path/to/dumps

Output: backend/data/zh_wiki_geo.tsv + backend/data/zh_wiki_geo.sqlite
    TSV:      zh_name <TAB> lat <TAB> lng <TAB> kind(article|redirect) <TAB> page_id
    SQLite:   geo(name TEXT PRIMARY KEY, coord INTEGER) WITHOUT ROWID
              coord packs microdegrees: ((lat+90)*1e6 << 32) | ((lng+180)*1e6)

The resolver queries the SQLite file point-wise (no full-table load), with
priority over the GeoNames zh alias layer. The TSV is kept for human review
and diffing; the SQLite is what ships (gzipped) as a Tauri resource.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import time
import urllib.request
from pathlib import Path

_T2S = None  # opencc.OpenCC("t2s") — created lazily in main() so write_sqlite
             # is reusable without the opencc dependency installed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DUMP_BASE = "https://dumps.wikimedia.org/zhwiki/latest"
FILES = ("geo_tags.sql.gz", "page.sql.gz", "redirect.sql.gz")

DEFAULT_INPUT_DIR = Path.home() / ".ai-reader-v2" / "zhwiki"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent / "backend" / "data" / "zh_wiki_geo.tsv"
)
DEFAULT_SQLITE_OUTPUT = DEFAULT_OUTPUT.with_suffix(".sqlite")


# ── MySQL dump tuple tokenizer ─────────────────────────────

_UNESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "'": "'", '"': '"', "\\": "\\"}


def _unescape(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append(_UNESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def iter_insert_tuples(text: str) -> list[list[str | None]]:
    """Parse a VALUES payload like (...),(...); into raw field lists."""
    idx = text.find("VALUES")
    if idx < 0:
        return []
    payload = text[idx + 6:]
    tuples: list[list[str | None]] = []
    i = 0
    n = len(payload)
    while i < n:
        if payload[i] == "(":
            fields: list[str | None] = []
            buf: list[str] = []
            in_str = False
            field_quoted = False
            i += 1
            while i < n:
                c = payload[i]
                if in_str:
                    if c == "\\" and i + 1 < n:
                        buf.append(c)
                        buf.append(payload[i + 1])
                        i += 2
                        continue
                    if c == "'":
                        in_str = False
                        i += 1
                        continue
                    buf.append(c)
                    i += 1
                    continue
                if c == "'":
                    in_str = True
                    field_quoted = True
                    i += 1
                    continue
                if c == ",":
                    fields.append(_finish_field(buf, field_quoted))
                    buf = []
                    field_quoted = False
                    i += 1
                    continue
                if c == ")":
                    fields.append(_finish_field(buf, field_quoted))
                    tuples.append(fields)
                    i += 1
                    break
                buf.append(c)
                i += 1
        else:
            i += 1
    return tuples


def _finish_field(buf: list[str], quoted: bool) -> str | None:
    raw = "".join(buf).strip()
    if quoted:
        return _unescape(raw)
    if raw.upper() == "NULL" or raw == "":
        return None
    return raw


def iter_sql_rows(path: Path, table: str):
    """Yield field lists from every INSERT for `table` in a .sql.gz dump.

    Wikimedia dumps put a line break right after VALUES — tuples live on the
    following lines until one ends with ';'.
    """
    prefix = f"INSERT INTO `{table}` VALUES"
    in_insert = False
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not in_insert:
                if line.startswith(prefix):
                    in_insert = True
                    if line.rstrip("\n").endswith(";"):
                        # single-line insert (uncommon in these dumps)
                        yield from iter_insert_tuples(line)
                        in_insert = False
                continue
            done = line.rstrip("\n").rstrip().endswith(";")
            yield from iter_insert_tuples("VALUES " + line)
            if done:
                in_insert = False


# ── Step 1: page_id → title (namespace 0 only) ─────────────

def load_pages(path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    count = 0
    for fields in iter_sql_rows(path, "page"):
        count += 1
        # page_id, page_namespace, page_title, ...
        if len(fields) < 3 or fields[1] != "0":
            continue
        try:
            pid = int(fields[0])  # type: ignore[arg-type]
        except (ValueError, TypeError):
            continue
        title = (fields[2] or "").replace("_", " ")
        if title:
            pages[pid] = title
    logger.info("page: %d 行读取, ns=0 页面 %d 个", count, len(pages))
    return pages


# ── Step 2: page_id → (lat, lng) from geo_tags ─────────────

def load_geo_tags(path: Path) -> dict[int, tuple[float, float, bool]]:
    """Return {page_id: (lat, lng, is_primary)}. Prefers primary tag per page."""
    geo: dict[int, tuple[float, float, bool]] = {}
    count = 0
    for fields in iter_sql_rows(path, "geo_tags"):
        count += 1
        # gt_id, gt_page_id, gt_globe, gt_primary, gt_lat, gt_lon, gt_dim, ...
        if len(fields) < 6:
            continue
        if fields[2] not in ("earth", ""):
            continue
        try:
            pid = int(fields[1])  # type: ignore[arg-type]
            lat = float(fields[4])  # type: ignore[arg-type]
            lng = float(fields[5])  # type: ignore[arg-type]
        except (ValueError, TypeError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        primary = fields[3] in ("1", "primary")
        existing = geo.get(pid)
        if existing is None or (primary and not existing[2]):
            geo[pid] = (lat, lng, primary)
    logger.info("geo_tags: %d 行读取, %d 个页面有坐标", count, len(geo))
    return geo


# ── Step 3: redirect title → target title ──────────────────

def load_redirects(
    path: Path, pages: dict[int, str]
) -> list[tuple[str, str]]:
    """Return [(redirect_title, target_title)] for ns=0 redirects."""
    pairs: list[tuple[str, str]] = []
    count = 0
    for fields in iter_sql_rows(path, "redirect"):
        count += 1
        # rd_from, rd_namespace, rd_title, rd_interwiki, rd_fragment
        # (this table has no rd_id)
        if len(fields) < 3 or fields[1] != "0":
            continue
        try:
            from_pid = int(fields[0])  # type: ignore[arg-type]
        except (ValueError, TypeError):
            continue
        from_title = pages.get(from_pid)
        to_title = (fields[2] or "").replace("_", " ")
        if from_title and to_title and from_title != to_title:
            pairs.append((from_title, to_title))
    logger.info("redirect: %d 行读取, ns=0 重定向 %d 条", count, len(pairs))
    return pairs


# ── Download ───────────────────────────────────────────────

def ensure_downloads(input_dir: Path) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = input_dir / f"zhwiki-latest-{name}"
        if dest.exists():
            continue
        url = f"{DUMP_BASE}/zhwiki-latest-{name}"
        logger.info("下载 %s → %s", url, dest)
        urllib.request.urlretrieve(url, dest)


# ── Main ───────────────────────────────────────────────────

def _pack_coord(lat: float, lng: float) -> int:
    """Pack lat/lng into one int64 of microdegrees (mirrors geo_resolver._pack_coord)."""
    return (int((lat + 90.0) * 1_000_000) << 32) | int((lng + 180.0) * 1_000_000)


def write_sqlite(rows: dict[str, tuple[float, float, str, int]], path: Path) -> None:
    """Emit the runtime lookup DB: one row per unique name, table-as-index."""
    import sqlite3

    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute(
        "CREATE TABLE geo (name TEXT PRIMARY KEY, coord INTEGER NOT NULL) WITHOUT ROWID"
    )
    batch: list[tuple[str, int]] = []
    for name in sorted(rows):
        lat, lng, _kind, _pid = rows[name]
        batch.append((name, _pack_coord(lat, lng)))
        if len(batch) >= 50000:
            con.executemany("INSERT INTO geo VALUES (?,?)", batch)
            batch = []
    if batch:
        con.executemany("INSERT INTO geo VALUES (?,?)", batch)
    con.commit()
    con.execute("VACUUM")
    con.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                   help=f"dump 文件目录 (default: {DEFAULT_INPUT_DIR})")
    p.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT,
                   help=f"输出 TSV (default: {DEFAULT_OUTPUT})")
    p.add_argument("--sqlite-path", type=Path, default=DEFAULT_SQLITE_OUTPUT,
                   help=f"输出 SQLite (default: {DEFAULT_SQLITE_OUTPUT})")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    t0 = time.time()

    ensure_downloads(args.input_dir)
    page_path = args.input_dir / "zhwiki-latest-page.sql.gz"
    geo_path = args.input_dir / "zhwiki-latest-geo_tags.sql.gz"
    redir_path = args.input_dir / "zhwiki-latest-redirect.sql.gz"

    logger.info("=== Step 1/4: 解析 page 表 (page_id → title) ===")
    pages = load_pages(page_path)

    logger.info("=== Step 2/4: 解析 geo_tags (page_id → coord) ===")
    geo = load_geo_tags(geo_path)

    logger.info("=== Step 3/4: 解析 redirect (别名 → 目标条目) ===")
    redirects = load_redirects(redir_path, pages)

    logger.info("=== Step 4/4: 合并并写出 ===")
    title_to_pid = {t: pid for pid, t in pages.items()}
    rows: dict[str, tuple[float, float, str, int]] = {}

    for pid, (lat, lng, _primary) in geo.items():
        title = pages.get(pid)
        if not title:
            continue
        rows[title] = (lat, lng, "article", pid)

    redirect_hits = 0
    for from_title, to_title in redirects:
        if from_title in rows:
            continue  # redirect title shadowed by a real article of the same name
        to_pid = title_to_pid.get(to_title)
        if to_pid is None:
            continue
        coord = geo.get(to_pid)
        if coord is None:
            continue
        rows[from_title] = (coord[0], coord[1], "redirect", to_pid)
        redirect_hits += 1

    # Traditional → simplified variants (same rationale as build_zh_geonames.py)
    import opencc

    global _T2S
    _T2S = opencc.OpenCC("t2s")
    added = 0
    for name, (lat, lng, kind, pid) in list(rows.items()):
        simp = _T2S.convert(name)
        if simp != name and simp not in rows:
            rows[simp] = (lat, lng, kind, pid)
            added += 1

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_path, "w", encoding="utf-8") as f:
        for name in sorted(rows):
            lat, lng, kind, pid = rows[name]
            f.write(f"{name}\t{lat:.5f}\t{lng:.5f}\t{kind}\t{pid}\n")

    size_kb = args.output_path.stat().st_size / 1024
    logger.info("=== 完成 (%.1f 秒) ===", time.time() - t0)
    logger.info("  条目坐标: %d, 重定向命中: %d, 繁→简新增: %d",
                len(geo), redirect_hits, added)
    logger.info("  输出: %s (%.1f KB, %d 行)", args.output_path, size_kb, len(rows))

    write_sqlite(rows, args.sqlite_path)
    logger.info("  SQLite: %s (%.1f MB)",
                args.sqlite_path, args.sqlite_path.stat().st_size / 1e6)

    # Conflict report vs the GeoNames zh alias table: same-name entries whose
    # coordinates disagree by >150km. The wiki layer wins at resolve time;
    # this list is for human review of crowd-alias errors.
    geonames_tsv = args.output_path.parent / "zh_geonames.tsv"
    if geonames_tsv.exists():
        import math

        def _hav(a: tuple[float, float], b: tuple[float, float]) -> float:
            r, p1, p2 = 6371.0, math.radians(a[0]), math.radians(b[0])
            dp, dl = p2 - p1, math.radians(b[1] - a[1])
            h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
            return 2 * r * math.asin(math.sqrt(h))

        gn: dict[str, tuple[float, float]] = {}
        with open(geonames_tsv, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 3:
                    continue
                try:
                    gn.setdefault(parts[0], (float(parts[1]), float(parts[2])))
                except ValueError:
                    continue
        conflicts = []
        for name, (lat, lng, _k, _p) in rows.items():
            g = gn.get(name)
            if g is None:
                continue
            d = _hav((lat, lng), g)
            if d > 150:
                conflicts.append((d, name, (lat, lng), g))
        conflicts.sort(reverse=True)
        logger.info("  与 zh_geonames 同名冲突 (>150km): %d 条, Top 20:", len(conflicts))
        for d, name, w, g in conflicts[:20]:
            logger.info("    %s  wiki=(%.2f,%.2f) geonames=(%.2f,%.2f) 相差 %.0fkm",
                        name, w[0], w[1], g[0], g[1], d)


if __name__ == "__main__":
    main()
