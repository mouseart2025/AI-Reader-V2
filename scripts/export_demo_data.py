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

    # Export only chapter text + entities (skip visualization endpoints):
    python scripts/export_demo_data.py --novel-id <ID> --text-only

    # Export without chapter text (old behavior):
    python scripts/export_demo_data.py --novel-id <ID> --no-text
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = os.environ.get("AI_READER_API_BASE", "http://localhost:8000")


# Accumulated request failures for the current novel. Without this, a failed
# endpoint only printed a single "⚠️ Skipped X" line and the script still ended
# with "✅ Demo 数据已导出" and exit code 0 — a silently truncated export looked
# identical to a complete one. Cleared at the start of each export_demo().
_failures: list[str] = []


def api_get(base_url: str, path: str) -> dict | list | None:
    """GET request to API, returns parsed JSON or None on error."""
    url = f"{base_url}{path}"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"  HTTP {e.code} for {path}", file=sys.stderr)
        _failures.append(f"HTTP {e.code} {path}")
        return None
    except URLError as e:
        print(f"  Connection error for {path}: {e.reason}", file=sys.stderr)
        _failures.append(f"CONN {path} ({e.reason})")
        return None


def _report_failures(count: int) -> None:
    """Print a consolidated failure report for the novel just exported."""
    if not count:
        return
    print(f"\n❌ {count} 个请求失败,导出不完整:", file=sys.stderr)
    for item in _failures[:20]:
        print(f"   - {item}", file=sys.stderr)
    if count > 20:
        print(f"   ... 另有 {count - 20} 条", file=sys.stderr)


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


def export_chapter_texts(
    base_url: str, novel_id: str, output_dir: Path, compress: bool
) -> tuple[int, int]:
    """Export individual chapter content + entities as separate .json.gz files.

    Returns (total_size_bytes, chapter_count).
    """
    chapters_dir = output_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    # Get chapter list
    chapters_data = api_get(base_url, f"/api/novels/{novel_id}/chapters")
    if not chapters_data:
        print("  ⚠️ Failed to fetch chapters list", file=sys.stderr)
        return 0, 0

    chapters_list = (
        chapters_data.get("chapters", chapters_data)
        if isinstance(chapters_data, dict)
        else chapters_data
    )

    total_size = 0
    exported = 0
    total = len(chapters_list)

    for ch in chapters_list:
        if not isinstance(ch, dict):
            continue
        num = ch.get("chapter_num")
        if num is None:
            continue

        # Fetch chapter content
        content_data = api_get(
            base_url, f"/api/novels/{novel_id}/chapters/{num}"
        )
        if not content_data or not isinstance(content_data, dict):
            print(f"  ⚠️ Skipped chapter {num} (no content)")
            continue

        # Fetch chapter entities — API returns {"entities": [...]}
        entities_data = api_get(
            base_url, f"/api/novels/{novel_id}/chapters/{num}/entities"
        )
        if isinstance(entities_data, dict) and "entities" in entities_data:
            entities = entities_data["entities"]
        elif isinstance(entities_data, list):
            entities = entities_data
        else:
            entities = []

        # Fetch chapter scenes — API returns {"scenes": [...], "scene_count": N}
        scenes_data = api_get(
            base_url, f"/api/novels/{novel_id}/scenes/{num}"
        )
        if isinstance(scenes_data, dict) and "scenes" in scenes_data:
            scenes = scenes_data["scenes"]
        else:
            scenes = []

        # Build slim chapter record
        chapter_record = {
            "chapter_num": num,
            "title": content_data.get("title", ch.get("title", "")),
            "content": content_data.get("content", ""),
            "word_count": content_data.get("word_count", 0),
            "entities": entities,
            "scenes": scenes,
        }

        filename = f"ch-{num:03d}.json"
        size = save_json(chapter_record, chapters_dir / filename, compress=compress)
        total_size += size
        exported += 1

        # Progress indicator (don't spam — every 10 chapters)
        if exported % 10 == 0 or exported == total:
            print(f"  📖 章节文本: {exported}/{total}")

    return total_size, exported


def _fetch_entity_profile(
    base_url: str, novel_id: str, name: str, entity_type: str,
) -> dict | None:
    """Fetch a single entity's full aggregated profile."""
    encoded = quote(name, safe="")
    return api_get(
        base_url,
        f"/api/novels/{novel_id}/entities/{encoded}?type={entity_type}",
    )


_FS_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _entity_filename_stem(name: str) -> str:
    """Filesystem-safe + HTTP-safe stem for an entity filename.

    Stored as the raw Chinese name so Cloudflare's URL decoder finds the file
    when the browser sends ``%E8%B4%BE%E9%9B%A8%E6%9D%91``. We only sanitize
    characters that break filesystems (slashes, control chars, etc.).
    """
    return _FS_UNSAFE.sub("_", name).strip() or "_"


def export_entity_profiles(
    base_url: str, novel_id: str, output_dir: Path, compress: bool,
    max_workers: int = 8,
) -> tuple[int, int]:
    """Export per-entity full aggregated profiles to entities/<type>/<name>.json[.gz].

    Filenames keep the raw Chinese name (filesystem-unsafe chars sanitized) so
    that browser-issued ``encodeURIComponent`` requests round-trip correctly
    through Cloudflare Pages. Concepts are skipped — backend has no concept
    profile aggregation.
    """
    print("  🃏 Fetching entity list...")
    entities_data = api_get(base_url, f"/api/novels/{novel_id}/entities")
    if not entities_data or not isinstance(entities_data, dict):
        print("  ⚠️ Skipped entity profiles (no data)")
        return 0, 0

    entities = entities_data.get("entities", [])
    profile_types = {"person", "location", "item", "org"}
    targets = [e for e in entities if isinstance(e, dict) and e.get("type") in profile_types]
    if not targets:
        print("  ⚠️ Skipped entity profiles (no person/location/item/org)")
        return 0, 0

    entities_dir = output_dir / "entities"
    entities_dir.mkdir(exist_ok=True)
    for t in profile_types:
        (entities_dir / t).mkdir(exist_ok=True)

    total = len(targets)
    print(f"  🃏 Exporting {total} entity profiles ({max_workers} workers)...")

    total_size = 0
    success = 0
    fails = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_entity = {
            ex.submit(
                _fetch_entity_profile, base_url, novel_id, e["name"], e["type"]
            ): e
            for e in targets
        }
        for fut in as_completed(future_to_entity):
            e = future_to_entity[fut]
            name = e.get("name", "?")
            etype = e.get("type", "?")
            try:
                profile = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  ⚠️ {etype}/{name}: {exc}")
                fails += 1
                continue
            if not profile:
                fails += 1
                continue
            strip_redundant_fields(profile, STRIP_FIELDS)
            safe_name = _entity_filename_stem(name)
            target_path = entities_dir / etype / f"{safe_name}.json"
            # Inline save (avoid noisy per-file print from save_json)
            json_str = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
            if compress:
                gz_path = target_path.with_suffix(target_path.suffix + ".gz")
                with gzip.open(gz_path, "wt", encoding="utf-8") as f:
                    f.write(json_str)
                total_size += gz_path.stat().st_size
            else:
                target_path.write_text(json_str, encoding="utf-8")
                total_size += target_path.stat().st_size
            success += 1
            if success % 100 == 0 or success == total:
                print(f"  🃏 entity profiles: {success}/{total}")

    if fails:
        print(f"  ⚠️ {fails} entity profile(s) failed/empty")
    return total_size, success


def export_demo(
    base_url: str, novel_id: str, output_dir: Path, compress: bool,
    include_text: bool = True, text_only: bool = False,
) -> int:
    """Export all visualization endpoints for a novel.

    Returns the number of failed requests (0 == clean export).
    """
    _failures.clear()
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

    # Text-only mode: just export chapter texts and exit
    if text_only:
        print("  📖 Text-only mode: exporting chapter texts...")
        text_size, text_count = export_chapter_texts(
            base_url, novel_id, output_dir, compress
        )
        total_size += text_size
        stats["chapter_texts"] = {"count": text_count}
        total_mb = total_size / (1024 * 1024)
        print(f"\n  📦 章节文本总量: {total_mb:.2f} MB ({text_count} 章)")
        if _failures:
            _report_failures(len(_failures))
            print(f"\n❌ 章节文本导出不完整: {output_dir / 'chapters'}")
        else:
            print(f"\n✅ 章节文本已导出到: {output_dir / 'chapters'}")
        return len(_failures)

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
                # Export per-layer map data so demo layer-tab switches have
                # something to render. Without this, switching to "天界" / "副本/秘境"
                # etc. shows an empty map (the default response is overworld-only).
                ws = data.get("world_structure") or {}
                layers = ws.get("layers") or []
                extra_layers = [
                    layer for layer in layers
                    if layer.get("layer_id")
                    and layer.get("layer_id") != "overworld"
                    and (layer.get("location_count") or 0) > 0
                ]
                stats["map"]["layers"] = len(extra_layers)
                for layer in extra_layers:
                    layer_id = layer["layer_id"]
                    print(f"  Fetching map layer={layer_id}...")
                    layer_data = api_get(
                        base_url,
                        f"/api/novels/{novel_id}/map?layer_id={layer_id}",
                    )
                    if layer_data is None:
                        print(f"  ⚠️ Skipped map-{layer_id} (no data)")
                        continue
                    strip_redundant_fields(layer_data, STRIP_FIELDS)
                    layer_size = save_json(
                        layer_data,
                        output_dir / f"map-{layer_id}.json",
                        compress=compress,
                    )
                    total_size += layer_size
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

    # Export chapter text + entities (individual files)
    if include_text:
        print("  📖 Exporting chapter texts + entities...")
        text_size, text_count = export_chapter_texts(
            base_url, novel_id, output_dir, compress
        )
        total_size += text_size
        stats["chapter_texts"] = {"count": text_count, "size_kb": text_size / 1024}

    # Export per-entity full aggregated profiles (drives the rich
    # encyclopedia card UI in the demo, replacing the simplified
    # graph+encyclopedia-derived placeholder profiles).
    profile_size, profile_count = export_entity_profiles(
        base_url, novel_id, output_dir, compress
    )
    total_size += profile_size
    stats["entity_profiles"] = {
        "count": profile_count,
        "size_kb": profile_size / 1024,
    }

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
    if stats.get("chapter_texts"):
        ct = stats["chapter_texts"]
        print(f"  原  文: {ct['count']} 章 ({ct['size_kb']:.0f} KB)")
    if stats.get("entity_profiles"):
        ep = stats["entity_profiles"]
        print(f"  实体卡: {ep['count']} 份 ({ep['size_kb']:.0f} KB)")

    total_mb = total_size / (1024 * 1024)
    print(f"\n  📦 总数据量: {total_mb:.2f} MB")
    if total_mb > 5:
        # 实测 demo 五本都在 6-12MB(加了章节原文 + 实体卡之后),5MB 早已不是
        # 可达目标,这里只提示不作为失败条件 —— 见 --all 的 volume 汇总。
        print("  ⚠️ 超过 5MB 参考值(五本实测 6-12MB,非失败)")
    else:
        print("  ✅ 在 5MB 参考值内")

    failed = len(_failures)
    _report_failures(failed)
    if failed:
        print(f"\n❌ Demo 数据导出不完整: {output_dir} ({failed} 个请求失败)")
    else:
        print(f"\n✅ Demo 数据已导出到: {output_dir}")
    return failed


def _read_exported_count(slug_dir: Path, stem: str, key: str) -> int:
    """Read an item count back out of an already-exported (gzipped) JSON file."""
    for cand in (slug_dir / f"{stem}.json.gz", slug_dir / f"{stem}.json"):
        if not cand.exists():
            continue
        try:
            if cand.suffix == ".gz":
                with gzip.open(cand, "rt", encoding="utf-8") as f:
                    data = json.loads(f.read())
            else:
                data = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        return _count_items(data, key)
    return 0


def _fetch_novel_stats(
    base_url: str, novel_id: str, slug_dir: Path | None = None,
) -> dict:
    """Fetch stats for manifest generation.

    ``relation_count`` / ``event_count`` do NOT exist in the encyclopedia stats
    response (``encyclopedia_service.get_category_stats`` only returns category
    buckets), so reading them via ``data.get(...)`` silently produced 0 forever.
    They are read back from the exported graph/timeline instead — zero extra
    requests, and guaranteed consistent with what actually landed on disk.
    """
    data = api_get(base_url, f"/api/novels/{novel_id}/encyclopedia")
    if not data or not isinstance(data, dict):
        return {}
    stats = {
        "characters": data.get("person", 0),
        "locations": data.get("location", 0),
        "relations": 0,
        "events": 0,
    }
    if slug_dir:
        stats["relations"] = _read_exported_count(slug_dir, "graph", "edges")
        stats["events"] = _read_exported_count(slug_dir, "timeline", "events")
    return stats


def generate_manifest(
    base_url: str, output_dir: Path, novel_entries: list[dict],
) -> None:
    """Generate manifest.json listing all exported novels."""
    from datetime import date
    novels = []
    for entry in novel_entries:
        novel_id = entry["id"]
        slug = entry.get("slug", _sanitize_dirname(entry.get("title", novel_id)))
        stats = _fetch_novel_stats(base_url, novel_id, output_dir / slug)
        novels.append({
            "slug": slug,
            "title": entry.get("title", ""),
            "author": entry.get("author"),
            "totalChapters": entry.get("total_chapters", 0),
            "stats": stats,
        })

    manifest = {
        "version": 1,
        "generated": date.today().isoformat(),
        "novels": novels,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n📋 manifest.json generated: {manifest_path}")


def export_all(
    base_url: str, output_dir: Path, compress: bool,
    include_text: bool = True, text_only: bool = False,
    include_manifest: bool = False,
    novel_ids: str | None = None, slugs: str | None = None,
) -> int:
    """Export multiple novels, each in its own subdirectory.

    With ``novel_ids`` (comma-separated) only those novels are exported, in the
    given order. Without it, every novel with ``analysis_progress > 0`` is
    exported — in a populated DB that is 60+ titles (including duplicate
    imports of the same book), so it warns loudly before proceeding.

    Returns the total number of failed requests across all novels.
    """
    novels = _get_novels(base_url)

    if novel_ids:
        wanted = [s.strip() for s in novel_ids.split(",") if s.strip()]
        selected = [n for n in novels if n["id"] in set(wanted)]
        missing = set(wanted) - {n["id"] for n in selected}
        if missing:
            print(f"❌ 未找到小说 ID: {', '.join(sorted(missing))}", file=sys.stderr)
            sys.exit(1)
        order = {nid: i for i, nid in enumerate(wanted)}
        selected.sort(key=lambda n: order[n["id"]])
    else:
        selected = [n for n in novels if (n.get("analysis_progress", 0) or 0) > 0]
        print(
            f"⚠️ --all 模式:DB 共 {len(novels)} 本,其中 {len(selected)} 本已分析,"
            f"将全部导出。\n   若只要 demo 五本,改用 "
            f"--novel-ids <id1,id2,...> --slugs <slug1,slug2,...>",
            file=sys.stderr,
        )

    if not selected:
        print("No matching novels found.", file=sys.stderr)
        sys.exit(1)

    slug_list = [s.strip() for s in slugs.split(",")] if slugs else []
    resolved: list[tuple[dict, str]] = []
    for i, novel in enumerate(selected):
        slug = (
            slug_list[i]
            if i < len(slug_list)
            else _sanitize_dirname(novel.get("title", novel["id"]))
        )
        resolved.append((novel, slug))

    print(f"📚 将导出 {len(resolved)} 本:")
    for novel, slug in resolved:
        print(f"   - {slug:<12} ← {novel.get('title', '?')} ({novel['id']})")

    total_fail = 0
    for i, (novel, slug) in enumerate(resolved, start=1):
        print(f"\n{'─' * 50}")
        print(f"[{i}/{len(resolved)}] {slug}")
        total_fail += export_demo(
            base_url, novel["id"], output_dir / slug, compress,
            include_text=include_text, text_only=text_only,
        )
        novel["slug"] = slug  # consumed by generate_manifest

    if include_manifest:
        generate_manifest(base_url, output_dir, [n for n, _ in resolved])

    print(f"\n{'═' * 50}")
    if total_fail:
        print(
            f"❌ {len(resolved)} 本导出结束,但共 {total_fail} 个请求失败",
            file=sys.stderr,
        )
    else:
        print(f"🎉 All {len(resolved)} novel(s) exported to: {output_dir}")
    return total_fail


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
        help="Export all analyzed novels (auto-named subdirectories). "
             "WARNING: exports every analyzed novel in the DB, not just demos.",
    )
    parser.add_argument(
        "--novel-ids",
        help="Comma-separated novel UUIDs to export (explicit whitelist, "
             "recommended over --all). Order is preserved.",
    )
    parser.add_argument(
        "--slugs",
        help="Comma-separated directory names, 1:1 positional match with --novel-ids",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Save as plain JSON instead of gzip (default: gzip compressed)",
    )
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="Skip chapter text export (old behavior, metadata only)",
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Export only chapter texts + entities (skip visualization endpoints)",
    )
    parser.add_argument(
        "--slug",
        help="Directory name for the novel (e.g., 'honglou'). Used as subdirectory under output-dir.",
    )
    parser.add_argument(
        "--include-manifest",
        action="store_true",
        help="Generate manifest.json listing all novels in output-dir (for desktop app)",
    )
    args = parser.parse_args()

    if args.list:
        list_novels(args.base_url)
        return

    compress = not args.no_compress
    include_text = not args.no_text
    text_only = args.text_only

    if args.all or args.novel_ids:
        if args.novel_ids and not args.slugs:
            print(
                "⚠️ 未提供 --slugs,目录名将用小说标题(中文),与 demo 现有 "
                "英文 slug 不一致。",
                file=sys.stderr,
            )
        failures = export_all(
            args.base_url, Path(args.output_dir), compress=compress,
            include_text=include_text, text_only=text_only,
            include_manifest=args.include_manifest,
            novel_ids=args.novel_ids, slugs=args.slugs,
        )
        sys.exit(1 if failures else 0)

    if not args.novel_id:
        parser.error(
            "--novel-id is required (use --list to see available novels, "
            "--novel-ids for an explicit list, or --all to export all)"
        )

    # When --slug is provided, use it as subdirectory under output-dir
    output_dir = Path(args.output_dir)
    if args.slug:
        output_dir = output_dir / args.slug

    failures = export_demo(
        base_url=args.base_url,
        novel_id=args.novel_id,
        output_dir=output_dir,
        compress=compress,
        include_text=include_text,
        text_only=text_only,
    )

    # Generate manifest for single-novel export
    if args.include_manifest:
        novel = api_get(args.base_url, f"/api/novels/{args.novel_id}")
        if novel and isinstance(novel, dict):
            if args.slug:
                novel["slug"] = args.slug
                manifest_dir = Path(args.output_dir)
            else:
                # No --slug: data landed directly in output_dir, so the manifest
                # must live one level up and the "slug" is the dir's own name.
                novel["slug"] = Path(args.output_dir).name
                manifest_dir = Path(args.output_dir).parent
            generate_manifest(args.base_url, manifest_dir, [novel])

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
