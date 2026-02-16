#!/usr/bin/env python3
"""Benchmark chapter splitting across a corpus of TXT files.

Usage:
    python backend/scripts/benchmark_import.py <directory> [--output-dir <dir>]

Walks <directory> for .txt files, runs decode_text() + split_chapters() on each,
and writes results to CSV + summary JSON.  Zero LLM calls.
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Add backend/src to path so we can import modules directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.text_processor import decode_text, detect_encoding
from src.utils.chapter_splitter import split_chapters_ex
from src.utils.text_sanitizer import detect_noise

# ── "Usable" definition ──
# A) chapter_count >= 3 AND max_chapter_words <= 50,000
# B) chapter_count >= 2 AND total_words <= 80,000 (short/medium novels)
# C) fallback mode: chapter_count >= 5 AND 80% of chapters in [2000, 20000] words

_MAX_CHAPTER_WORDS = 50_000
_SHORT_NOVEL_THRESHOLD = 80_000


def is_usable(chapter_count: int, max_words: int, total_words: int, chapters_info: list) -> bool:
    """Check if a split result meets the 'usable' criteria."""
    # Criterion A
    if chapter_count >= 3 and max_words <= _MAX_CHAPTER_WORDS:
        return True
    # Criterion B
    if chapter_count >= 2 and total_words <= _SHORT_NOVEL_THRESHOLD:
        return True
    # Criterion C (fallback)
    if chapter_count >= 5:
        in_range = sum(1 for ch in chapters_info if 2000 <= ch.word_count <= 20000)
        if in_range / chapter_count >= 0.8:
            return True
    return False


def classify_failure(chapter_count: int, max_words: int, total_words: int) -> str:
    """Classify why a split is not usable."""
    if chapter_count == 1:
        if total_words > _MAX_CHAPTER_WORDS:
            return "single_huge_chapter"
        return "single_chapter_small"
    if chapter_count == 2 and total_words > _SHORT_NOVEL_THRESHOLD:
        return "only_2_chapters"
    if max_words > _MAX_CHAPTER_WORDS:
        return "oversized_chapter"
    if chapter_count < 3:
        return "too_few_chapters"
    return "other"


def process_file(filepath: Path) -> dict:
    """Process a single TXT file and return metrics."""
    result = {
        "filename": filepath.name,
        "filepath": str(filepath),
        "file_size": filepath.stat().st_size,
        "encoding": "",
        "chapter_count": 0,
        "max_chapter_words": 0,
        "avg_chapter_words": 0,
        "total_words": 0,
        "matched_mode": "",
        "is_fallback": False,
        "is_usable": False,
        "failure_reason": "",
        "noise_total": 0,
        "noise_url": 0,
        "noise_promo": 0,
        "noise_template": 0,
        "noise_decoration": 0,
        "noise_repeated": 0,
        "error": "",
    }

    try:
        raw = filepath.read_bytes()
        result["encoding"] = detect_encoding(raw)
        text = decode_text(raw)
        result["total_words"] = len(text)

        split_result = split_chapters_ex(text)
        chapters = split_result.chapters
        result["chapter_count"] = len(chapters)
        result["matched_mode"] = split_result.matched_mode
        result["is_fallback"] = split_result.is_fallback

        if chapters:
            word_counts = [ch.word_count for ch in chapters]
            result["max_chapter_words"] = max(word_counts)
            result["avg_chapter_words"] = round(sum(word_counts) / len(word_counts))

        result["is_usable"] = is_usable(
            result["chapter_count"],
            result["max_chapter_words"],
            result["total_words"],
            chapters,
        )

        if not result["is_usable"]:
            result["failure_reason"] = classify_failure(
                result["chapter_count"],
                result["max_chapter_words"],
                result["total_words"],
            )

        # Text hygiene detection
        try:
            report = detect_noise(text, chapters)
            result["noise_total"] = report.total_suspect_lines
            for cat, count in report.by_category.items():
                key = f"noise_{cat}"
                if key in result:
                    result[key] = count
        except Exception:
            pass  # Hygiene detection is optional

    except Exception as e:
        result["error"] = str(e)
        result["failure_reason"] = "exception"

    return result


def main():
    parser = argparse.ArgumentParser(description="Benchmark chapter splitting on a TXT corpus")
    parser.add_argument("directory", help="Directory containing .txt files")
    parser.add_argument("--output-dir", default=".", help="Output directory for CSV and JSON (default: current dir)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files to process (0 = all)")
    args = parser.parse_args()

    corpus_dir = Path(args.directory)
    if not corpus_dir.is_dir():
        print(f"Error: {corpus_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect all .txt files
    txt_files = sorted(corpus_dir.rglob("*.txt"))
    if args.limit > 0:
        txt_files = txt_files[: args.limit]

    total = len(txt_files)
    print(f"Found {total} .txt files in {corpus_dir}")

    # Process each file
    results: list[dict] = []
    start_time = time.time()

    for i, filepath in enumerate(txt_files, 1):
        if i % 100 == 0 or i == total:
            elapsed = time.time() - start_time
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  [{i}/{total}] {rate:.1f} files/s — {filepath.name}")

        result = process_file(filepath)
        results.append(result)

    elapsed = time.time() - start_time
    print(f"\nProcessed {total} files in {elapsed:.1f}s ({total / elapsed:.1f} files/s)")

    # ── Write CSV ──
    csv_path = output_dir / "benchmark_results.csv"
    csv_fields = [
        "filename", "file_size", "encoding", "chapter_count",
        "max_chapter_words", "avg_chapter_words", "total_words",
        "matched_mode", "is_fallback", "is_usable", "failure_reason",
        "noise_total", "noise_url", "noise_promo", "noise_template",
        "noise_decoration", "noise_repeated", "error",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"CSV written to {csv_path}")

    # ── Compute summary ──
    usable_count = sum(1 for r in results if r["is_usable"])
    error_count = sum(1 for r in results if r["error"])
    failure_reasons = Counter(r["failure_reason"] for r in results if r["failure_reason"])
    mode_dist = Counter(r["matched_mode"] for r in results if r["matched_mode"])
    fallback_count = sum(1 for r in results if r["is_fallback"])

    # Top 20 failure reasons
    top_failures = failure_reasons.most_common(20)

    # Top 20 failure samples
    failure_samples = [
        {"filename": r["filename"], "chapter_count": r["chapter_count"],
         "max_chapter_words": r["max_chapter_words"], "total_words": r["total_words"],
         "matched_mode": r["matched_mode"], "failure_reason": r["failure_reason"],
         "error": r["error"]}
        for r in results if not r["is_usable"]
    ][:20]

    # Hygiene stats
    noisy_count = sum(1 for r in results if r["noise_total"] > 0)
    avg_noise = round(sum(r["noise_total"] for r in results) / total, 1) if total > 0 else 0
    noise_category_totals = {
        cat: sum(r[f"noise_{cat}"] for r in results)
        for cat in ["url", "promo", "template", "decoration", "repeated"]
    }

    summary = {
        "total_files": total,
        "usable_count": usable_count,
        "usable_rate": round(usable_count / total * 100, 2) if total > 0 else 0,
        "error_count": error_count,
        "fallback_count": fallback_count,
        "mode_distribution": dict(mode_dist.most_common()),
        "failure_reason_distribution": dict(top_failures),
        "failure_samples_top20": failure_samples,
        "hygiene": {
            "files_with_noise": noisy_count,
            "avg_noise_lines_per_file": avg_noise,
            "category_totals": noise_category_totals,
        },
        "elapsed_seconds": round(elapsed, 1),
    }

    json_path = output_dir / "benchmark_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Summary written to {json_path}")

    # Print summary
    print(f"\n{'='*50}")
    print(f"  Total files:  {total}")
    print(f"  Usable:       {usable_count} ({summary['usable_rate']}%)")
    print(f"  Fallback:     {fallback_count}")
    print(f"  Failed:       {total - usable_count - error_count}")
    print(f"  Errors:       {error_count}")
    print(f"{'='*50}")
    if mode_dist:
        print("\n  Split mode distribution:")
        for mode, count in mode_dist.most_common():
            print(f"    {mode}: {count}")
    if top_failures:
        print("\n  Top failure reasons:")
        for reason, count in top_failures:
            print(f"    {reason}: {count}")
    if noisy_count:
        print(f"\n  Hygiene: {noisy_count} files with noise (avg {avg_noise} lines/file)")
        for cat, cnt in noise_category_totals.items():
            if cnt > 0:
                print(f"    {cat}: {cnt} lines total")


if __name__ == "__main__":
    main()
