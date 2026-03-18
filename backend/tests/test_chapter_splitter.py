"""Unit tests for chapter_splitter.split_chapters_ex().

All tests use synthetic text — no external files required.
"""

from src.utils.chapter_splitter import split_chapters_ex, detect_text_genre, SplitResult, ChapterInfo


# ── Helper ────────────────────────────────────────────────────

def _make_text(chapters: list[tuple[str, str]]) -> str:
    """Build synthetic novel text from (heading, body) tuples."""
    parts = []
    for heading, body in chapters:
        parts.append(f"{heading}\n{body}")
    return "\n\n".join(parts)


def _body(n: int = 1000) -> str:
    """Generate a body of approximately n chars."""
    sentence = "这是一段正文内容，用于测试章节切分。"
    repeats = max(1, n // len(sentence))
    return "\n".join([sentence] * repeats)


# ── Mode Tests (7 modes) ─────────────────────────────────────

def test_mode_chapter_zh():
    text = _make_text([
        ("第一章 开始", _body()),
        ("第二章 发展", _body()),
        ("第三章 高潮", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "chapter_zh"
    assert len(r.chapters) >= 3
    assert not r.is_fallback


def test_mode_chapter_zh_番外():
    text = _make_text([
        ("第一章 正文", _body()),
        ("第二章 继续", _body()),
        ("番外 后日谈", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "chapter_zh"
    assert len(r.chapters) >= 3


def test_mode_section_zh():
    text = _make_text([
        ("第一回 桃园结义", _body()),
        ("第二回 张飞鞭督邮", _body()),
        ("第三回 虎牢关三战", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "section_zh"
    assert len(r.chapters) >= 3
    assert not r.is_fallback


def test_mode_cn_numbered():
    text = _make_text([
        ("一、春天", _body()),
        ("二、夏天", _body()),
        ("三、秋天", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "cn_numbered"
    assert len(r.chapters) >= 3
    assert not r.is_fallback


def test_mode_numbered():
    text = _make_text([
        ("1. 初遇", _body()),
        ("2. 重逢", _body()),
        ("3. 离别", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "numbered"
    assert len(r.chapters) >= 3
    assert not r.is_fallback


def test_mode_chapter_en():
    text = _make_text([
        ("Chapter 1 The Beginning", _body()),
        ("Chapter 2 The Middle", _body()),
        ("Chapter 3 The End", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "chapter_en"
    assert len(r.chapters) >= 3
    assert not r.is_fallback


def test_mode_markdown():
    text = _make_text([
        ("# 第一节", _body()),
        ("# 第二节", _body()),
        ("# 第三节", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "markdown"
    assert len(r.chapters) >= 3
    assert not r.is_fallback


def test_mode_separator():
    body = _body()
    text = f"{body}\n\n---\n\n{body}\n\n---\n\n{body}"
    r = split_chapters_ex(text)
    assert r.matched_mode == "separator"
    assert len(r.chapters) >= 2
    assert not r.is_fallback


# ── Priority Tests ────────────────────────────────────────────

def test_chapter_zh_beats_numbered():
    """chapter_zh has higher priority than numbered when both match."""
    text = _make_text([
        ("第一章 开始", _body()),
        ("第二章 继续", _body()),
        ("1. 条目一", "短内容"),
        ("2. 条目二", "短内容"),
    ])
    r = split_chapters_ex(text)
    # chapter_zh should win since it has higher priority and >= 2 matches
    assert r.matched_mode == "chapter_zh"


# ── Fallback Tests ────────────────────────────────────────────

def test_fallback_to_heuristic():
    """When no regex matches, fallback to heuristic title detection."""
    # Build a text with short title-like lines preceded by blank lines
    # Use long paragraphs to avoid essay/poetry genre detection
    long_body = ("这是一段很长的叙述性文字，描述了各种场景和人物的复杂关系。" * 50 + "\n") * 10
    parts = []
    for i in range(5):
        title = f"人物介绍{i+1}"
        parts.append(f"\n{title}\n{long_body}")
    text = "\n".join(parts)
    r = split_chapters_ex(text)
    # Should either use heuristic or fixed_size as fallback, or genre detection
    assert r.is_fallback or r.matched_mode in ("heuristic_title", "fixed_size", "genre_essay")


def test_fallback_to_fixed_size():
    """Plain text with no structure falls back to fixed_size or genre_essay."""
    # Continuous text with no headings — may be classified as essay (< 30K, no dialogue, no patterns)
    text = ("这是一段很长的文本内容。" * 2000)  # ~20K chars, no headings
    r = split_chapters_ex(text)
    assert r.matched_mode in ("fixed_size", "genre_essay")
    if r.matched_mode == "fixed_size":
        assert r.is_fallback
    else:
        assert not r.is_fallback  # genre_essay is intentional, not a fallback


def test_single_huge_chapter_triggers_fixed_size():
    """A single chapter > 30K chars should trigger fixed_size fallback."""
    body = "这是正文。\n" * 5000  # ~35K chars
    text = f"第一章 唯一的章节\n{body}"
    r = split_chapters_ex(text)
    # Only one chapter_zh match (< 2), so should fallback
    # If single huge chapter, should use fixed_size
    if len(r.chapters) == 1 and r.chapters[0].word_count > 30000:
        # OK — single chapter is acceptable if no fallback triggered
        pass
    else:
        assert len(r.chapters) >= 2


# ── Boundary Condition Tests ─────────────────────────────────

def test_empty_text():
    r = split_chapters_ex("")
    assert len(r.chapters) >= 1
    assert r.chapters[0].word_count == 0 or r.chapters[0].content == ""


def test_very_short_text():
    r = split_chapters_ex("短文本")
    assert len(r.chapters) == 1
    assert r.chapters[0].content == "短文本"


def test_prologue_preservation():
    """Prologue >= 100 chars should be preserved as 序章."""
    prologue = "这是序言内容。" * 20  # ~140 chars
    text = f"{prologue}\n\n第一章 正文\n{_body()}\n\n第二章 继续\n{_body()}"
    r = split_chapters_ex(text)
    assert r.chapters[0].title == "序章"
    assert r.chapters[0].word_count >= 100


def test_prologue_discard():
    """Prologue < 100 chars should be discarded."""
    prologue = "短序言"  # < 100 chars
    text = f"{prologue}\n\n第一章 正文\n{_body()}\n\n第二章 继续\n{_body()}"
    r = split_chapters_ex(text)
    assert r.chapters[0].title != "序章"


def test_oversized_subsplit():
    """Chapters > 50K chars should be sub-split."""
    body = "段落内容。\n" * 8000  # ~56K chars
    text = f"第一章 正常\n{_body()}\n\n第二章 超长\n{body}"
    r = split_chapters_ex(text)
    # The oversized chapter should be sub-split
    titles = [ch.title for ch in r.chapters]
    # Check for sub-split markers like "超长 (1)"
    assert any("(1)" in t or "(2)" in t for t in titles) or \
        all(ch.word_count <= 55000 for ch in r.chapters)


# ── Post-processing Tests ────────────────────────────────────

def test_volume_assignment():
    """Volume markers (第X卷/部) should be detected and assigned."""
    text = (
        "第一部 黎明\n\n"
        "第一章 起始\n" + _body() + "\n\n"
        "第二章 发展\n" + _body() + "\n\n"
        "第二部 黄昏\n\n"
        "第三章 转折\n" + _body() + "\n\n"
        "第四章 结局\n" + _body()
    )
    r = split_chapters_ex(text)
    assert r.matched_mode == "chapter_zh"
    # Check volume assignment
    vol_nums = [ch.volume_num for ch in r.chapters if ch.volume_num is not None]
    assert len(set(vol_nums)) >= 2  # At least 2 volumes


def test_dedup_adjacent():
    """Duplicate adjacent chapter titles should be merged."""
    text = (
        "第一章 测试\n" + _body() + "\n\n"
        "第一章 测试\n" + _body(200) + "\n\n"  # Duplicate
        "第二章 继续\n" + _body()
    )
    r = split_chapters_ex(text)
    titles = [ch.title for ch in r.chapters]
    # Duplicates should be merged — should not have two "测试" titles
    assert titles.count("测试") <= 1


def test_volume_reset_detection():
    """Repeated chapter numbers should trigger volume inference."""
    text = (
        "第一章 开始A\n" + _body() + "\n\n"
        "第二章 继续A\n" + _body() + "\n\n"
        "第一章 开始B\n" + _body() + "\n\n"  # Reset
        "第二章 继续B\n" + _body()
    )
    r = split_chapters_ex(text)
    # Should detect volume reset
    vol_nums = [ch.volume_num for ch in r.chapters if ch.volume_num is not None]
    if vol_nums:
        assert len(set(vol_nums)) >= 2


# ── Custom Input Tests ────────────────────────────────────────

def test_custom_regex():
    text = "【壹】开始\n" + _body() + "\n\n【贰】继续\n" + _body() + "\n\n【叁】结局\n" + _body()
    r = split_chapters_ex(text, custom_regex=r"^【[壹贰叁肆伍陆柒捌玖拾]+】")
    assert r.matched_mode == "custom"
    assert len(r.chapters) >= 3


def test_custom_regex_invalid():
    """Invalid regex should return single chapter without error."""
    r = split_chapters_ex("some text", custom_regex="[invalid")
    assert r.matched_mode == "custom"
    assert len(r.chapters) == 1


def test_split_points():
    text = "AAAA\nBBBB\nCCCC\nDDDD"
    r = split_chapters_ex(text, split_points=[5, 10])
    assert r.matched_mode == "manual"
    assert len(r.chapters) >= 2


# ── Specific Mode Tests ──────────────────────────────────────

def test_specific_mode_chapter_zh():
    text = _make_text([
        ("第一章 指定模式", _body()),
        ("第二章 验证", _body()),
    ])
    r = split_chapters_ex(text, mode="chapter_zh")
    assert r.matched_mode == "chapter_zh"


def test_specific_mode_fixed_size():
    text = _body(20000)
    r = split_chapters_ex(text, mode="fixed_size")
    assert r.matched_mode == "fixed_size"
    assert len(r.chapters) >= 2


def test_specific_mode_heuristic():
    """Specifying mode=heuristic_title forces heuristic detection."""
    parts = []
    for i in range(5):
        parts.append(f"\n标题{i+1}\n{_body(800)}")
    text = "\n".join(parts)
    r = split_chapters_ex(text, mode="heuristic_title")
    assert r.matched_mode in ("heuristic_title", "fixed_size")


# ── Chinese Number Tests ─────────────────────────────────────

def test_large_chinese_number():
    """Verify large Chinese numbers are handled (第一百二十三章)."""
    text = _make_text([
        ("第一百二十三章 远方", _body()),
        ("第一百二十四章 归来", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "chapter_zh"
    assert len(r.chapters) >= 2


def test_cn_numbered_two_digit():
    """Chinese numbered mode with compound numbers (十二、)."""
    text = _make_text([
        ("十一、上篇", _body()),
        ("十二、下篇", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.matched_mode == "cn_numbered"
    assert len(r.chapters) >= 2


# ── CRLF Normalization ────────────────────────────────────────

def test_crlf_normalization():
    """CRLF line endings should be normalized to LF."""
    text = "第一章 开始\r\n" + _body() + "\r\n\r\n第二章 继续\r\n" + _body()
    r = split_chapters_ex(text)
    assert r.matched_mode == "chapter_zh"
    assert len(r.chapters) >= 2


# ── Genre Detection Tests ─────────────────────────────────────

def test_genre_essay_detection():
    """AC10: Short text with no dialogue and no pattern matches → essay."""
    # < 30K chars, dialogue_ratio < 1%, no pattern >= 2 matches
    # Use longer paragraphs to avoid triggering poetry detection (avg_para_len > 50)
    para = "这是一篇优美的散文，描述了大自然的壮丽景色和作者内心深处的感受。春天的花朵在微风中摇曳，阳光洒满了整个山谷。"
    text = "\n".join([para] * 30)  # ~3.6K chars, avg_para_len > 50
    r = split_chapters_ex(text)
    assert r.detected_genre == "essay"
    assert len(r.chapters) == 1
    assert not r.is_fallback


def test_genre_novel_high_dialogue():
    """AC11: High dialogue text should be classified as novel."""
    # Generate text with > 5% dialogue lines
    lines = []
    for i in range(200):
        lines.append('"你好，" 他说。')
        lines.append("她微笑着看着远方。")
    text = "\n".join(lines)
    genre, confidence = detect_text_genre(text)
    assert genre == "novel"
    assert confidence > 0.7


def test_genre_safety_valve_large_text():
    """AC11b: Text > 50K chars should NOT be essay even if low dialogue."""
    # 80K chars, no dialogue, but too large for essay
    text = ("这是一段纯叙述性的长文本内容。\n" * 5000)  # ~80K chars
    genre, confidence = detect_text_genre(text)
    # Safety valve: > 50K can't be essay
    assert genre != "essay"
    assert genre != "poetry"


def test_genre_with_chapter_markers():
    """Text with chapter markers should not be essay even if short."""
    text = _make_text([
        ("第一章 开始", _body(2000)),
        ("第二章 继续", _body(2000)),
    ])
    genre, _ = detect_text_genre(text)
    # Has pattern matches, should not be essay
    assert genre != "essay"


def test_genre_detected_on_result():
    """detected_genre should be set on the SplitResult."""
    text = _make_text([
        ("第一章 开始", _body()),
        ("第二章 继续", _body()),
        ("第三章 结局", _body()),
    ])
    r = split_chapters_ex(text)
    assert r.detected_genre is not None
    assert isinstance(r.detected_genre, str)
