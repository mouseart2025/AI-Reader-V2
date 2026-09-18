"""GeoEvolve 阶段 3 测试（prompt_evolve.py + run_loop 阶段 3 部分）。

覆盖：
  - 段落手术：split/replace 逐字节往返、标记缺失/不唯一报错、heal 自愈
  - 提议器输出校验（畸形 JSON、缺字段、恒等、超长、越界、删锚点）
  - anti-hack 黄金答案串检测（prompt 适配版：原段落已有示例豁免）
  - 快速层指标（recall 的 T 子集口径、inflation、generic_rate）
  - min_improvement 显著性下限（archive 噪声内改善拒绝）

纯本地：不触 LLM、不读真实 DB。真实 prompt 文件只读。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

_EVOLVE_DIR = Path(__file__).resolve().parent.parent / "scripts" / "evolve"
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_EVOLVE_DIR), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import prompt_evolve as pe  # noqa: E402
import run_loop as rl  # noqa: E402

REAL_TEXT = pe.PROMPT_FILE.read_text(encoding="utf-8")


# ── 段落手术 ─────────────────────────────────────────────────────────

class TestSectionSurgery:
    def test_split_real_file(self):
        head, section, tail = pe.split_section(REAL_TEXT)
        assert section.startswith(pe.SECTION_BEGIN)
        assert not tail.startswith(pe.SECTION_BEGIN.replace("## ", ""))
        assert head + section + tail == REAL_TEXT
        assert "宁多勿漏" in section

    def test_replace_and_restore_byte_exact(self):
        _head, section, _tail = pe.split_section(REAL_TEXT)
        mutated = section + "999. 测试新规则\n"
        new_text = pe.replace_section(REAL_TEXT, mutated)
        assert "999. 测试新规则" in new_text
        # 还原逐字节一致
        assert pe.replace_section(new_text, section) == REAL_TEXT

    def test_missing_marker_raises(self):
        with pytest.raises(pe.SectionError):
            pe.split_section("没有标记的文本")

    def test_duplicate_marker_raises(self):
        dup = REAL_TEXT + "\n" + pe.SECTION_BEGIN + "\n"
        with pytest.raises(pe.SectionError):
            pe.split_section(dup)

    def test_heal_restores_tampered_file(self, tmp_path: Path, monkeypatch):
        fake = tmp_path / "extraction_system.txt"
        fake.write_text(REAL_TEXT, encoding="utf-8")
        monkeypatch.setattr(pe, "PROMPT_FILE", fake)
        monkeypatch.setattr(pe, "STATE_PATH", tmp_path / "prompt_state.json")
        state = pe.load_state()
        assert state["override_section"] is None
        assert pe.heal_prompt_file(state) is False  # 未污染 → 不修复
        # 污染文件 → heal 修复
        _h, section, _t = pe.split_section(REAL_TEXT)
        fake.write_text(pe.replace_section(REAL_TEXT, section + "垃圾追加\n"),
                        encoding="utf-8")
        assert pe.heal_prompt_file(state) is True
        assert fake.read_text(encoding="utf-8") == REAL_TEXT

    def test_override_roundtrip(self, tmp_path: Path, monkeypatch):
        fake = tmp_path / "extraction_system.txt"
        fake.write_text(REAL_TEXT, encoding="utf-8")
        monkeypatch.setattr(pe, "PROMPT_FILE", fake)
        monkeypatch.setattr(pe, "STATE_PATH", tmp_path / "prompt_state.json")
        state = pe.load_state()
        _h, section, _t = pe.split_section(REAL_TEXT)
        override = section.replace("宁多勿漏", "宁多勿漏（强化）", 1)
        pe.apply_section(state, override)
        state["override_section"] = override
        pe.save_state(state)
        assert "（强化）" in fake.read_text(encoding="utf-8")
        # 回退
        state["override_section"] = None
        pe.heal_prompt_file(state)
        assert fake.read_text(encoding="utf-8") == REAL_TEXT


# ── 提议器输出校验 ───────────────────────────────────────────────────

ORIGINAL = pe.split_section(REAL_TEXT)[1]


def _valid(new_section: str | None = None) -> dict:
    return {"hypothesis": "补充 XX 类地名规则以提高召回",
            "new_section": new_section or (ORIGINAL + "999. 补充规则示例\n")}


class TestProposalValidation:
    def test_valid_passes(self):
        out = pe.validate_proposal(_valid(), ORIGINAL)
        assert "999. 补充规则示例" in out

    def test_not_dict_rejected(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal(["不是", "对象"], ORIGINAL)

    def test_missing_hypothesis(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal({"new_section": "x"}, ORIGINAL)

    def test_missing_new_section(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal({"hypothesis": "x"}, ORIGINAL)

    def test_identity_rejected(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal(_valid(ORIGINAL), ORIGINAL)

    def test_oversize_rejected(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal(_valid(ORIGINAL * 3), ORIGINAL)

    def test_wrong_start_marker(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal(_valid("## 错误标题\n内容\n"), ORIGINAL)

    def test_section_escape_rejected(self):
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal(_valid(ORIGINAL + pe.SECTION_END + "\n越界\n"),
                                 ORIGINAL)

    def test_anchor_deletion_rejected(self):
        # 删掉"不要提取泛化地理词"锚点 → 拒
        bad = ORIGINAL.replace("不要提取泛化地理词", "可提取泛化地理词")
        with pytest.raises(pe.ProposalError):
            pe.validate_proposal(_valid(bad), ORIGINAL)


# ── anti-hack（prompt 适配）──────────────────────────────────────────

class TestAntiHackPrompt:
    def test_golden_name_added_flagged(self):
        golden = {"雷音寺", "花果山"}
        new = ORIGINAL + "例：雷音寺的 parent 是西天\n"
        # 花果山在原段落已有(示例) → 豁免;雷音寺是新增答案串 → 命中
        flagged = pe.anti_hack_prompt_check(new, ORIGINAL, golden)
        assert flagged == ["雷音寺"]

    def test_existing_example_exempt(self):
        golden = {"花果山"}
        assert pe.anti_hack_prompt_check(ORIGINAL, ORIGINAL, golden) == []

    def test_non_golden_word_ok(self):
        flagged = pe.anti_hack_prompt_check(ORIGINAL + "例：某无名山头\n",
                                            ORIGINAL, {"花果山"})
        assert flagged == []

    def test_real_golden_names_nonempty(self):
        names = pe.load_golden_names()
        assert "花果山" in names and len(names) > 100


# ── 快速层指标 ───────────────────────────────────────────────────────

class TestFastLayerMetrics:
    CHAPTERS: ClassVar[dict] = {"xiyouji": [4, 15], "honglou": [4], "shuihu": [4]}

    def _fixtures(self):
        t_set = {
            "xiyouji": {"chapters": {"4": ["花果山", "水帘洞", "东海"],
                                     "15": ["南天门"], "95": ["灵山"]}},
            "honglou": {"chapters": {"4": ["大观园", "怡红院"]}},
            "shuihu": {"chapters": {"4": ["梁山泊"]}},
        }
        e0 = {"xiyouji": {"names": ["花果山"]},
              "honglou": {"names": ["大观园"]},
              "shuihu": {"names": ["梁山泊"]}}
        extracted = {
            "xiyouji": {4: ["花果山", "水帘洞"], 15: ["南天门"]},
            "honglou": {4: ["大观园", "怡红院", "客栈"]},  # 客栈=泛称
            "shuihu": {4: ["梁山泊"]},
        }
        return t_set, e0, extracted

    def test_recall_uses_subset_t(self):
        t_set, e0, extracted = self._fixtures()
        m = pe.fast_layer_metrics(extracted, t_set, e0, self.CHAPTERS)
        # xiyouji: 子集 T = {花果山,水帘洞,东海,南天门}(不含 ch95 的灵山)
        # E' = {花果山,水帘洞,南天门} → recall 3/4
        assert m["xiyouji"]["prompt.recall"] == pytest.approx(0.75)
        assert m["honglou"]["prompt.recall"] == 1.0
        # inflation: honglou E' 3 名 vs E0 1 名
        assert m["honglou"]["prompt.count_inflation"] == 3.0
        # generic: 客栈 命中泛称 → 1/3
        assert m["honglou"]["prompt.generic_rate"] == pytest.approx(1 / 3)

    def test_without_chapters_fixture_uses_full_t(self):
        t_set, e0, extracted = self._fixtures()
        m = pe.fast_layer_metrics(extracted, t_set, e0)
        # 全量 T 含 ch95 灵山 → 3/5
        assert m["xiyouji"]["prompt.recall"] == pytest.approx(0.6)


# ── min_improvement 显著性下限 ───────────────────────────────────────

class TestMinImprovement:
    POLICY: ClassVar[dict] = {
        "thresholds": {"metric_regression_abs": 0.01,
                       "per_novel_regression_abs": 0.01,
                       "golden_pass_rate_min": 1.0,
                       "min_improvement": {"prompt.recall": 0.01}},
    }

    def _candidate(self, metrics, parent):
        return {"generation": 1, "operator": "prompt_gepa_reflect",
                "genome_diff": {}, "metrics": metrics, "parent_metrics": parent}

    def test_noise_level_improvement_rejected(self):
        parent = {"xiyouji.prompt.recall": 0.40}
        cand = {"xiyouji.prompt.recall": 0.405}  # +0.005 < 0.01 噪声内
        frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8, self.POLICY)
        assert decision == "rejected_no_improvement"
        assert frontier == []

    def test_significant_improvement_archived(self):
        parent = {"xiyouji.prompt.recall": 0.40}
        cand = {"xiyouji.prompt.recall": 0.42}  # +0.02 > 0.01
        _frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8, self.POLICY)
        assert decision == "archived"

    def test_other_metrics_still_strict(self):
        # min_improvement 只约束 prompt.recall;其它键任意严格改善即可
        parent = {"xiyouji.prompt.recall": 0.40,
                  "xiyouji.prompt.generic_rate": 0.05}
        cand = {"xiyouji.prompt.recall": 0.40,
                "xiyouji.prompt.generic_rate": 0.048}  # 泛称率降(改善)
        _frontier, decision = rl.archive_candidate(
            [], self._candidate(cand, parent), 8, self.POLICY)
        assert decision == "archived"


# ── 父代向量(E0 fixture → vec)───────────────────────────────────────

class TestParentVec:
    def test_fixture_mapping(self):
        e0 = {"novels": {
            "xiyouji": {"recall_a": 0.35, "recall_b": 0.37,
                        "generic_rate_a": 0.05, "generic_rate_b": 0.07},
            "honglou": {"recall_a": 0.40, "recall_b": 0.42,
                        "generic_rate_a": 0.10, "generic_rate_b": 0.10},
            "shuihu": {"recall_a": 0.55, "recall_b": 0.55,
                        "generic_rate_a": 0.03, "generic_rate_b": 0.03},
        }}
        vec = rl._stage3_parent_vec_from_fixture(e0)
        # v5 口径:A/B 双跑均值
        assert vec["xiyouji.prompt.recall"] == pytest.approx(0.36)
        assert vec["xiyouji.prompt.count_inflation"] == 1.0
        assert vec["shuihu.prompt.generic_rate"] == 0.03
        assert vec["xiyouji.prompt.generic_rate"] == pytest.approx(0.06)
        assert vec["macro.prompt.recall"] == pytest.approx((0.36 + 0.41 + 0.55) / 3)

    def test_directions_registered(self):
        assert rl.direction_for("xiyouji.prompt.recall") == "higher"
        assert rl.direction_for("xiyouji.prompt.count_inflation") == "lower"
        assert rl.direction_for("xiyouji.prompt.generic_rate") == "lower"


# ── v5:快速层 repeats 合并 + 三次复测确认 ────────────────────────────

class TestEvalRepeatsMerge:
    def test_recall_mean_guards_worst(self, monkeypatch):
        import asyncio

        import prompt_evolve as pe

        runs = iter([
            {"xiyouji": {4: ["花果山", "水帘洞"]}, "honglou": {4: ["大观园"]},
             "shuihu": {4: ["梁山泊"]}},
            {"xiyouji": {4: ["花果山"]}, "honglou": {4: ["大观园", "客栈"]},
             "shuihu": {4: ["梁山泊"]}},
        ])
        monkeypatch.setattr(pe, "extract_subset",
                            lambda *a, **k: _a(next(runs)))
        monkeypatch.setattr(pe, "build_system_prompt", lambda *a, **k: "sys")

        async def _a(v):
            return v

        t_set = {"xiyouji": {"chapters": {"4": ["花果山", "水帘洞"]}},
                 "honglou": {"chapters": {"4": ["大观园", "怡红院"]}},
                 "shuihu": {"chapters": {"4": ["梁山泊"]}}}
        fixtures = {"genres": {"xiyouji": "", "honglou": "", "shuihu": ""},
                    "chapters": {"xiyouji": [4], "honglou": [4], "shuihu": [4]},
                    "t_set": t_set,
                    "e0": {"novels": {"xiyouji": {"names": ["花果山"]},
                                      "honglou": {"names": ["大观园"]},
                                      "shuihu": {"names": ["梁山泊"]}}}}
        state = {"override_section": None,
                 "original_section": pe.split_section(
                     pe.PROMPT_FILE.read_text(encoding="utf-8"))[1]}
        cost = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        res = asyncio.run(rl._eval_stage3_candidate(state, fixtures, cost,
                                                    None, repeats=2))
        # xiyouji recall: 1.0 与 0.5 → 均值 0.75
        assert res["metrics"]["xiyouji"]["prompt.recall"] == pytest.approx(0.75)
        # honglou generic: run1 0/1, run2 1/2(客栈泛称) → 护栏取最差 0.5
        assert res["metrics"]["honglou"]["prompt.generic_rate"] == pytest.approx(0.5)


class TestConfirmRule:
    POLICY: ClassVar[dict] = {
        "thresholds": {"metric_regression_abs": 0.01,
                       "per_novel_regression_abs": 0.045,
                       "golden_pass_rate_min": 1.0,
                       "min_improvement": {"prompt.recall": 0.045,
                                           "macro.prompt.recall": 0.02},
                       "guard_overrides": {"prompt.recall": {"abs": 0.045}}},
        "metrics": {"prompt_fast_layer": {"confirm_repeats": 3}},
    }

    def _run_confirm(self, monkeypatch, run_vecs):
        import asyncio

        runs = iter(run_vecs)

        async def fake_eval(*a, **k):
            return {"metrics": next(runs), "extracted": {}}

        monkeypatch.setattr(rl, "_eval_stage3_candidate", fake_eval)
        parent = {"xiyouji.prompt.recall": 0.40, "macro.prompt.recall": 0.43}
        return asyncio.run(rl._confirm_stage3_candidate(
            {"override_section": None, "original_section": "x"}, "new",
            None, parent, self.POLICY, {"cost_usd": 0}, None, 1))

    def _vec(self, xiyouji_recall):
        # 快速层 per-novel 结构(macro 由 _stage3_vec_from_metrics 计算)
        return {"xiyouji": {"prompt.recall": xiyouji_recall},
                "honglou": {"prompt.recall": 0.45},
                "shuihu": {"prompt.recall": 0.45}}

    def test_confirmed_when_median_exceeds(self, monkeypatch):
        c = self._run_confirm(monkeypatch,
                              [self._vec(0.50), self._vec(0.48), self._vec(0.52)])
        assert c["confirmed"]
        assert "xiyouji.prompt.recall" in c["confirmed_keys"]

    def test_rejected_when_median_within_noise(self, monkeypatch):
        # 单次 0.50 超阈但中位数 0.42 不超 → 拒绝(gen64 情形)
        c = self._run_confirm(monkeypatch,
                              [self._vec(0.50), self._vec(0.42), self._vec(0.41)])
        assert not c["confirmed"]
        assert "中位数无超阈改善" in c["reason"]

    def test_rejected_when_any_run_regresses(self, monkeypatch):
        c = self._run_confirm(monkeypatch,
                              [self._vec(0.50), self._vec(0.33), self._vec(0.52)])
        assert not c["confirmed"]
        assert "回归" in c["reason"]
