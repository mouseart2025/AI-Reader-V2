"""Tests for source-language heuristic adapters."""

import pytest

from src.extraction.entity_pre_scanner import EntityPreScanner
from src.extraction.source_language_heuristics import get_source_language_heuristics


VI_CHAPTERS = [
    (
        "Trần Quốc Tuấn đứng trên bến Chương Dương nhìn về sông Hồng. "
        "Tướng Trần Quốc Tuấn nói: \"Quân Đại Việt sẽ giữ thành Thăng Long.\" "
        "Phạm Ngũ Lão thưa rằng nghĩa quân Đại Việt đã tới làng Phù Đổng."
    ),
    (
        "Nguyễn Trãi gặp Lê Lợi bên núi Lam Sơn. "
        "\"Việc lớn đã đến\", Nguyễn Trãi nói, rồi chỉ về phía sông Mã."
    ),
]
VI_TITLES = ["Chương 1: Bến Chương Dương", "Chương 2: Núi Lam Sơn"]
VI_TEXT = "\n".join(VI_CHAPTERS)


def test_vietnamese_heuristics_do_not_use_chinese_prescan(monkeypatch):
    scanner = EntityPreScanner()

    def fail_chinese_scan(_text: str):
        raise AssertionError("Chinese jieba scanner should not run for source_language=vi")

    monkeypatch.setattr(scanner, "_scan_word_freq", fail_chinese_scan)

    entries = scanner._phase1_scan(VI_CHAPTERS, VI_TITLES, VI_TEXT, source_language="vi")
    names = {entry.name for entry in entries}

    assert "Trần Quốc Tuấn" in names
    assert "bến Chương Dương" in names


def test_vietnamese_prescan_extracts_names_locations_and_orgs():
    scanner = EntityPreScanner()

    entries = scanner._phase1_scan(VI_CHAPTERS, VI_TITLES, VI_TEXT, source_language="vi")
    by_name = {entry.name: entry for entry in entries}

    assert by_name["Trần Quốc Tuấn"].entity_type == "person"
    assert by_name["Nguyễn Trãi"].entity_type == "person"
    assert by_name["bến Chương Dương"].entity_type == "location"
    assert by_name["sông Hồng"].entity_type == "location"
    assert by_name["quân Đại Việt"].entity_type == "org"
    assert "Chương" not in by_name


@pytest.mark.parametrize(
    ("source_language", "uses_chinese_prescan", "uses_chinese_name_corrections", "supports_prescan"),
    [
        ("zh-CN", True, True, True),
        ("auto", True, True, True),
        ("vi", False, False, True),
        ("en", False, False, False),
    ],
)
def test_source_language_heuristic_switches(
    source_language,
    uses_chinese_prescan,
    uses_chinese_name_corrections,
    supports_prescan,
):
    adapter = get_source_language_heuristics(source_language)

    assert adapter.uses_chinese_prescan is uses_chinese_prescan
    assert adapter.uses_chinese_name_corrections is uses_chinese_name_corrections
    assert adapter.supports_prescan is supports_prescan
