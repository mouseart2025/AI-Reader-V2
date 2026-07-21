"""Tests for the hallucination-island filter (issue #30)."""

from src.services.hallucination_filter import find_ungrounded_names


CORPUS = "话说孙悟空大闹天宫，与猪八戒一同西行取经。那韩立从未在此书出现。"


class TestFindUngroundedNames:
    def test_grounded_by_canonical_name(self):
        assert find_ungrounded_names(CORPUS, {"孙悟空": set()}) == set()

    def test_grounded_by_alias(self):
        # Canonical name absent, but an alias appears in the text.
        assert find_ungrounded_names(CORPUS, {"齐天大圣": {"孙悟空"}}) == set()

    def test_ungrounded_name_filtered(self):
        # 厉飞雨 is a 《凡人修仙传》 character leaked into another novel's facts.
        result = find_ungrounded_names(CORPUS, {"厉飞雨": {"厉师兄"}})
        assert result == {"厉飞雨"}

    def test_ungrounded_canonical_rescued_by_grounded_alias(self):
        assert find_ungrounded_names(CORPUS, {"猪刚鬣": {"猪八戒"}}) == set()

    def test_short_names_kept_as_unverifiable(self):
        # Single-char names produce too many spurious substring hits to verify.
        assert find_ungrounded_names("", {"甲": set()}) == set()

    def test_empty_corpus_filters_verifiable_names(self):
        assert find_ungrounded_names("", {"孙悟空": set()}) == {"孙悟空"}

    def test_mixed_population(self):
        names_aliases = {
            "孙悟空": {"行者"},
            "猪八戒": set(),
            "厉飞雨": set(),
            "墨居仁": {"墨大夫"},
        }
        assert find_ungrounded_names(CORPUS, names_aliases) == {"厉飞雨", "墨居仁"}
