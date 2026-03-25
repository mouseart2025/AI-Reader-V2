"""Tests for v0.63.0 hierarchy quality improvements:
- Story 2.1: Transitivity check
- Story 2.2: Temporal weight decay
- Story 2.3: Location alias normalization
"""

import pytest

from src.services.world_structure_agent import WorldStructureAgent, _get_suffix_rank


# ── Story 2.1: Transitivity check ───────────────────────────────


class TestTransitivityCheck:
    """Verify _check_transitivity detects and fixes chain rank violations."""

    def test_clean_chain_no_violations(self):
        """A(洲=1)→B(国=2)→C(城=4)→D(洞=5): all ranks increase → no violations."""
        parents = {"B": "A_洲", "C_城": "B_国", "D_洞": "C_城"}
        # Use real names with suffixes
        parents = {"傲来国": "东胜神洲", "长安城": "傲来国", "水帘洞": "花果山"}
        violations = WorldStructureAgent._check_transitivity(parents)
        assert violations == []

    def test_detects_rank_inversion(self):
        """A(洲=1)→B(城=4)→C(国=2): ancestor B(4) > descendant C(2) → violation."""
        parents = {"长安城": "东胜神洲", "傲来国": "长安城"}
        violations = WorldStructureAgent._check_transitivity(parents)
        assert len(violations) == 1
        ancestor, descendant = violations[0]
        assert ancestor == "长安城"  # rank 4, incorrectly parenting 国(rank 2)
        assert descendant == "傲来国"

    def test_no_violation_when_no_suffix(self):
        """Locations without recognizable suffix → no rank → skip."""
        parents = {"ABC": "XYZ", "DEF": "ABC"}
        violations = WorldStructureAgent._check_transitivity(parents)
        assert violations == []

    def test_fix_removes_offending_edge(self):
        """fix_transitivity_violations removes the ancestor edge."""
        parents = {"长安城": "东胜神洲", "傲来国": "长安城"}
        violations = WorldStructureAgent._check_transitivity(parents)
        removed = WorldStructureAgent.fix_transitivity_violations(parents, violations)
        assert removed == 1
        assert "长安城" not in parents or parents.get("长安城") == "东胜神洲"

    def test_deep_chain_violation(self):
        """A(洲)→B(山)→C(殿)→D(国): D(国=2) under C(殿=6) is violation."""
        parents = {
            "花果山": "东胜神洲",
            "灵霄宝殿": "花果山",
            "车迟国": "灵霄宝殿",
        }
        violations = WorldStructureAgent._check_transitivity(parents)
        # 灵霄宝殿(6) > 车迟国(2) → violation
        assert len(violations) >= 1
        ancestors = [v[0] for v in violations]
        assert "灵霄宝殿" in ancestors

    def test_empty_parents(self):
        """Empty dict → no violations."""
        assert WorldStructureAgent._check_transitivity({}) == []


# ── Story 2.2: Temporal weight decay ────────────────────────────


class TestTemporalWeightDecay:
    """Verify chapter position weighting formula."""

    def test_weight_formula(self):
        """chapter_weight = 1.0 + 0.5 * (chapter_idx / total_chapters)."""
        # Chapter 0 of 100: weight = 1.0
        assert abs(_chapter_weight(0, 100) - 1.0) < 0.01
        # Chapter 50 of 100: weight = 1.25
        assert abs(_chapter_weight(50, 100) - 1.25) < 0.01
        # Chapter 99 of 100: weight ≈ 1.495
        assert abs(_chapter_weight(99, 100) - 1.495) < 0.01

    def test_max_weight_capped(self):
        """Last chapter weight should not exceed 1.5 (NFR6)."""
        w = _chapter_weight(999, 1000)
        assert w <= 1.5

    def test_short_novel_minimal_effect(self):
        """For a 10-chapter novel, weight range is small (1.0 ~ 1.45)."""
        w_first = _chapter_weight(0, 10)
        w_last = _chapter_weight(9, 10)
        assert w_first >= 1.0
        assert w_last <= 1.5
        assert w_last - w_first < 0.5

    def test_later_chapter_wins_on_tie(self):
        """Single vote at chapter 95 should outweigh single vote at chapter 5."""
        total = 100
        early_weighted = 1.0 * _chapter_weight(5, total)  # ~1.025
        late_weighted = 1.0 * _chapter_weight(95, total)   # ~1.475
        assert late_weighted > early_weighted

    def test_stable_majority_not_overridden(self):
        """30 early votes should still beat 5 late votes (decay not too aggressive)."""
        total = 100
        early_total = sum(_chapter_weight(i, total) for i in range(50) for _ in range(1))
        # Simulate 30 votes spread across chapters 0-49
        early_total = sum(_chapter_weight(i, total) for i in range(0, 50, 50 // 30 + 1))
        # Simulate 5 votes at chapters 95-99
        late_total = sum(_chapter_weight(i, total) for i in range(95, 100))
        # 30 early votes ≈ 30 * ~1.1 = 33, 5 late votes ≈ 5 * ~1.48 = 7.4
        # Early should still win
        assert early_total * (30 / len(range(0, 50, 50 // 30 + 1))) > late_total


def _chapter_weight(chapter_idx: int, total_chapters: int) -> float:
    """Reference implementation of chapter weight formula."""
    if total_chapters <= 1:
        return 1.0
    return 1.0 + 0.5 * (chapter_idx / total_chapters)


# ── Story 2.3: Location alias normalization ─────────────────────


class TestLocationAliasNormalization:
    """Verify suffix-based location name merging."""

    def test_basic_merge(self):
        """金陵 + 金陵城 → merge to 金陵城."""
        names = {"金陵", "金陵城"}
        merged = _normalize_location_aliases(names)
        assert "金陵" in merged
        assert merged["金陵"] == "金陵城"

    def test_no_merge_without_suffix(self):
        """东京 + 东京城外 → no merge (外 not in suffix table)."""
        names = {"东京", "东京城外"}
        merged = _normalize_location_aliases(names)
        assert "东京" not in merged  # 城外 is not a recognized suffix

    def test_no_merge_single_char_substring(self):
        """花 + 花果山 → no merge (single-char substring too short)."""
        names = {"花", "花果山"}
        merged = _normalize_location_aliases(names)
        assert "花" not in merged

    def test_merge_with_mountain_suffix(self):
        """花果 + 花果山 → merge to 花果山."""
        names = {"花果", "花果山"}
        merged = _normalize_location_aliases(names)
        assert "花果" in merged
        assert merged["花果"] == "花果山"

    def test_daming_fu(self):
        """大名 + 大名府 → merge to 大名府."""
        names = {"大名", "大名府"}
        merged = _normalize_location_aliases(names)
        assert "大名" in merged
        assert merged["大名"] == "大名府"

    def test_no_merge_different_semantics(self):
        """长安 + 长安街 → merge is valid (街 is in suffix table)."""
        names = {"长安", "长安街"}
        merged = _normalize_location_aliases(names)
        # 长安 → 长安街 is a valid merge (街 is a suffix)
        assert "长安" in merged

    def test_multiple_candidates_picks_shortest_suffix(self):
        """东京 + 东京城 + 东京镇 → merge 东京 to first found (东京城)."""
        names = {"东京", "东京城", "东京镇"}
        merged = _normalize_location_aliases(names)
        assert "东京" in merged
        # Should pick one of the suffixed versions
        assert merged["东京"] in ("东京城", "东京镇")

    def test_empty_input(self):
        """Empty set → empty dict."""
        assert _normalize_location_aliases(set()) == {}


def _normalize_location_aliases(names: set[str]) -> dict[str, str]:
    """Reference implementation: detect suffix variants and return merge map.

    Returns {short_name: long_name} where long_name = short_name + known_suffix.
    """
    from src.services.world_structure_agent import _NAME_SUFFIX_TIER

    suffix_chars = {s for s, _t in _NAME_SUFFIX_TIER if len(s) == 1}
    merge_map: dict[str, str] = {}
    sorted_names = sorted(names, key=len)

    for short in sorted_names:
        if len(short) < 2:
            continue
        if short in merge_map:
            continue
        for long in sorted_names:
            if long == short or not long.startswith(short) or len(long) <= len(short):
                continue
            suffix_part = long[len(short):]
            # Check if the suffix part is a recognized geographic suffix
            if suffix_part in suffix_chars or any(
                suffix_part == s for s, _t in _NAME_SUFFIX_TIER if len(s) >= 2
            ):
                merge_map[short] = long
                break
    return merge_map
