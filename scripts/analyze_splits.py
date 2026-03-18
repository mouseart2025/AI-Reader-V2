#!/usr/bin/env python3
"""Batch chapter-split analysis script.

Scans a directory of .txt files, runs split_chapters_ex() on each,
and produces a JSON report with quality metrics and problem detection.

Usage:
    cd backend && uv run python ../scripts/analyze_splits.py --output ../scripts/split_analysis_report.json
    cd backend && uv run python ../scripts/analyze_splits.py --sample 50
    cd backend && uv run python ../scripts/analyze_splits.py --problems-only
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean, median, stdev

# Add backend/src to path so we can import the splitter directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from src.utils.chapter_splitter import split_chapters_ex  # noqa: E402


def _compute_dialogue_ratio(text: str) -> float:
    """Compute ratio of lines that start with dialogue markers."""
    import re
    dialogue_line = re.compile(r'^[""「]', re.MULTILINE)
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return 0.0
    dialogue_lines = sum(1 for l in lines if dialogue_line.search(l.strip()))
    return dialogue_lines / len(lines)


def _compute_avg_para_len(text: str) -> float:
    """Compute average paragraph length (non-empty lines)."""
    paras = [l.strip() for l in text.split("\n") if l.strip()]
    if not paras:
        return 0.0
    return mean(len(p) for p in paras)


def _compute_heading_candidate_density(text: str) -> float:
    """Compute ratio of lines that could be headings (short, no body punctuation)."""
    import re
    body_punct = set("。，；：！？…、》）」』】")
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return 0.0
    candidates = sum(
        1 for l in lines
        if len(l) <= 30 and not any(c in body_punct for c in l)
    )
    return candidates / len(lines)


def analyze_file(filepath: Path) -> dict:
    """Analyze a single .txt file and return metrics."""
    text = filepath.read_text(encoding="utf-8")
    text_len = len(text)

    start = time.monotonic()
    result = split_chapters_ex(text)
    elapsed_ms = (time.monotonic() - start) * 1000

    chapters = result.chapters
    chapter_count = len(chapters)
    sizes = [ch.word_count for ch in chapters]
    avg_size = mean(sizes) if sizes else 0
    max_size = max(sizes) if sizes else 0
    min_size = min(sizes) if sizes else 0
    size_stdev = stdev(sizes) if len(sizes) > 1 else 0
    cv = (size_stdev / avg_size) if avg_size > 0 else 0
    tiny_count = sum(1 for s in sizes if s < 200)
    huge_count = sum(1 for s in sizes if s > 50000)

    # Diagnosis
    if result.is_fallback:
        if result.matched_mode == "fixed_size":
            problem_category = "no_heading_match"
        else:
            problem_category = "fallback_used"
    elif chapter_count == 1 and max_size > 30000:
        problem_category = "single_huge_chapter"
    elif chapter_count < 5 and text_len > 100000:
        problem_category = "heading_too_sparse"
    elif avg_size < 500 and chapter_count > 10:
        problem_category = "heading_too_dense"
    elif tiny_count > 0 and tiny_count / chapter_count > 0.2:
        problem_category = "many_tiny_chapters"
    elif cv > 2.0:
        problem_category = "high_variance"
    else:
        problem_category = "ok"

    # Text features for genre analysis
    # Only compute on a sample to save time (first 50K chars)
    sample_text = text[:50000]
    dialogue_ratio = _compute_dialogue_ratio(sample_text)
    avg_para_len = _compute_avg_para_len(sample_text)
    heading_density = _compute_heading_candidate_density(sample_text)

    return {
        "file": filepath.name,
        "file_size": text_len,
        "matched_mode": result.matched_mode,
        "is_fallback": result.is_fallback,
        "chapter_count": chapter_count,
        "avg_chapter_size": round(avg_size),
        "max_chapter_size": max_size,
        "min_chapter_size": min_size,
        "size_cv": round(cv, 3),
        "tiny_chapters": tiny_count,
        "huge_chapters": huge_count,
        "first_title": chapters[0].title if chapters else "",
        "last_title": chapters[-1].title if chapters else "",
        "problem_category": problem_category,
        "elapsed_ms": round(elapsed_ms, 1),
        # Text feature metrics
        "dialogue_ratio": round(dialogue_ratio, 4),
        "avg_para_len": round(avg_para_len, 1),
        "heading_density": round(heading_density, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Batch chapter-split analysis")
    parser.add_argument(
        "--dir",
        default=os.environ.get("EBOOK_SAMPLE_DIR", ""),
        help="Directory containing .txt files (default: $EBOOK_SAMPLE_DIR)",
    )
    parser.add_argument("--output", "-o", default=None, help="Output JSON report path")
    parser.add_argument("--sample", type=int, default=0, help="Process only first N files")
    parser.add_argument("--problems-only", action="store_true", help="Only output problem files")
    args = parser.parse_args()

    sample_dir = Path(args.dir)
    if not sample_dir.is_dir():
        print(f"错误: 样本目录不存在: {sample_dir}")
        print("请设置 EBOOK_SAMPLE_DIR 环境变量或使用 --dir 参数")
        sys.exit(1)

    txt_files = sorted(sample_dir.glob("*.txt"))
    if not txt_files:
        print(f"错误: {sample_dir} 中没有找到 .txt 文件")
        sys.exit(1)

    if args.sample > 0:
        txt_files = txt_files[: args.sample]

    print(f"分析 {len(txt_files)} 个文件 (来自 {sample_dir}) ...")

    results = []
    errors = []
    for i, f in enumerate(txt_files, 1):
        try:
            r = analyze_file(f)
            results.append(r)
            status = "✓" if r["problem_category"] == "ok" else f"⚠ {r['problem_category']}"
            print(f"  [{i}/{len(txt_files)}] {f.name}: {r['matched_mode']} → {r['chapter_count']}章 {status}")
        except Exception as e:
            errors.append({"file": f.name, "error": str(e)})
            print(f"  [{i}/{len(txt_files)}] {f.name}: ✗ {e}")

    # Summary statistics
    if results:
        mode_dist = {}
        problem_dist = {}
        for r in results:
            mode_dist[r["matched_mode"]] = mode_dist.get(r["matched_mode"], 0) + 1
            problem_dist[r["problem_category"]] = problem_dist.get(r["problem_category"], 0) + 1

        total = len(results)
        fallback_count = sum(1 for r in results if r["is_fallback"])
        ok_count = problem_dist.get("ok", 0)
        elapsed_values = [r["elapsed_ms"] for r in results]

        summary = {
            "total_files": total,
            "ok_count": ok_count,
            "problem_count": total - ok_count,
            "error_count": len(errors),
            "fallback_rate": round(fallback_count / total, 4) if total else 0,
            "mode_distribution": dict(sorted(mode_dist.items(), key=lambda x: -x[1])),
            "problem_distribution": dict(sorted(problem_dist.items(), key=lambda x: -x[1])),
            "timing": {
                "avg_ms": round(mean(elapsed_values), 1),
                "median_ms": round(median(elapsed_values), 1),
                "max_ms": round(max(elapsed_values), 1),
            },
            # Genre feature distributions
            "dialogue_ratio_median": round(median(r["dialogue_ratio"] for r in results), 4),
            "avg_para_len_median": round(median(r["avg_para_len"] for r in results), 1),
        }
    else:
        summary = {"total_files": 0, "error_count": len(errors)}

    # Filter for problems-only
    output_results = results
    if args.problems_only:
        output_results = [r for r in results if r["problem_category"] != "ok"]

    report = {
        "summary": summary,
        "results": output_results,
        "errors": errors,
    }

    # Output
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已保存: {out_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    # Print summary
    if results:
        print(f"\n=== 汇总 ===")
        print(f"总文件数: {summary['total_files']}")
        print(f"正常: {summary['ok_count']} ({summary['ok_count']/summary['total_files']*100:.1f}%)")
        print(f"问题: {summary['problem_count']} ({summary['problem_count']/summary['total_files']*100:.1f}%)")
        if errors:
            print(f"错误: {summary['error_count']}")
        print(f"Fallback 率: {summary['fallback_rate']*100:.1f}%")
        print(f"模式分布: {summary['mode_distribution']}")
        print(f"问题分布: {summary['problem_distribution']}")
        print(f"耗时: 均值 {summary['timing']['avg_ms']}ms, 中位数 {summary['timing']['median_ms']}ms, 最大 {summary['timing']['max_ms']}ms")


if __name__ == "__main__":
    main()
