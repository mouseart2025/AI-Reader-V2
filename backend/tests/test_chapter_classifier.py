"""Unit tests for chapter_classifier.classify_chapters() and classify_chapters_detailed()."""

from src.utils.chapter_splitter import ChapterInfo
from src.utils.chapter_classifier import classify_chapters, classify_chapters_detailed


def _ch(title: str, content: str, word_count: int | None = None, chapter_num: int = 1) -> ChapterInfo:
    """Create a ChapterInfo for testing."""
    return ChapterInfo(
        chapter_num=chapter_num,
        title=title,
        content=content,
        word_count=word_count or len(content),
    )


def _dialogue_content(n: int = 5000) -> str:
    """Generate content with high dialogue ratio (> 3%).

    Uses ASCII double quote (U+0022) which is what _DIALOGUE_LINE regex matches.
    """
    lines = []
    for i in range(100):
        lines.append('"小明说道，你好。"')
        lines.append("他转过身去，看着远方。")
        lines.append('"好的，"她回答。')
    return "\n".join(lines)


def _prose_content(n: int = 5000) -> str:
    """Generate content with no dialogue markers."""
    sentence = "这是一段叙述性的文字，没有任何对话内容存在。"
    return "\n".join([sentence] * (n // len(sentence) + 1))


# ── Normal chapters should not be suspect ────────────────────

def test_normal_chapters_not_suspect():
    """Normal novel chapters with dialogue should all be False."""
    chapters = [
        _ch("第一章 开始", _dialogue_content(), chapter_num=1),
        _ch("第二章 发展", _dialogue_content(), chapter_num=2),
        _ch("第三章 高潮", _dialogue_content(), chapter_num=3),
        _ch("第四章 结局", _dialogue_content(), chapter_num=4),
    ]
    results = classify_chapters(chapters)
    assert results == [False, False, False, False]


# ── Strong title match → suspect ─────────────────────────────

def test_author_bio_suspect():
    """Title containing '作者简介' should be suspect."""
    chapters = [
        _ch("第一章 开始", _dialogue_content(), chapter_num=1),
        _ch("作者简介", "张三，著名作家。", chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[1] is True


def test_publisher_info_suspect():
    """Title containing '出版说明' should be suspect."""
    chapters = [
        _ch("出版说明", "本书由XX出版社出版。", chapter_num=1),
        _ch("第一章 开始", _dialogue_content(), chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[0] is True


# ── Publishing metadata in content → suspect ─────────────────

def test_copyright_content_suspect():
    """Content with >= 2 publishing keywords should be suspect."""
    content = """
    本书版权所有，未经许可不得转载。
    ISBN 978-7-000-00000-0
    出版社：人民文学出版社
    定价：49.80元
    """
    chapters = [
        _ch("第一章 开始", _dialogue_content(), chapter_num=1),
        _ch("版本信息", content, chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[1] is True


# ── Short prologue without dialogue → suspect ────────────────

def test_short_prologue_suspect():
    """Auto-generated 序章 < 3000 chars and no dialogue should be suspect."""
    chapters = [
        _ch("序章", _prose_content(2000), word_count=2000, chapter_num=1),
        _ch("第一章 开始", _dialogue_content(), chapter_num=2),
        _ch("第二章 继续", _dialogue_content(), chapter_num=3),
    ]
    results = classify_chapters(chapters)
    assert results[0] is True  # Short prologue without dialogue


def test_short_prologue_with_dialogue_not_suspect():
    """序章 < 3000 chars but WITH dialogue should NOT be suspect."""
    chapters = [
        _ch("序章", _dialogue_content(), word_count=2000, chapter_num=1),
        _ch("第一章 开始", _dialogue_content(), chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[0] is False


# ── Boundary chapters with suspect titles ─────────────────────

def test_boundary_chapter_suspect():
    """Last chapter titled '后记' without dialogue should be suspect."""
    chapters = [
        _ch("第一章 开始", _dialogue_content(), chapter_num=1),
        _ch("第二章 发展", _dialogue_content(), chapter_num=2),
        _ch("后记", _prose_content(800), chapter_num=3),
    ]
    results = classify_chapters(chapters)
    assert results[2] is True


def test_boundary_chapter_with_dialogue_not_suspect():
    """Last chapter titled '后记' WITH dialogue should NOT be suspect."""
    chapters = [
        _ch("第一章 开始", _dialogue_content(), chapter_num=1),
        _ch("后记", _dialogue_content(), chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[1] is False


# ── Dialogue detection ────────────────────────────────────────

def test_dialogue_detection_prevents_false_positive():
    """High dialogue content (> 3%) should prevent suspect marking."""
    chapters = [
        _ch("序章", _dialogue_content(), word_count=2500, chapter_num=1),
        _ch("第一章 开始", _dialogue_content(), chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[0] is False


# ── Empty input ───────────────────────────────────────────────

def test_empty_chapters():
    assert classify_chapters([]) == []


# ── Detailed classification ───────────────────────────────────

def test_detailed_returns_reasons():
    """classify_chapters_detailed should return ClassifyResult with reasons."""
    chapters = [
        _ch("作者简介", "张三，著名作家。", chapter_num=1),
        _ch("第一章 开始", _dialogue_content(), chapter_num=2),
    ]
    results = classify_chapters_detailed(chapters)
    assert len(results) == 2
    assert results[0].is_suspect is True
    assert "标题" in results[0].reason
    assert results[1].is_suspect is False


# ── Literary criticism ────────────────────────────────────────

def test_literary_criticism_suspect():
    """Content with >= 3 literary criticism keywords should be suspect."""
    content = """
    作者以独特的叙事手法，展现了深厚的文学价值。
    本书以宏大的历史背景为基础，获得了茅盾文学奖。
    文学评论界对此书给予了高度评价。
    """
    chapters = [
        _ch("第一章 正文", _dialogue_content(), chapter_num=1),
        _ch("评论", content, chapter_num=2),
    ]
    results = classify_chapters(chapters)
    assert results[1] is True
