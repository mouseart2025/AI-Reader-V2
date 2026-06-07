"""Tests for the human-readable per-chapter Markdown export renderer."""

from src.models.chapter_fact import ChapterFact
from src.services.chapter_facts_markdown_renderer import _render_chapter


def _fact() -> ChapterFact:
    return ChapterFact.model_validate({
        "chapter_id": 6,
        "novel_id": "n",
        "characters": [
            {"name": "猴王", "new_aliases": ["美猴王"], "appearance": "金睛火眼",
             "abilities_gained": [{"dimension": "法术", "name": "七十二变", "description": "变化神通"}]},
        ],
        "relationships": [
            {"person_a": "猴王", "person_b": "二郎神", "relation_type": "敌对", "evidence": "斗经三百合"},
        ],
        "locations": [
            {"name": "花果山", "type": "山", "parent": "傲来国", "description": "猴王老巢"},
        ],
        "events": [
            {"summary": "大圣被擒", "type": "战斗", "importance": "high", "participants": ["猴王"], "location": "花果山"},
        ],
        "new_concepts": [
            {"name": "七十二变", "category": "功法", "definition": "地煞数变化"},
        ],
    })


def test_render_chapter_sections_and_alias_resolution():
    # alias map: 猴王 -> 孙悟空 (canonical), so output should show 孙悟空.
    alias_map = {"猴王": "孙悟空", "美猴王": "孙悟空"}
    md = _render_chapter(_fact(), "小圣施威降大圣", alias_map)

    assert "## 第 6 章 小圣施威降大圣" in md
    assert "### 👥 人物" in md
    assert "- **孙悟空**" in md          # character name resolved to canonical
    assert "别名：美猴王" in md           # original alias preserved as alias
    assert "七十二变" in md
    assert "### 🤝 关系" in md
    assert "孙悟空 —（敌对）→ 二郎神" in md
    assert "### 📍 地点" in md and "花果山" in md and "傲来国" in md
    assert "### ⚡ 事件" in md and "大圣被擒" in md
    assert "### 💡 新概念" in md


def test_render_empty_chapter_notes_no_facts():
    empty = ChapterFact.model_validate({"chapter_id": 99, "novel_id": "n"})
    md = _render_chapter(empty, "空章", {})
    assert "第 99 章 空章" in md
    assert "未抽取到结构化事实" in md
