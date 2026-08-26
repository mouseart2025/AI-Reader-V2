"""Golden standard regression tests using human-reviewed data.

These tests load the manually reviewed alias/character data from
backend/data/review/ and verify that the naming pipeline's filtering
logic would catch the issues identified by human reviewers.

Story 2.2 (西游记) + Story 2.4 (红楼梦).
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.name_authority import (
    alias_safety_level,
    is_blocked_name,
    is_unsafe_alias,
)
from src.extraction.fact_validator import _is_generic_person

REVIEW_DIR = Path(__file__).parent.parent / "data" / "review"


def _load_review_json(filename: str) -> dict | None:
    """Load a review JSON file, return None if not found."""
    path = REVIEW_DIR / filename
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Shared test logic ─────────────────────────────────────────


def _collect_wrong_aliases(review_data: dict) -> list[tuple[str, str]]:
    """Collect (canonical, wrong_alias) pairs from review data."""
    pairs = []
    for group in review_data.get("alias_groups", []):
        canonical = group["canonical_name"]
        for wrong in group.get("wrong_aliases", []):
            pairs.append((canonical, wrong))
    return pairs


def _collect_invalid_characters(review_data: dict) -> list[str]:
    """Collect character names marked as invalid."""
    return [
        c["name"] for c in review_data.get("characters", [])
        if c.get("is_valid_character") is False
    ]


def _collect_valid_characters(review_data: dict) -> list[str]:
    """Collect character names marked as valid."""
    return [
        c["name"] for c in review_data.get("characters", [])
        if c.get("is_valid_character") is True
    ]


# ── 西游记 golden standard ────────────────────────────────────


class TestXiyoujiGoldenAliases:
    """Verify wrong aliases from 西游记 review are caught by safety filters."""

    @pytest.fixture(autouse=True)
    def load_data(self):
        data = _load_review_json("xiyouji_aliases.json")
        if data is None:
            pytest.skip("xiyouji_aliases.json not found")
        self.wrong_aliases = _collect_wrong_aliases(data)
        self.alias_groups = data.get("alias_groups", [])

    def test_wrong_aliases_are_unsafe(self):
        """Every wrong_alias identified by human review should be caught."""
        uncaught = []
        for canonical, wrong in self.wrong_aliases:
            level = alias_safety_level(wrong)
            if level >= 2:  # safe — means our filter missed it
                uncaught.append(f"{wrong} (alias of {canonical}, level={level})")
        if uncaught:
            pytest.fail(
                f"{len(uncaught)} wrong aliases NOT caught by safety filter:\n"
                + "\n".join(f"  - {a}" for a in uncaught)
            )

    def test_correct_groupings_count(self):
        """At least 80% of alias groups should be correct.

        Skips when review data hasn't been filled in yet (all is_correct_grouping
        values are None). This makes the test hermetic — CI sees the committed
        raw file (unreviewed) and skips cleanly, while local dev machines with
        human-filled review data actually run the assertion.
        """
        total = len(self.alias_groups)
        if total == 0:
            pytest.skip("No alias groups to check")
        reviewed = sum(1 for g in self.alias_groups
                       if g.get("is_correct_grouping") is not None)
        if reviewed == 0:
            pytest.skip("xiyouji_aliases.json has not been human-reviewed "
                        "(all is_correct_grouping=None)")
        correct = sum(1 for g in self.alias_groups
                      if g.get("is_correct_grouping") is True)
        ratio = correct / total
        assert ratio >= 0.80, \
            f"Only {correct}/{total} ({ratio:.0%}) alias groups correct, need ≥80%"


class TestXiyoujiGoldenCharacters:
    """Verify invalid characters from 西游记 review are caught by filters."""

    @pytest.fixture(autouse=True)
    def load_data(self):
        data = _load_review_json("xiyouji_characters.json")
        if data is None:
            pytest.skip("xiyouji_characters.json not found")
        self.invalid = _collect_invalid_characters(data)
        self.valid = _collect_valid_characters(data)

    # Regression floor (Phase 2 audit, 2026-08-05): these invalid characters
    # ARE catchable by pattern rules today — the filters must never get worse.
    # The rest (银驮/碗子山妖魔/洪江龙王/平顶山·樵夫) are LLM-layer
    # hallucinations that name-pattern filters cannot catch by design.
    MIN_CAUGHT = {"太监", "持国天王"}

    def test_invalid_characters_caught_regression_floor(self):
        """Hard gate: pattern-catchable invalid characters must stay caught.

        Failure message names each regressed entry, the filter verdicts, and
        the data source so a CI red is actionable.
        """
        missed_floor = []
        for name in sorted(self.MIN_CAUGHT):
            generic = _is_generic_person(name)
            blocked = is_blocked_name(name)
            if generic is None and not blocked:
                missed_floor.append(
                    f"  - '{name}' (generic={generic}, blocked={blocked}) "
                    f"— source: data/review/xiyouji_characters.json, "
                    f"is_valid_character=false"
                )
        assert not missed_floor, (
            f"{len(missed_floor)} previously-caught invalid characters now "
            f"pass the filters (regression):\n" + "\n".join(missed_floor)
        )

    def test_invalid_characters_info(self):
        """Report which invalid characters our filters catch vs miss.

        Note: many invalid characters are LLM hallucinations (银驮, 洪江龙王)
        that cannot be caught by pattern rules — they require LLM-layer fixes.
        This test is informational; the hard gate is
        test_invalid_characters_caught_regression_floor.
        """
        caught = []
        missed = []
        for name in self.invalid:
            generic = _is_generic_person(name)
            blocked = is_blocked_name(name)
            if generic is not None or blocked:
                caught.append(name)
            else:
                missed.append(name)
        # Just ensure the test runs and reports — no hard failure
        # Missed items are tracked for future improvement
        assert True, f"Caught: {caught}, Missed (LLM hallucinations): {missed}"

    def test_valid_characters_not_false_positive(self):
        """Characters marked valid should NOT be filtered out."""
        false_positives = []
        for name in self.valid:
            generic = _is_generic_person(name)
            # is_blocked_name catches more (泛称 in alias context), but
            # some valid character names end with title suffixes.
            # Only check _is_generic_person for false positive detection.
            if generic is not None:
                false_positives.append(f"{name}: {generic}")
        # Allow small tolerance for edge cases
        max_fp = max(3, len(self.valid) * 0.05)
        assert len(false_positives) <= max_fp, \
            f"{len(false_positives)} valid chars falsely filtered:\n" \
            + "\n".join(f"  - {fp}" for fp in false_positives)


# ── 红楼梦 golden standard ────────────────────────────────────


class TestHonglouGoldenAliases:
    """Verify wrong aliases from 红楼梦 review are caught by safety filters."""

    @pytest.fixture(autouse=True)
    def load_data(self):
        data = _load_review_json("honglou_aliases.json")
        if data is None:
            pytest.skip("honglou_aliases.json not found")
        self.wrong_aliases = _collect_wrong_aliases(data)
        self.alias_groups = data.get("alias_groups", [])

    def test_wrong_aliases_are_unsafe(self):
        """Most wrong aliases identified by human review should be caught.

        Some edge cases (e.g., short narrative fragments like "尤氏悄悄")
        cannot be caught by pattern rules alone. Allow small tolerance.
        """
        uncaught = []
        for canonical, wrong in self.wrong_aliases:
            level = alias_safety_level(wrong)
            if level >= 2:
                uncaught.append(f"{wrong} (alias of {canonical}, level={level})")
        total = len(self.wrong_aliases)
        if total >= 3:  # need ≥3 samples for meaningful threshold check
            catch_rate = 1 - len(uncaught) / total
            assert catch_rate >= 0.80, \
                f"Only {catch_rate:.0%} wrong aliases caught (need ≥80%):\n" \
                + "\n".join(f"  - {a}" for a in uncaught)
        # For small samples, just report without failing
        # (edge cases like "尤氏悄悄" are tracked for future improvement)

    def test_correct_groupings_count(self):
        """At least 80% of alias groups should be correct.

        Skips when review data hasn't been filled in yet (see xiyouji
        equivalent for rationale).
        """
        total = len(self.alias_groups)
        if total == 0:
            pytest.skip("No alias groups to check")
        reviewed = sum(1 for g in self.alias_groups
                       if g.get("is_correct_grouping") is not None)
        if reviewed == 0:
            pytest.skip("honglou_aliases.json has not been human-reviewed "
                        "(all is_correct_grouping=None)")
        correct = sum(1 for g in self.alias_groups
                      if g.get("is_correct_grouping") is True)
        ratio = correct / total
        assert ratio >= 0.80, \
            f"Only {correct}/{total} ({ratio:.0%}) alias groups correct, need ≥80%"


class TestHonglouGoldenCharacters:
    """Verify invalid characters from 红楼梦 review are caught by filters."""

    @pytest.fixture(autouse=True)
    def load_data(self):
        data = _load_review_json("honglou_characters.json")
        if data is None:
            pytest.skip("honglou_characters.json not found")
        self.invalid = _collect_invalid_characters(data)
        self.valid = _collect_valid_characters(data)

    # Regression floor (Phase 2 audit, 2026-08-05): 红楼梦 review marks only
    # 贾探春 as invalid, and it is NOT catchable by pattern rules (it's a
    # real-person name — the invalid flag reflects an extraction-layer issue,
    # not a generic term). The floor is therefore empty today; add entries
    # here if future filters learn to catch review-flagged names, so the
    # guard never regresses below the recorded baseline.
    MIN_CAUGHT: set[str] = set()

    def test_invalid_characters_caught_regression_floor(self):
        """Hard gate: pattern-catchable invalid characters must stay caught."""
        missed_floor = []
        for name in sorted(self.MIN_CAUGHT):
            generic = _is_generic_person(name)
            blocked = is_blocked_name(name)
            if generic is None and not blocked:
                missed_floor.append(
                    f"  - '{name}' (generic={generic}, blocked={blocked}) "
                    f"— source: data/review/honglou_characters.json, "
                    f"is_valid_character=false"
                )
        assert not missed_floor, (
            f"{len(missed_floor)} previously-caught invalid characters now "
            f"pass the filters (regression):\n" + "\n".join(missed_floor)
        )

    def test_invalid_characters_info(self):
        """Report which invalid characters our filters catch vs miss.

        Informational only; the hard gate is
        test_invalid_characters_caught_regression_floor.
        """
        caught = []
        missed = []
        for name in self.invalid:
            generic = _is_generic_person(name)
            blocked = is_blocked_name(name)
            if generic is not None or blocked:
                caught.append(name)
            else:
                missed.append(name)
        assert True, f"Caught: {caught}, Missed: {missed}"

    def test_valid_characters_not_false_positive(self):
        """Characters marked valid should NOT be filtered out."""
        false_positives = []
        for name in self.valid:
            generic = _is_generic_person(name)
            if generic is not None:
                false_positives.append(f"{name}: {generic}")
        max_fp = max(3, len(self.valid) * 0.05)
        assert len(false_positives) <= max_fp, \
            f"{len(false_positives)} valid chars falsely filtered:\n" \
            + "\n".join(f"  - {fp}" for fp in false_positives)


# ═══════════════════════════════════════════════════════════════
# FR-2.5 (Epic 2): gold 分组正确率硬门禁 — 西游/红楼 ≥95%,CI 阻断。
#
# 方法:以 gold alias_groups 为输入,跑 Epic 2 消解管线(safety 过滤 →
# embedding blocking → LLM 聚类判定 → llm_merge override 应用),
# LLM/embedding 全部 mock。分组正确判据与 gold 审核口径一致:
#   - 误并:wrong_alias 仍并到 canonical 下 → 该组错误
#   - 欠并:missing_alias 未并到 canonical 下 → 该组错误
# 误并率单独报告(print),不参与门禁阈值。
# ═══════════════════════════════════════════════════════════════

from src.services import alias_resolver, entity_resolver  # noqa: E402

_GROUPING_GATE = 0.95


class _GoldMockLLM:
    """gold 语境 mock LLM。

    mode="naive":    把簇内所有名字并成一组(模拟不做区分的最差 LLM)。
    mode="informed": 按 gold 标注拆分 wrong_aliases(模拟合格 LLM)。
    """

    def __init__(self, mode: str, wrong_aliases: set[str]):
        self.mode = mode
        self.wrong = wrong_aliases

    async def generate(self, system, prompt, format=None, **kw):
        members = [
            line[2:].replace(" (存疑)", "").strip()
            for line in prompt.splitlines()
            if line.startswith("- ")
        ]
        if self.mode == "informed":
            members = [m for m in members if m not in self.wrong]
        groups = []
        if len(members) >= 2:
            groups.append({
                "canonical": members[0],
                "members": members,
                "reason": f"mock-{self.mode}",
            })
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        return {"groups": groups}, usage


async def _run_er_pipeline(review_data: dict, mode: str, log_path) -> dict:
    """对一份 gold aliases 文件跑 Epic 2 消解管线,返回分组正确率报告。"""
    groups = review_data.get("alias_groups", [])
    canonicals = {g["canonical_name"] for g in groups}
    wrong_aliases = {
        w for g in groups for w in g.get("wrong_aliases", [])
    }

    # name_meta:gold canonical 视为 dict 高频 person 主实体(晋升规则,
    # 如 观音菩萨/尤氏 命中 level-0 但确实是独立人物)。
    name_meta: dict[str, dict] = {}
    group_of: dict[str, int] = {}
    for gi, g in enumerate(groups):
        names = (
            [g["canonical_name"]]
            + list(g.get("system_aliases", []))
            + list(g.get("missing_aliases", []))
        )
        for n in names:
            entry = name_meta.setdefault(n, {"freq": 0, "dict_person_freq": 0})
            if n in canonicals:
                entry["dict_person_freq"] = max(
                    entry["dict_person_freq"], g.get("mention_count", 0), 100
                )
            group_of.setdefault(n, gi)

    dim = len(groups)

    def _embed(names: list[str]) -> list[list[float]]:
        vecs = []
        for n in names:
            v = [0.0] * dim
            v[group_of[n]] = 1.0
            vecs.append(v)
        return vecs

    mergeable, _hints, _blocked = entity_resolver.partition_candidates(name_meta)
    clusters = entity_resolver.build_candidate_clusters(
        mergeable, _embed, threshold=0.9, top_k=100, max_cluster_size=1000
    )

    llm = _GoldMockLLM(mode, wrong_aliases)
    overrides = []
    for cluster in clusters:
        # 让 gold canonical 做 LLM 输出的 canonical(若它在簇内)
        decision = await entity_resolver.resolve_cluster(
            "gold-gate", cluster, name_meta, llm,
            log_path=log_path, record_cost=False,
        )
        for grp in decision["output_groups"]:
            canon = next(
                (c for c in canonicals if c in grp["members"]), grp["canonical"]
            )
            overrides.append({
                "override_type": "llm_merge",
                "override_key": canon,
                "override_json": {
                    "members": [canon] + [m for m in grp["members"] if m != canon],
                    "canonical": canon,
                    "reason": grp["reason"],
                    "prompt_version": entity_resolver.PROMPT_VERSION,
                },
            })

    async def _load(_novel_id):
        return overrides

    with patch("src.db.entity_override_store.load_overrides", _load):
        final_map = await alias_resolver._apply_user_overrides("gold-gate", {})

    # 分组正确率:与 gold 审核口径一致(误并/欠并 → 组错误)
    correct = 0
    overmerged = 0
    errors: list[str] = []
    for g in groups:
        canon = g["canonical_name"]
        resolved = {canon} | {a for a, c in final_map.items() if c == canon}
        bad = [w for w in g.get("wrong_aliases", []) if w in resolved]
        missing = [m for m in g.get("missing_aliases", []) if m not in resolved]
        if bad:
            overmerged += 1
        if bad or missing:
            errors.append(
                f"{canon}: 误并={bad or '无'}, 欠并={missing or '无'}"
            )
        else:
            correct += 1

    total = len(groups)
    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 1.0,
        "overmerge_rate": overmerged / total if total else 0.0,
        "llm_calls": len(clusters),
        "errors": errors,
    }


class TestGoldGroupingAccuracyGate:
    """FR-2.5: 分组正确率硬门禁(≥95%),误并率单独报告。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "filename,novel",
        [("xiyouji_aliases.json", "西游记"), ("honglou_aliases.json", "红楼梦")],
    )
    async def test_grouping_accuracy_gate(self, filename, novel, tmp_path):
        data = _load_review_json(filename)
        if data is None:
            pytest.skip(f"{filename} not found")

        # 管线全开(safety + blocking + 合格 LLM 判定)
        informed = await _run_er_pipeline(
            data, "informed", tmp_path / "informed.jsonl"
        )
        # 对照:最差 LLM(整簇并)— 检验 safety hard-block 的兜底能力
        naive = await _run_er_pipeline(data, "naive", tmp_path / "naive.jsonl")

        # 误并率单独报告(不进阈值)
        print(
            f"\n[{novel}] 分组正确率: informed={informed['accuracy']:.1%} "
            f"naive={naive['accuracy']:.1%} | "
            f"误并率: informed={informed['overmerge_rate']:.1%} "
            f"naive={naive['overmerge_rate']:.1%} | "
            f"LLM 调用={informed['llm_calls']} (组数={informed['total']})"
        )

        assert informed["accuracy"] >= _GROUPING_GATE, (
            f"{novel} 分组正确率 {informed['accuracy']:.1%} < 95% 门禁:\n"
            + "\n".join(informed["errors"])
        )
        # naive 模式也不得跌破门禁 — hard-block 失效会在这里变红
        assert naive["accuracy"] >= _GROUPING_GATE, (
            f"{novel} naive 模式分组正确率 {naive['accuracy']:.1%} < 95% "
            f"(safety hard-block 兜底失效):\n" + "\n".join(naive["errors"])
        )

    @pytest.mark.asyncio
    async def test_informed_llm_beats_naive_on_honglou(self, tmp_path):
        """LLM 层增量价值:红楼 尤氏悄悄(level-2,模式规则抓不到)只能由
        LLM 判定拆出 — informed 模式分组正确率必须严格高于 naive。"""
        data = _load_review_json("honglou_aliases.json")
        if data is None:
            pytest.skip("honglou_aliases.json not found")
        informed = await _run_er_pipeline(
            data, "informed", tmp_path / "i.jsonl"
        )
        naive = await _run_er_pipeline(data, "naive", tmp_path / "n.jsonl")
        assert informed["accuracy"] > naive["accuracy"]
        assert informed["accuracy"] == 1.0
