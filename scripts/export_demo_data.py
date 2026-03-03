#!/usr/bin/env python3
"""
Export demo data from a novel's analysis results via the API.

Usage:
    # Start the backend server first, then:
    python scripts/export_demo_data.py --novel-id <ID> --output-dir demo/hongloumeng/data

    # List available novels:
    python scripts/export_demo_data.py --list

    # Export all analyzed novels (auto-named directories):
    python scripts/export_demo_data.py --all --output-dir demo/

    # Export with custom base URL:
    python scripts/export_demo_data.py --novel-id <ID> --base-url http://localhost:8000

    # Export without gzip compression:
    python scripts/export_demo_data.py --novel-id <ID> --no-compress
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://localhost:8000"


def api_get(base_url: str, path: str) -> dict | list | None:
    """GET request to API, returns parsed JSON or None on error."""
    url = f"{base_url}{path}"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"  HTTP {e.code} for {path}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"  Connection error for {path}: {e.reason}", file=sys.stderr)
        return None


def _get_novels(base_url: str) -> list[dict]:
    """Fetch novels list from API. Returns list of novel dicts."""
    data = api_get(base_url, "/api/novels")
    if not data:
        print("Failed to fetch novels. Is the backend running?", file=sys.stderr)
        sys.exit(1)
    # API wraps novels in {"novels": [...]}
    if isinstance(data, dict) and "novels" in data:
        return data["novels"]
    if isinstance(data, list):
        return data
    return []


def list_novels(base_url: str) -> None:
    """List all novels in the system."""
    novels = _get_novels(base_url)
    print(f"{'ID':<40} {'Title':<30} {'Chapters':<10} {'Progress'}")
    print("-" * 90)
    for novel in novels:
        progress = novel.get("analysis_progress", 0) or 0
        status = f"{progress:.0%}" if progress > 0 else "pending"
        print(
            f"{novel['id']:<40} {novel['title']:<30} "
            f"{novel.get('total_chapters', '?'):<10} {status}"
        )


def strip_redundant_fields(data: dict | list, fields_to_remove: set[str]) -> None:
    """Recursively remove specified fields to reduce JSON size."""
    if isinstance(data, dict):
        for key in list(data.keys()):
            if key in fields_to_remove:
                del data[key]
            else:
                strip_redundant_fields(data[key], fields_to_remove)
    elif isinstance(data, list):
        for item in data:
            strip_redundant_fields(item, fields_to_remove)


def save_json(data: dict | list, output_path: Path, compress: bool = True) -> int:
    """Save data as JSON, optionally gzip-compressed. Returns file size in bytes."""
    json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    if compress:
        gz_path = output_path.with_suffix(output_path.suffix + ".gz")
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write(json_str)
        size = gz_path.stat().st_size
        print(f"  -> {gz_path.name} ({size / 1024:.1f} KB)")
        return size
    else:
        output_path.write_text(json_str, encoding="utf-8")
        size = output_path.stat().st_size
        print(f"  -> {output_path.name} ({size / 1024:.1f} KB)")
        return size


# Fields that add bulk without demo value
STRIP_FIELDS = {
    "embedding",
    "embedding_model",
    "fact_json",  # Raw LLM output, very large
    "narrative_evidence",  # Spatial constraint evidence text
    "sample_context",  # Entity dictionary sample context
}


def _count_items(data: dict | list | None, key: str) -> int:
    """Count items in a list field of a dict, or length of a list."""
    if data is None:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        val = data.get(key, [])
        return len(val) if isinstance(val, list) else 0
    return 0


def _sanitize_dirname(title: str) -> str:
    """Convert novel title to safe directory name."""
    # Remove characters unsafe for filesystem
    safe = re.sub(r'[<>:"/\\|?*]', "", title)
    return safe.strip() or "unknown"


def export_demo(
    base_url: str, novel_id: str, output_dir: Path, compress: bool
) -> None:
    """Export all visualization endpoints for a novel."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Verify novel exists
    novel = api_get(base_url, f"/api/novels/{novel_id}")
    if not novel:
        print(f"Novel {novel_id} not found.", file=sys.stderr)
        sys.exit(1)
    title = novel.get("title", novel_id)
    print(f"📚 Exporting: {title}")

    total_size = 0
    stats: dict[str, dict] = {}

    # Save novel metadata
    size = save_json(novel, output_dir / "novel.json", compress=compress)
    total_size += size

    # Visualization endpoints
    endpoints = [
        ("graph", f"/api/novels/{novel_id}/graph"),
        ("map", f"/api/novels/{novel_id}/map"),
        ("timeline", f"/api/novels/{novel_id}/timeline"),
        ("encyclopedia", f"/api/novels/{novel_id}/encyclopedia/entries"),
        ("factions", f"/api/novels/{novel_id}/factions"),
        ("world-structure", f"/api/novels/{novel_id}/world-structure"),
        ("encyclopedia-stats", f"/api/novels/{novel_id}/encyclopedia"),
    ]

    for name, path in endpoints:
        print(f"  Fetching {name}...")
        data = api_get(base_url, path)
        if data is not None:
            strip_redundant_fields(data, STRIP_FIELDS)

            # Collect stats before saving
            if name == "graph" and isinstance(data, dict):
                stats["graph"] = {
                    "nodes": _count_items(data, "nodes"),
                    "edges": _count_items(data, "edges"),
                }
            elif name == "map" and isinstance(data, dict):
                stats["map"] = {
                    "locations": _count_items(data, "locations"),
                    "trajectories": len(data.get("trajectories", {})),
                }
            elif name == "timeline" and isinstance(data, dict):
                stats["timeline"] = {
                    "events": _count_items(data, "events"),
                    "swimlanes": len(data.get("swimlanes", {})),
                }
            elif name == "encyclopedia" and isinstance(data, dict):
                stats["encyclopedia"] = {
                    "entries": _count_items(data, "entries"),
                }
            elif name == "factions" and isinstance(data, dict):
                stats["factions"] = {
                    "orgs": _count_items(data, "orgs"),
                    "members": sum(
                        len(v) for v in data.get("members", {}).values()
                    ),
                }
            elif name == "encyclopedia-stats" and isinstance(data, dict):
                stats["encyclopedia-stats"] = {
                    "total": data.get("total", 0),
                    "person": data.get("person", 0),
                    "location": data.get("location", 0),
                    "item": data.get("item", 0),
                    "org": data.get("org", 0),
                    "concept": data.get("concept", 0),
                }

            size = save_json(data, output_dir / f"{name}.json", compress=compress)
            total_size += size
        else:
            print(f"  ⚠️ Skipped {name} (no data)")

    # Export chapters list (for chapter navigation)
    print("  Fetching chapters...")
    chapters_data = api_get(base_url, f"/api/novels/{novel_id}/chapters")
    if chapters_data:
        # API may wrap in {"chapters": [...]}
        chapters_list = (
            chapters_data.get("chapters", chapters_data)
            if isinstance(chapters_data, dict)
            else chapters_data
        )
        # Keep only essential chapter metadata, not full text
        slim_chapters = [
            {
                "chapter_num": ch.get("chapter_num"),
                "title": ch.get("title"),
                "word_count": ch.get("word_count"),
                "analysis_status": ch.get("analysis_status"),
            }
            for ch in chapters_list
            if isinstance(ch, dict)
        ]
        size = save_json(slim_chapters, output_dir / "chapters.json", compress=compress)
        total_size += size
        stats["chapters"] = {"count": len(slim_chapters)}

    # === Statistics Report ===
    print(f"\n{'=' * 50}")
    print(f"📊 导出统计 — {title}")
    print(f"{'=' * 50}")
    if stats.get("graph"):
        print(f"  关系图: {stats['graph']['nodes']} 人物, {stats['graph']['edges']} 关系")
    if stats.get("map"):
        print(f"  地  图: {stats['map']['locations']} 地点, {stats['map']['trajectories']} 轨迹")
    if stats.get("timeline"):
        print(f"  时间线: {stats['timeline']['events']} 事件, {stats['timeline']['swimlanes']} 泳道")
    if stats.get("encyclopedia"):
        print(f"  百  科: {stats['encyclopedia']['entries']} 词条")
    if stats.get("factions"):
        print(f"  阵  营: {stats['factions']['orgs']} 组织, {stats['factions']['members']} 成员")
    if stats.get("encyclopedia-stats"):
        es = stats["encyclopedia-stats"]
        print(
            f"  分类统计: 人物 {es['person']} / 地点 {es['location']} / "
            f"物品 {es['item']} / 组织 {es['org']} / 概念 {es['concept']} = 共 {es['total']}"
        )
    if stats.get("chapters"):
        print(f"  章  节: {stats['chapters']['count']} 章")

    total_mb = total_size / (1024 * 1024)
    print(f"\n  📦 总数据量: {total_mb:.2f} MB")
    if total_mb > 5:
        print("  ⚠️ 警告: 超过 5MB 目标限制！")
    else:
        print("  ✅ 在 5MB 限制内")
    print(f"\n✅ Demo 数据已导出到: {output_dir}")


def export_all(base_url: str, output_dir: Path, compress: bool) -> None:
    """Export all analyzed novels, each in its own subdirectory."""
    novels = _get_novels(base_url)

    analyzed = [n for n in novels if (n.get("analysis_progress", 0) or 0) > 0]
    if not analyzed:
        print("No analyzed novels found.", file=sys.stderr)
        sys.exit(1)

    print(f"📚 Found {len(analyzed)} analyzed novel(s)\n")
    for novel in analyzed:
        novel_id = novel["id"]
        title = novel.get("title", novel_id)
        dirname = _sanitize_dirname(title)
        novel_output = output_dir / dirname / "data"
        print(f"\n{'─' * 50}")
        export_demo(base_url, novel_id, novel_output, compress)

    print(f"\n{'═' * 50}")
    print(f"🎉 All {len(analyzed)} novel(s) exported to: {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export novel demo data via API")
    parser.add_argument("--novel-id", help="Novel UUID to export")
    parser.add_argument(
        "--output-dir",
        default="demo/data",
        help="Output directory (default: demo/data)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--list", action="store_true", help="List available novels")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Export all analyzed novels (auto-named subdirectories)",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Save as plain JSON instead of gzip (default: gzip compressed)",
    )
    args = parser.parse_args()

    if args.list:
        list_novels(args.base_url)
        return

    if args.all:
        export_all(args.base_url, Path(args.output_dir), compress=not args.no_compress)
        return

    if not args.novel_id:
        parser.error(
            "--novel-id is required (use --list to see available novels, "
            "or --all to export all)"
        )

    export_demo(
        base_url=args.base_url,
        novel_id=args.novel_id,
        output_dir=Path(args.output_dir),
        compress=not args.no_compress,
    )


if __name__ == "__main__":
    main()
