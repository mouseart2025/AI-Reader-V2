"""Regression tests for chapter splitting against ground truth baseline.

Reads labeled samples from scripts/split_ground_truth/baseline.json,
runs split_chapters_ex() on each, and verifies the result matches expectations.

Requires:
  - EBOOK_SAMPLE_DIR environment variable pointing to the sample .txt directory
  - scripts/split_ground_truth/baseline.json with labeled entries

All tests are skipped (not failed) when these are not available.

Usage:
    cd backend && EBOOK_SAMPLE_DIR=/path/to/ebooks uv run pytest tests/test_split_regression.py -v
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.utils.chapter_splitter import split_chapters_ex


# ── Fixtures ──────────────────────────────────────────────────

_GT_PATH = Path(__file__).parent.parent.parent / "scripts" / "split_ground_truth" / "baseline.json"


@pytest.fixture
def sample_dir():
    path = os.environ.get("EBOOK_SAMPLE_DIR")
    if not path or not os.path.isdir(path):
        pytest.skip("EBOOK_SAMPLE_DIR not set or not a directory")
    return Path(path)


def _load_ground_truth() -> list[dict]:
    if not _GT_PATH.exists():
        return []
    return json.loads(_GT_PATH.read_text(encoding="utf-8"))


def _gt_ids() -> list[str]:
    """Generate test IDs from ground truth entries."""
    entries = _load_ground_truth()
    return [e["file"] for e in entries]


def _gt_entries() -> list[dict]:
    return _load_ground_truth()


# ── Parametrized regression tests ─────────────────────────────

_entries = _gt_entries()

if _entries:
    @pytest.mark.regression
    @pytest.mark.parametrize("entry", _entries, ids=[e["file"] for e in _entries])
    def test_split_regression(entry: dict, sample_dir: Path):
        """Verify split result matches ground truth for a labeled sample."""
        filepath = sample_dir / entry["file"]
        if not filepath.exists():
            pytest.skip(f"Sample file not found: {entry['file']}")

        # Read and normalize
        text = filepath.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")

        # Verify file hash (detect file changes)
        file_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if entry.get("file_hash") and file_hash != entry["file_hash"]:
            pytest.skip(f"File hash mismatch — file may have changed: {entry['file']}")

        # Run splitter
        result = split_chapters_ex(text)
        chapters = result.chapters

        # Skip mode check for "correct_no_split" entries
        if not entry.get("correct_no_split", False):
            # Verify matched mode
            expected_mode = entry.get("expected_mode")
            if expected_mode:
                assert result.matched_mode == expected_mode, (
                    f"Mode mismatch: expected {expected_mode}, got {result.matched_mode}"
                )

        # Verify chapter count within tolerance
        expected_count = entry.get("expected_chapter_count")
        if expected_count is not None:
            tolerance = max(2, int(expected_count * 0.1))
            actual_count = len(chapters)
            assert abs(actual_count - expected_count) <= tolerance, (
                f"Chapter count: expected {expected_count} ±{tolerance}, "
                f"got {actual_count}"
            )

        # Verify first chapter title (substring match)
        expected_first = entry.get("expected_first_title")
        if expected_first and chapters:
            assert expected_first in chapters[0].title, (
                f"First title mismatch: expected substring '{expected_first}' "
                f"in '{chapters[0].title}'"
            )
else:
    @pytest.mark.regression
    def test_no_ground_truth():
        """Placeholder: no ground truth entries found."""
        pytest.skip("No ground truth baseline.json found or it is empty")
