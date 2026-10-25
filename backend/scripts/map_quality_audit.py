#!/usr/bin/env python3
"""Audit map layout decisions across a local AI Reader library.

For each analysed novel this asks `get_map_data` for the automatic layout and
prints why the result is geographic (real basemap) or layered/constraint
(fantasy layers, low geo coverage, missing geo data, …).

The call goes through the normal map path, so it may write regenerated layout
caches. Run it against a copy of `data.db` when auditing a production library:

    cp ~/.ai-reader-v2/data.db /tmp/airv2-audit.db
    AI_READER_DATA_DIR=/tmp/airv2-audit \
      backend/.venv/bin/python backend/scripts/map_quality_audit.py --all

Examples:
    ... map_quality_audit.py --novel 海底
    ... map_quality_audit.py --all --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--novel", help="Title substring")
    group.add_argument("--all", action="store_true", help="Audit every analysed novel")
    parser.add_argument(
        "--data-dir",
        help="AI_READER_DATA_DIR to use (defaults to the environment/app default)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include novels without chapter facts",
    )
    return parser.parse_args()


async def _audit(args: argparse.Namespace) -> list[dict]:
    from src.infra.config import DB_PATH
    from src.services import visualization_service

    if not DB_PATH.exists():
        raise SystemExit(f"database not found: {DB_PATH}")

    con = sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT n.id, n.title, n.total_chapters,
                   (SELECT COUNT(*) FROM chapter_facts f WHERE f.novel_id = n.id) AS facts
            FROM novels n
            ORDER BY n.title
            """
        ).fetchall()
    finally:
        con.close()

    out: list[dict] = []
    for row in rows:
        if not args.all and args.novel.lower() not in (row["title"] or "").lower():
            continue
        if row["facts"] == 0 and not args.include_empty:
            continue
        end = max(int(row["total_chapters"] or 1), 1)
        visualization_service._map_cache.clear()
        try:
            data = await visualization_service.get_map_data(row["id"], 1, end)
            coverage = data.get("geo_coverage") or {}
            item = {
                "title": row["title"],
                "chapters": end,
                "facts": row["facts"],
                "layout_mode": data.get("layout_mode"),
                "layout_reason": data.get("layout_reason"),
                "geo_resolved": coverage.get("resolved", len(data.get("geo_coords") or {})),
                "geo_total": coverage.get("total", 0),
                "geo_ratio": coverage.get("ratio"),
                "locations": len(data.get("locations") or []),
            }
        except Exception as exc:  # a broken novel must not abort the audit
            item = {
                "title": row["title"],
                "chapters": end,
                "facts": row["facts"],
                "layout_mode": "ERROR",
                "layout_reason": f"{type(exc).__name__}: {exc}",
                "geo_resolved": 0,
                "geo_total": 0,
                "geo_ratio": None,
                "locations": 0,
            }
        out.append(item)
        if not args.json:
            ratio = item["geo_ratio"]
            ratio_s = f"{ratio * 100:5.1f}%" if ratio is not None else "    -"
            print(
                f"{(item['title'] or '')[:30]:32} "
                f"ch={item['chapters']:4} "
                f"mode={item['layout_mode']!s:10} "
                f"reason={item['layout_reason']!s:16} "
                f"geo={item['geo_resolved']:4}/{item['geo_total']:<4} "
                f"ratio={ratio_s}"
            )
    return out


def main() -> None:
    args = _parse_args()
    if args.data_dir:
        os.environ["AI_READER_DATA_DIR"] = args.data_dir
    items = asyncio.run(_audit(args))
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        reasons = Counter(str(i["layout_reason"]) for i in items)
        modes = Counter(str(i["layout_mode"]) for i in items)
        print("\nlayout modes:", dict(modes))
        print("layout reasons:", dict(reasons))
        print(f"total: {len(items)}")


if __name__ == "__main__":
    main()
