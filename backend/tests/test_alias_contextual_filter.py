"""E3 (issue #70): stable alias 与语境指称分层 —— 验证层闸测试。

FactValidator._clean_aliases Rule 4:命中语境指称特征(代词自称、假想名、
外观状态短语、泛类身份、临时指称)的 alias 不入稳定别名,记 logger.info +
name_resolution 审计(rule=contextual_alias_drop)。

原则:宁可漏拦不可误杀 —— 真绰号(大贤良师/美髯公)、含「子」「阿」前缀的
正常名必须放行。
"""

from __future__ import annotations

import json

import pytest

from src.extraction.fact_validator import FactValidator, _is_contextual_alias
from src.models.chapter_fact import ChapterFact, CharacterFact


def _read_jsonl(path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── 单元:_is_contextual_alias 拦截面 ──


@pytest.mark.parametrize("alias", [
    # 代词与自称
    "吾", "汝等", "尔等", "在下", "老夫", "妾身", "贫道", "本座", "朕",
    # 假想/反事实身份(假如她其实叫 X)
    "假如叫小雪", "假装叫玲", "要是叫阿蓝",
    # 外观与状态描述(身穿某装备)
    "身穿灵装", "手持巨剑", "受伤状态",
    # 泛类身份(某组织成员)
    "组织成员", "某组织成员", "一个士兵",
    # 临时指称(某某少年)
    "某某少年", "一个士兵", "那名少女",
    # 描述性短语(含「的」)
    "受伤的女孩",
])
def test_contextual_alias_blocked(alias):
    """语境指称类 alias 命中软校验,返回原因字符串。"""
    assert _is_contextual_alias(alias) is not None


@pytest.mark.parametrize("alias", [
    # 真绰号/称号必须放行
    "大贤良师", "美髯公", "及时雨", "齐天大圣", "行者", "卧龙",
    # 含「子」「阿」前缀的正常名不误伤
    "子龙", "阿瞒", "阿斗", "阿香",
    # 简称/字号
    "士隐", "雨村", "贾化",
    # 代号
    "公主",  # 泛类词本身不拦(是否合适由其他层判断),本闸只拦语境指称
])
def test_stable_alias_passes(alias):
    """稳定称呼/绰号/代号不命中,放行(宁可漏拦不可误杀)。"""
    assert _is_contextual_alias(alias) is None


def test_empty_alias_passes():
    """空串不命中(由 _clean_aliases 的空值分支处理)。"""
    assert _is_contextual_alias("") is None


# ── 集成:validate() 中 Rule 4 生效 + 审计落盘 ──


def test_validate_drops_contextual_aliases_keeps_stable(tmp_path):
    """混合 alias 列表:语境指称被剔除,真绰号保留,角色本身不动。"""
    log = tmp_path / "name_resolution_log.jsonl"
    validator = FactValidator(audit_log_path=log)
    fact = ChapterFact(
        chapter_id=3, novel_id="n1",
        characters=[
            CharacterFact(
                name="关羽",
                new_aliases=["美髯公", "吾", "身穿绿袍", "假如叫关二", "某某将领"],
            ),
        ],
    )
    out = validator.validate(fact)

    assert [c.name for c in out.characters] == ["关羽"]  # 角色保留
    aliases = out.characters[0].new_aliases
    assert aliases == ["美髯公"]  # 语境指称全部被拦,真绰号放行

    records = [r for r in _read_jsonl(log) if r["rule"] == "contextual_alias_drop"]
    assert {r["from"] for r in records} == {"吾", "身穿绿袍", "假如叫关二", "某某将领"}
    for r in records:
        assert r["to"] == ""
        assert r["source"] == "correction"
        assert r["owner"] == "关羽"
        assert r["field"] == "characters.new_aliases"
        assert r["reason"]
        assert r["novel_id"] == "n1"
        assert r["chapter_id"] == 3


def test_validate_no_contextual_alias_no_log(tmp_path):
    """全是稳定别名 → 不拦截、不写审计。"""
    log = tmp_path / "fv.jsonl"
    validator = FactValidator(audit_log_path=log)
    fact = ChapterFact(
        chapter_id=1, novel_id="n1",
        characters=[
            CharacterFact(name="张角", new_aliases=["大贤良师"]),
            CharacterFact(name="关羽", new_aliases=["美髯公"]),
        ],
    )
    out = validator.validate(fact)

    assert out.characters[0].new_aliases == ["大贤良师"]
    assert out.characters[1].new_aliases == ["美髯公"]
    assert not log.exists()
