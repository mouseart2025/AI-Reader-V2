"""Tests for alias_resolver — safety levels, char variant normalization, Union-Find merging."""

import pytest

from src.services.alias_resolver import (
    _alias_safety_level,
    _apply_known_hotfix_patches,
    _hotfix_xiyouji_sha_bajie,
)


# ── _alias_safety_level tests ───────────────────────────────────


class TestAliasSafetyLevel:
    """Safety level classification for alias names."""

    # Level 0: hard-block

    def test_kinship_terms_blocked(self):
        assert _alias_safety_level("哥哥") == 0
        assert _alias_safety_level("妈妈") == 0

    def test_generic_person_blocked(self):
        assert _alias_safety_level("老人") == 0
        assert _alias_safety_level("妇人") == 0
        assert _alias_safety_level("妖精") == 0

    def test_pure_titles_blocked(self):
        assert _alias_safety_level("长老") == 0
        assert _alias_safety_level("掌门") == 0
        assert _alias_safety_level("师父") == 0

    def test_deictic_pattern_blocked(self):
        """那+role pattern: 那贼, 那厮."""
        assert _alias_safety_level("那贼") == 0
        assert _alias_safety_level("那厮") == 0

    def test_surname_title_blocked(self):
        """韩前辈, 林道友 etc."""
        assert _alias_safety_level("韩前辈") == 0
        assert _alias_safety_level("王师兄") == 0

    # Level 1: soft-block

    def test_too_long_suspicious(self):
        assert _alias_safety_level("独孤求败风清扬令狐冲") == 1  # 9 chars, no "的"

    def test_collective_markers(self):
        assert _alias_safety_level("众猴") == 1
        assert _alias_safety_level("孩儿们") == 1

    def test_single_char_suspicious(self):
        assert _alias_safety_level("僧") == 1

    def test_quantity_phrase(self):
        """Numeric prefix + measure word = quantity, not name."""
        assert _alias_safety_level("两个仙女") == 1
        assert _alias_safety_level("百余人") == 1

    # Level 2: safe

    def test_valid_names_safe(self):
        assert _alias_safety_level("孙悟空") == 2
        assert _alias_safety_level("韩立") == 2
        assert _alias_safety_level("唐僧") == 2
        assert _alias_safety_level("三藏") == 2
        assert _alias_safety_level("行者") == 2
        assert _alias_safety_level("悟空") == 2

    def test_journey_west_aliases_safe(self):
        """Bug #5: key Journey to the West aliases should be level 2."""
        assert _alias_safety_level("陈玄奘") == 2
        assert _alias_safety_level("唐三藏") == 2
        assert _alias_safety_level("江流") == 2
        assert _alias_safety_level("齐天大圣") == 2
        assert _alias_safety_level("美猴王") == 2
        assert _alias_safety_level("孙行者") == 2

    def test_legitimate_numeric_prefix_names(self):
        """Names like 二愣子, 三太子 should pass."""
        assert _alias_safety_level("二愣子") == 2
        assert _alias_safety_level("三太子") == 2
        assert _alias_safety_level("一灯大师") == 2

    def test_royal_kinship_blocked(self):
        """Royal kinship terms bridge unrelated kings/queens across chapters."""
        assert _alias_safety_level("父王") == 0
        assert _alias_safety_level("母后") == 0
        assert _alias_safety_level("太后") == 0
        assert _alias_safety_level("公主") == 0
        assert _alias_safety_level("太子") == 0
        assert _alias_safety_level("驸马") == 0
        assert _alias_safety_level("殿下") == 0

    def test_qualified_royal_names_safe(self):
        """Specific royal names with qualifiers should pass."""
        assert _alias_safety_level("铁扇公主") == 2
        assert _alias_safety_level("乌鸡国国王") == 2
        assert _alias_safety_level("哪吒太子") == 2
        assert _alias_safety_level("百花公主") == 1  # 百 numeric prefix → soft-block

    def test_empty_blocked(self):
        assert _alias_safety_level("") == 0


# ── Hotfix patch tests ──────────────────────────────────────────


class TestHotfixXiyoujiShaBajie:
    """Demo-layer patch correcting 沙僧/八戒 UF mis-merge."""

    def _buggy_map(self):
        return {
            "沙僧": "八戒",
            "沙悟净": "八戒",
            "沙和尚": "八戒",
            "悟净": "八戒",
            "卷帘大将": "八戒",
            "卷帘": "八戒",
            "金身罗汉": "八戒",
            "三徒弟": "八戒",
            "悟能": "八戒",
            "猪八戒": "八戒",
            "那呆子": "八戒",
            "净坛使者": "八戒",
            "悟空": "孙悟空",
        }

    def test_signature_triggers_patch(self):
        patched = _hotfix_xiyouji_sha_bajie(self._buggy_map())
        assert patched["沙悟净"] == "沙僧"
        assert patched["沙和尚"] == "沙僧"
        assert patched["悟净"] == "沙僧"
        assert patched["卷帘大将"] == "沙僧"
        assert patched["卷帘"] == "沙僧"
        assert patched["金身罗汉"] == "沙僧"
        assert patched["三徒弟"] == "沙僧"

    def test_bajie_aliases_preserved(self):
        patched = _hotfix_xiyouji_sha_bajie(self._buggy_map())
        assert patched["悟能"] == "八戒"
        assert patched["猪八戒"] == "八戒"
        assert patched["那呆子"] == "八戒"
        assert patched["净坛使者"] == "八戒"

    def test_canonical_not_self_mapped(self):
        patched = _hotfix_xiyouji_sha_bajie(self._buggy_map())
        assert "沙僧" not in patched

    def test_sha_seng_becomes_canonical(self):
        patched = _hotfix_xiyouji_sha_bajie(self._buggy_map())
        assert "沙僧" in set(patched.values())

    def test_no_op_when_signature_absent(self):
        clean_map = {"悟空": "孙悟空", "沙悟净": "沙僧", "悟能": "八戒"}
        patched = _hotfix_xiyouji_sha_bajie(clean_map)
        assert patched == clean_map

    def test_unrelated_entries_untouched(self):
        patched = _hotfix_xiyouji_sha_bajie(self._buggy_map())
        assert patched["悟空"] == "孙悟空"

    def test_apply_all_hotfixes_chains_patches(self):
        patched = _apply_known_hotfix_patches(self._buggy_map())
        assert patched["沙悟净"] == "沙僧"
        assert patched["悟能"] == "八戒"
