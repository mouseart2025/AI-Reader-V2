"""Tests for dynasty-aware "州" classification and TextVerifier context extraction.

Covers:
  - TierClassifier Rule 7: 州 tier varies by era/genre
  - _detect_era: era detection from genre + location names
  - _zhou_target_tier: era → tier mapping
  - _zhou_expected_tier (RuleValidator): genre → expected tier
  - TextVerifier.context(): snippet extraction with window
"""

import pytest
from collections import Counter

from src.services.geo_skills.tier_classifier import (
    TierClassifier,
    _detect_era,
    _zhou_target_tier,
)
from src.services.hierarchy_validator import (
    TextVerifier,
    RuleValidator,
    KnowledgeBase,
    _zhou_expected_tier,
)


# ── Era detection ───────────────────────────────────────────────


class TestDetectEra:
    """_detect_era infers historical era from genre + location names."""

    def test_fantasy_genre_returns_fantasy(self):
        """Pure fantasy without era keywords → fantasy."""
        assert _detect_era("fantasy", {"花果山", "水帘洞"}) == "fantasy"

    def test_fantasy_with_shangzhou_returns_shangzhou(self):
        """封神演义: genre=fantasy but shangzhou keywords detected → shangzhou."""
        names = {"西岐", "朝歌", "纣王宫", "姜子牙"}
        assert _detect_era("fantasy", names) == "shangzhou"

    def test_sanguo_keywords(self):
        names = {"荆州", "益州", "曹操营", "刘备府"}
        assert _detect_era("historical", names) == "sanguo"

    def test_shangzhou_keywords(self):
        names = {"西岐", "朝歌", "纣王宫", "姜子牙"}
        assert _detect_era("historical", names) == "shangzhou"

    def test_historical_without_era_keywords(self):
        names = {"汴京", "梁山泊", "东京"}
        assert _detect_era("historical", names) == "historical"

    def test_wuxia_returns_historical(self):
        names = {"华山", "少林寺"}
        assert _detect_era("wuxia", names) == "historical"

    def test_default_genre(self):
        assert _detect_era("", {"some_place"}) == "default"

    def test_sanguo_needs_at_least_2_hits(self):
        """Single keyword match is not enough — need ≥2 for confidence."""
        names = {"刘备府"}  # only 1 hit
        assert _detect_era("historical", names) != "sanguo"


# ── Zhou target tier ────────────────────────────────────────────


class TestZhouTargetTier:
    """_zhou_target_tier maps era to correct tier for 州."""

    def test_sanguo_kingdom(self):
        assert _zhou_target_tier("sanguo") == "kingdom"

    def test_fantasy_kingdom(self):
        assert _zhou_target_tier("fantasy") == "kingdom"

    def test_shangzhou_city(self):
        assert _zhou_target_tier("shangzhou") == "city"

    def test_historical_city(self):
        assert _zhou_target_tier("historical") == "city"

    def test_default_city(self):
        assert _zhou_target_tier("default") == "city"


# ── TierClassifier._multi_feature_refine Rule 7 ────────────────


class TestMultiFeatureRefineZhou:
    """Rule 7: dynasty-aware 州 reclassification in Phase 2."""

    def _refine(self, names_tiers, era="default", parents=None, freqs=None):
        parents = parents or {}
        freqs = freqs or Counter({n: 10 for n in names_tiers})
        children = {}
        return TierClassifier._multi_feature_refine(
            tiers=dict(names_tiers),
            parents=parents,
            frequencies=freqs,
            children_count=children,
            era=era,
        )

    def test_fengshen_jizhou_kingdom_to_city(self):
        """封神: 冀州 kingdom→city (封地, not province)."""
        updates = self._refine({"冀州": "kingdom"}, era="shangzhou")
        assert updates.get("冀州") == "city"

    def test_fengshen_enzhou_kingdom_to_city(self):
        """封神: 恩州 kingdom→city."""
        updates = self._refine({"恩州": "kingdom"}, era="shangzhou")
        assert updates.get("恩州") == "city"

    def test_honglou_pinganzhou_to_city(self):
        """红楼梦: 平安州 kingdom→city (清代行政区划)."""
        updates = self._refine({"平安州": "kingdom"}, era="default")
        assert updates.get("平安州") == "city"

    def test_sanguo_jingzhou_stays_kingdom(self):
        """三国: 荆州 kingdom stays kingdom (省级)."""
        updates = self._refine({"荆州": "kingdom"}, era="sanguo")
        assert "荆州" not in updates  # no change needed

    def test_xiyouji_hongzhou_stays_kingdom(self):
        """西游记(fantasy): 洪州 kingdom stays kingdom."""
        updates = self._refine({"洪州": "kingdom"}, era="fantasy")
        assert "洪州" not in updates

    def test_buzhou_skipped(self):
        """南赡部洲 ends with 部洲, Rule 7 should NOT apply."""
        updates = self._refine({"南赡部洲": "continent"}, era="shangzhou")
        assert "南赡部洲" not in updates

    def test_zero_evidence_zhou_falls_through(self):
        """mc=0 州 should NOT be reclassified by Rule 7 (falls to Rule 2/3)."""
        updates = self._refine(
            {"幽州": "kingdom"}, era="shangzhou",
            freqs=Counter({"幽州": 0}),
        )
        # Rule 7 skipped (mc<2), Rule 2 fires: kingdom→site
        assert updates.get("幽州") == "site"

    def test_single_evidence_zhou_falls_through(self):
        """mc=1, children=0 → Rule 3 applies (single-mention leaf)."""
        updates = self._refine(
            {"幽州": "kingdom"}, era="shangzhou",
            freqs=Counter({"幽州": 1}),
        )
        assert updates.get("幽州") == "site"

    def test_city_to_kingdom_for_sanguo(self):
        """If already city in sanguo era, upgrade to kingdom."""
        updates = self._refine({"益州": "city"}, era="sanguo")
        assert updates.get("益州") == "kingdom"

    def test_already_correct_no_change(self):
        """If tier already matches target, no update emitted."""
        updates = self._refine({"苏州": "city"}, era="default")
        assert "苏州" not in updates


# ── RuleValidator _zhou_expected_tier ───────────────────────────


class TestZhouExpectedTier:
    """RuleValidator: genre+era-aware expected tier for 州 validation."""

    def test_fantasy_no_shangzhou_expects_kingdom(self):
        """Pure fantasy (西游记) → kingdom."""
        assert _zhou_expected_tier("fantasy", {"花果山", "水帘洞"}) == "kingdom"

    def test_fantasy_with_shangzhou_keywords_expects_city(self):
        """封神演义: genre=fantasy but era=shangzhou → city."""
        names = {"西岐", "朝歌", "纣王宫", "姜子牙"}
        assert _zhou_expected_tier("fantasy", names) == "city"

    def test_historical_expects_city(self):
        assert _zhou_expected_tier("historical") == "city"

    def test_wuxia_expects_city(self):
        assert _zhou_expected_tier("wuxia") == "city"

    def test_empty_genre_expects_city(self):
        assert _zhou_expected_tier("") == "city"

    def test_sanguo_keywords_expect_kingdom(self):
        """三国: historical genre + sanguo keywords → kingdom."""
        names = {"荆州", "益州", "曹操营", "刘备府"}
        assert _zhou_expected_tier("historical", names) == "kingdom"


# ── TextVerifier.context() ──────────────────────────────────────


class TestTextVerifierContext:
    """TextVerifier.context() extracts windowed snippets around matches."""

    @pytest.fixture
    def verifier(self, tmp_path):
        """Create a TextVerifier from a small test novel."""
        import sqlite3
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE chapters (novel_id TEXT, chapter_num INT, content TEXT)")
        # Insert test chapters with known content
        chapters = [
            "第一回 话说那大唐国内有一座长安城，乃是天子脚下繁华之地。",
            "第二回 唐三藏自长安城出发，一路西行，经过平安州地界。",
            "第三回 行至苏州城外，见河流纵横，风景秀丽。到了平安州驿站歇息。",
        ]
        for i, text in enumerate(chapters, 1):
            conn.execute(
                "INSERT INTO chapters VALUES (?, ?, ?)",
                ("test-novel", i, text),
            )
        conn.commit()
        conn.close()
        return TextVerifier("test-novel", db)

    def test_exists(self, verifier):
        assert verifier.exists("长安城")
        assert not verifier.exists("花果山")

    def test_count(self, verifier):
        assert verifier.count("长安城") == 2
        assert verifier.count("平安州") == 2
        assert verifier.count("不存在") == 0

    def test_context_returns_snippets(self, verifier):
        snippets = verifier.context("长安城", window=10, max_snippets=5)
        assert len(snippets) == 2
        for s in snippets:
            assert "【长安城】" in s

    def test_context_window_size(self, verifier):
        snippets = verifier.context("苏州", window=5, max_snippets=1)
        assert len(snippets) == 1
        # Window should be small — snippet shouldn't contain full chapter
        assert len(snippets[0]) < 30

    def test_context_max_snippets(self, verifier):
        snippets = verifier.context("平安州", window=10, max_snippets=1)
        assert len(snippets) == 1  # capped at 1

    def test_context_nonexistent(self, verifier):
        snippets = verifier.context("花果山", window=10)
        assert snippets == []

    def test_text_length(self, verifier):
        assert verifier.text_length > 50
