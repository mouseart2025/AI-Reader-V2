#!/usr/bin/env python3
"""Interactive ground truth labeling tool for chapter splitting.

Generates baseline.json for regression testing. Supports two input modes:
  --from-report report.json   Auto-sample from analysis report by problem_category
  --files file1.txt file2.txt  Manually specify files to label

Usage:
    cd backend && uv run python ../scripts/generate_ground_truth.py --files /path/to/book1.txt /path/to/book2.txt
    cd backend && uv run python ../scripts/generate_ground_truth.py --from-report ../scripts/split_analysis_report.json --sample 20
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from src.utils.chapter_splitter import split_chapters_ex  # noqa: E402

GT_DIR = Path(__file__).resolve().parent / "split_ground_truth"
BASELINE_PATH = GT_DIR / "baseline.json"


def compute_file_hash(text: str) -> str:
    """Compute SHA256 after normalizing line endings (CRLF → LF)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_existing_baseline() -> list[dict]:
    if BASELINE_PATH.exists():
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return []


def save_baseline(entries: list[dict]) -> None:
    GT_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n已保存 {len(entries)} 条标注到 {BASELINE_PATH}")


def label_file(filepath: Path, existing_files: set[str]) -> dict | None:
    """Interactively label a single file. Returns entry dict or None if skipped."""
    filename = filepath.name
    if filename in existing_files:
        print(f"\n跳过 {filename}（已标注）")
        return None

    print(f"\n{'='*60}")
    print(f"文件: {filename}")
    print(f"大小: {filepath.stat().st_size / 1024:.1f} KB")

    try:
        text = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = filepath.read_text(encoding="gbk")
        except Exception:
            print(f"  ✗ 无法读取文件编码")
            return None

    file_hash = compute_file_hash(text)
    result = split_chapters_ex(text)

    chapters = result.chapters
    print(f"\n当前引擎结果:")
    print(f"  模式: {result.matched_mode}")
    print(f"  Fallback: {result.is_fallback}")
    print(f"  章节数: {len(chapters)}")
    if chapters:
        print(f"  首章: {chapters[0].title}")
        if len(chapters) > 1:
            print(f"  第2章: {chapters[1].title}")
        if len(chapters) > 2:
            print(f"  第3章: {chapters[2].title}")
        print(f"  末章: {chapters[-1].title}")
        sizes = [ch.word_count for ch in chapters]
        print(f"  字数范围: {min(sizes)} ~ {max(sizes)}")

    print(f"\n判断: [Y] 正确  [N] 有误  [S] 跳过  [C] 正确不切分")
    while True:
        choice = input("> ").strip().upper()
        if choice in ("Y", "N", "S", "C"):
            break
        print("请输入 Y/N/S/C")

    if choice == "S":
        print("  已跳过")
        return None

    entry = {
        "file": filename,
        "file_hash": file_hash,
        "file_size": len(text),
    }

    if choice == "C":
        # Correct: no splitting needed (essay/poetry/short)
        entry.update({
            "correct_no_split": True,
            "expected_mode": result.matched_mode,
            "expected_chapter_count": len(chapters),
            "expected_first_title": chapters[0].title if chapters else None,
            "expected_last_title": chapters[-1].title if chapters else None,
            "quality": "good",
            "notes": "正确不切分",
        })
        print("  ✓ 标记为正确不切分")
        return entry

    if choice == "Y":
        entry.update({
            "correct_no_split": False,
            "expected_mode": result.matched_mode,
            "expected_chapter_count": len(chapters),
            "expected_first_title": chapters[0].title if chapters else None,
            "expected_last_title": chapters[-1].title if chapters else None,
            "quality": "good",
            "notes": "",
        })
        print("  ✓ 标记为正确")
        return entry

    # choice == "N" — incorrect, need manual input
    print(f"\n当前模式 '{result.matched_mode}' 切出 {len(chapters)} 章，")
    print("请输入预期值（回车跳过使用当前值）:")

    expected_mode = input(f"  预期模式 [{result.matched_mode}]: ").strip()
    if not expected_mode:
        expected_mode = result.matched_mode

    count_str = input(f"  预期章节数 [{len(chapters)}]: ").strip()
    expected_count = int(count_str) if count_str else len(chapters)

    first_title = input(f"  预期首章标题子串 [{chapters[0].title if chapters else ''}]: ").strip()
    if not first_title and chapters:
        first_title = chapters[0].title

    notes = input("  备注: ").strip()

    entry.update({
        "correct_no_split": False,
        "expected_mode": expected_mode,
        "expected_chapter_count": expected_count,
        "expected_first_title": first_title or None,
        "expected_last_title": chapters[-1].title if chapters else None,
        "quality": "bad",
        "notes": notes,
    })
    print("  ✓ 标记为有误")
    return entry


def sample_from_report(report_path: Path, sample_size: int) -> list[str]:
    """Auto-sample files from analysis report by problem_category."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    results = report.get("results", [])

    # Group by problem_category
    by_cat: dict[str, list[str]] = {}
    for r in results:
        cat = r.get("problem_category", "ok")
        by_cat.setdefault(cat, []).append(r["file"])

    # Sample proportionally, prioritizing problems
    sampled: list[str] = []
    # First take problems
    problem_cats = [c for c in by_cat if c != "ok"]
    per_cat = max(1, sample_size // (len(problem_cats) + 1)) if problem_cats else sample_size
    for cat in problem_cats:
        files = by_cat[cat][:per_cat]
        sampled.extend(files)
        print(f"  {cat}: 选取 {len(files)} 本")

    # Fill remaining with ok samples
    remaining = sample_size - len(sampled)
    if remaining > 0 and "ok" in by_cat:
        ok_files = by_cat["ok"][:remaining]
        sampled.extend(ok_files)
        print(f"  ok: 选取 {len(ok_files)} 本")

    return sampled[:sample_size]


def main():
    parser = argparse.ArgumentParser(description="Ground truth labeling tool")
    parser.add_argument("--files", nargs="+", help="Specific .txt files to label")
    parser.add_argument("--from-report", dest="report", help="Auto-sample from analysis report JSON")
    parser.add_argument("--sample", type=int, default=20, help="Number of files to sample from report")
    parser.add_argument("--dir", default="", help="Base directory for files (from report or --files)")
    args = parser.parse_args()

    sample_dir = args.dir or ""

    if args.report:
        report_path = Path(args.report)
        if not report_path.exists():
            print(f"错误: 报告文件不存在: {report_path}")
            sys.exit(1)
        filenames = sample_from_report(report_path, args.sample)
        if not sample_dir:
            import os
            sample_dir = os.environ.get("EBOOK_SAMPLE_DIR", "")
        if not sample_dir:
            print("错误: 需要 --dir 或 EBOOK_SAMPLE_DIR 指定样本目录")
            sys.exit(1)
        filepaths = [Path(sample_dir) / f for f in filenames]
    elif args.files:
        filepaths = [Path(f) for f in args.files]
    else:
        print("错误: 需要 --files 或 --from-report 参数")
        sys.exit(1)

    # Load existing baseline
    existing = load_existing_baseline()
    existing_files = {e["file"] for e in existing}
    print(f"已有 {len(existing)} 条标注")

    # Label each file
    new_entries = []
    for fp in filepaths:
        if not fp.exists():
            print(f"\n跳过 {fp.name}（文件不存在）")
            continue
        entry = label_file(fp, existing_files)
        if entry:
            new_entries.append(entry)
            # Save incrementally
            existing.append(entry)
            existing_files.add(entry["file"])
            save_baseline(existing)

    print(f"\n完成！新增 {len(new_entries)} 条标注，总计 {len(existing)} 条")


if __name__ == "__main__":
    main()
