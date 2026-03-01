#!/usr/bin/env python3
"""
Export demo data from a novel's analysis results via the API.

Usage:
    # Start the backend server first, then:
    python scripts/export_demo_data.py --novel-id <ID> --output-dir demo/hongloumeng/data

    # List available novels:
    python scripts/export_demo_data.py --list

    # Export with custom base URL:
    python scripts/export_demo_data.py --novel-id <ID> --base-url http://localhost:8000
"""

import argparse
import gzip
import json
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


def list_novels(base_url: str) -> None:
    """List all novels in the system."""
    data = api_get(base_url, "/api/novels")
    if not data:
        print("Failed to fetch novels. Is the backend running?", file=sys.stderr)
        sys.exit(1)
    print(f"{'ID':<40} {'Title':<30} {'Chapters':<10} {'Status'}")
    print("-" * 90)
    for novel in data:
        status = "analyzed" if novel.get("analyzed_chapters", 0) > 0 else "pending"
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


def save_json(data: dict | list, output_path: Path, compress: bool = True) -> None:
    """Save data as JSON, optionally gzip-compressed."""
    json_str = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    if compress:
        gz_path = output_path.with_suffix(output_path.suffix + ".gz")
        with gzip.open(gz_path, "wt", encoding="utf-8") as f:
            f.write(json_str)
        size_kb = gz_path.stat().st_size / 1024
        print(f"  -> {gz_path.name} ({size_kb:.1f} KB)")
    else:
        output_path.write_text(json_str, encoding="utf-8")
        size_kb = output_path.stat().st_size / 1024
        print(f"  -> {output_path.name} ({size_kb:.1f} KB)")


# Fields that add bulk without demo value
STRIP_FIELDS = {
    "embedding",
    "embedding_model",
    "fact_json",  # Raw LLM output, very large
}


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
    print(f"Exporting: {novel.get('title', novel_id)}")

    # Save novel metadata
    save_json(novel, output_dir / "novel.json", compress=compress)

    # Endpoints to export
    endpoints = [
        ("graph", f"/api/novels/{novel_id}/graph"),
        ("map", f"/api/novels/{novel_id}/map"),
        ("timeline", f"/api/novels/{novel_id}/timeline"),
        ("encyclopedia", f"/api/novels/{novel_id}/encyclopedia/entries"),
        ("factions", f"/api/novels/{novel_id}/factions"),
    ]

    for name, path in endpoints:
        print(f"  Fetching {name}...")
        data = api_get(base_url, path)
        if data is not None:
            strip_redundant_fields(data, STRIP_FIELDS)
            save_json(data, output_dir / f"{name}.json", compress=compress)
        else:
            print(f"  Skipped {name} (no data)")

    # Export chapters list (for chapter navigation)
    print("  Fetching chapters...")
    chapters = api_get(base_url, f"/api/novels/{novel_id}/chapters")
    if chapters:
        # Keep only essential chapter metadata, not full text
        slim_chapters = [
            {
                "chapter_num": ch.get("chapter_num"),
                "title": ch.get("title"),
                "word_count": ch.get("word_count"),
                "has_facts": ch.get("has_facts", False),
            }
            for ch in chapters
        ]
        save_json(slim_chapters, output_dir / "chapters.json", compress=compress)

    print(f"\nDone! Demo data exported to: {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export novel demo data via API")
    parser.add_argument("--novel-id", help="Novel UUID to export")
    parser.add_argument(
        "--output-dir",
        default="demo/hongloumeng/data",
        help="Output directory (default: demo/hongloumeng/data)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--list", action="store_true", help="List available novels")
    parser.add_argument(
        "--no-compress", action="store_true", help="Save as plain JSON (no gzip)"
    )
    args = parser.parse_args()

    if args.list:
        list_novels(args.base_url)
        return

    if not args.novel_id:
        parser.error("--novel-id is required (use --list to see available novels)")

    export_demo(
        base_url=args.base_url,
        novel_id=args.novel_id,
        output_dir=Path(args.output_dir),
        compress=not args.no_compress,
    )


if __name__ == "__main__":
    main()
