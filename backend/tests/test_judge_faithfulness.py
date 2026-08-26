"""FR-3.2/FR-3.3 judge 脚本单元测试。

全部使用构造数据 + mock LLM,不打真实 API、不读真实 DB/IAA 文件。
加载方式对齐 test_quality_dashboard.py 的 importlib 模式。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCRIPT = _BACKEND_DIR / "scripts" / "judge_extraction_faithfulness.py"
_spec = importlib.util.spec_from_file_location("judge_extraction_faithfulness", _SCRIPT)
jf = importlib.util.module_from_spec(_spec)
sys.modules["judge_extraction_faithfulness"] = jf
_spec.loader.exec_module(jf)


def _run(coro):
    """运行协程并恢复主线程 event loop(对齐 test_quality_dashboard)。"""
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop(asyncio.new_event_loop())


class MockLLM:
    """Mock LLM:按 prompt 内容返回排队响应,缺省返回满分评分。"""

    def __init__(self, judge_response: dict | None = None,
                 calibrate_response: dict | None = None):
        self.judge_response = judge_response or {
            "precision": {"score": 0.9, "reason": "均有原文支持"},
            "faithfulness": {"score": 0.8, "reason": "evidence 基本可定位"},
            "comprehensiveness": {"score": 0.7, "reason": "漏了一条次要关系"},
            "item_verdicts": [
                {"label": "1", "supported": True, "reason": "有支持"},
                {"label": "2", "supported": False, "reason": "原文无此互动"},
            ],
        }
        self.calibrate_response = calibrate_response
        self.prompts: list[str] = []

    async def generate(self, system, prompt, format=None, temperature=0.0,
                       max_tokens=4096, timeout=300, num_ctx=None):
        self.prompts.append(prompt)
        from src.infra.llm_client import LlmUsage
        if "论断" in prompt and self.calibrate_response is not None:
            return self.calibrate_response, LlmUsage(10, 5, 15)
        return self.judge_response, LlmUsage(100, 50, 150)


CHAPTER_TEXT = "宋江与武松结拜为义兄弟。两人把酒言欢。宋江辞别柴进上路。"

FACT = {
    "relationships": [
        {"person_a": "宋江", "person_b": "武松", "relation_type": "结拜兄弟",
         "evidence": "宋江与武松结拜为义兄弟"},
        {"person_a": "宋江", "person_b": "柴进", "relation_type": "朋友",
         "evidence": "原文根本没有的句子"},
    ],
    "events": [
        {"summary": "宋江辞别柴进", "type": "旅行", "evidence": "宋江辞别柴进上路"},
        {"summary": "武松打虎", "type": "战斗", "evidence": ""},
    ],
}


# ── 纯函数 ──


def test_build_judge_items():
    items = jf.build_judge_items(FACT)
    assert len(items) == 4
    assert items[0]["kind"] == "relationship" and "宋江" in items[0]["label"]
    assert items[2]["kind"] == "event"


def test_check_spans_locally():
    items = jf.build_judge_items(FACT)
    check = jf.check_spans_locally(items, CHAPTER_TEXT)
    assert check["total_items"] == 4
    # 3 条有 evidence(1 条事件缺失)
    assert check["evidence_coverage"] == pytest.approx(3 / 4)
    # 2 条可定位(结拜 + 辞别;“原文根本没有的句子”不可定位)
    assert check["span_located_rate"] == pytest.approx(2 / 4)
    assert items[0]["span_located"] is True
    assert items[1]["span_located"] is False


def test_parse_judge_scores_clamps_and_tolerates():
    scores = jf.parse_judge_scores({
        "precision": {"score": 1.7, "reason": "x"},
        "faithfulness": {"score": "bad", "reason": "y"},
        "comprehensiveness": {"score": -0.5},
        "item_verdicts": [{"label": "1", "supported": False}, "junk"],
    })
    assert scores["precision"]["score"] == 1.0
    assert scores["faithfulness"]["score"] is None
    assert scores["comprehensiveness"]["score"] == 0.0
    assert len(scores["item_verdicts"]) == 1


def test_aggregate_scores():
    chapter_results = [
        {"scores": {"precision": {"score": 0.8}, "faithfulness": {"score": 0.6},
                    "comprehensiveness": {"score": 1.0}},
         "span_check": {"total_items": 2, "evidence_coverage": 1.0, "span_located_rate": 0.5}},
        {"scores": {"precision": {"score": 1.0}, "faithfulness": {"score": 0.8},
                    "comprehensiveness": {"score": 0.6}},
         "span_check": {"total_items": 4, "evidence_coverage": 0.5, "span_located_rate": 0.5}},
    ]
    agg = jf.aggregate_scores(chapter_results)
    assert agg["precision"] == pytest.approx(0.9)
    assert agg["faithfulness"] == pytest.approx(0.7)
    assert agg["comprehensiveness"] == pytest.approx(0.8)
    assert agg["m5"] == pytest.approx((0.9 + 0.7 + 0.8) / 3)
    assert agg["total_items"] == 6
    assert agg["evidence_coverage"] == pytest.approx(0.75)


def test_verdicts_to_findings_review_compatible():
    """findings 与 quality_audit 报告同构,可被 generate_review_page 消费。"""
    items = jf.build_judge_items(FACT)
    jf.check_spans_locally(items, CHAPTER_TEXT)
    scores = {"item_verdicts": [
        {"label": items[0]["label"], "supported": False, "reason": "无支持"},
    ]}
    block = jf.verdicts_to_findings(3, items, scores)
    assert block["chapter_num"] == 3
    types = {f["error_type"] for f in block["findings"]}
    # items[0] 被裁定不支持;items[1] evidence 不可定位
    assert "unsupported_by_text" in types
    assert "evidence_not_locatable" in types
    for f in block["findings"]:
        for key in ("entity_name", "entity_type", "error_type",
                    "confidence", "reason", "fix_target"):
            assert key in f


def test_compute_calibration_kappa():
    # 完全一致 → kappa 1.0,已校准
    good = jf.compute_calibration_kappa([(True, True), (False, False)] * 10)
    assert good["kappa"] == 1.0 and good["calibrated"] is True
    # 完全不一致 → kappa < 0,未校准
    bad = jf.compute_calibration_kappa([(True, False), (False, True)] * 10)
    assert bad["kappa"] < 0 and bad["calibrated"] is False
    # 空样本 → 未校准
    empty = jf.compute_calibration_kappa([])
    assert empty["calibrated"] is False


# ── FR-3.3 校准条目构造 ──


def _iaa_tasks():
    return [
        {"task_id": "T1", "novel": "西游记", "kind": "locations", "name": "花果山",
         "tier_system": "mountain", "parent_system": "傲来国",
         "context_snippets": ["第1回: 傲来国有一座花果山"],
         "annotation": {"is_valid": "true", "correct_tier": "mountain",
                        "correct_parent": "傲来国"}},
        {"task_id": "T2", "novel": "西游记", "kind": "characters", "name": "银驮",
         "context_snippets": ["第3回: 众猴簇拥"],
         "annotation": {"is_valid": "false"}},
        {"task_id": "T3", "novel": "红楼梦", "kind": "relations",
         "person_a": "凤姐", "person_b": "鸳鸯", "system_type": "委托",
         "context_snippets": ["第3回: 王夫人笑指"],
         "annotation": {"correct_type": "主仆"}},
        {"task_id": "T4", "novel": "红楼梦", "kind": "characters", "name": "缺标签",
         "context_snippets": [], "annotation": {}},
    ]


def test_build_calibration_items():
    items = jf.build_calibration_items(_iaa_tasks())
    # T4 人工标签缺失,跳过
    assert [it["task_id"] for it in items] == ["T1", "T2", "T3"]
    assert items[0]["human"] is True
    assert items[1]["human"] is False
    # relations:correct_type(主仆) != system_type(委托) → 人工判不支持
    assert items[2]["human"] is False
    assert "花果山" in items[0]["claim"]


# ── judge_chapter / run_judge_for_novel(mock LLM)──


def test_judge_chapter_maps_verdict_indexes():
    llm = MockLLM()
    chapter = {"chapter_num": 3, "title": "t", "content": CHAPTER_TEXT,
               "fact_json": json.dumps(FACT, ensure_ascii=False)}
    r = _run(jf.judge_chapter(llm, "水浒传", chapter))
    assert r["scores"]["precision"]["score"] == 0.9
    # 序号 "1"/"2" 映射回条目标签
    labels = [v["label"] for v in r["scores"]["item_verdicts"]]
    assert labels[0].startswith("关系: 宋江")
    assert r["span_check"]["total_items"] == 4


def test_run_judge_for_novel_writes_report_and_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        jf, "load_chapters_with_facts",
        lambda novel_id: ("水浒传", [
            {"chapter_num": i, "title": f"第{i}回", "content": CHAPTER_TEXT,
             "fact_json": json.dumps(FACT, ensure_ascii=False)}
            for i in range(1, 8)
        ]),
    )
    audit_path = tmp_path / "judge_log.jsonl"
    report = _run(jf.run_judge_for_novel(
        "novel-1", sample=3, llm=MockLLM(), out_dir=tmp_path, audit_path=audit_path,
    ))
    assert report["slug"] == "shuihu"
    assert len(report["sample_chapters"]) == 3
    assert report["aggregate"]["m5"] == pytest.approx(0.8)
    # 报告 JSON/MD 落盘,findings 结构可供 review.html 消费
    json_files = list(tmp_path.glob("judge_faithfulness_shuihu_*.json"))
    assert len(json_files) == 1
    saved = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert saved["findings"] and "aggregate" in saved
    assert (tmp_path / json_files[0].name.replace(".json", ".md")).exists()
    # 审计日志:每章一行,含 prompt 版本(NFR-5)
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    entry = json.loads(lines[0])
    assert entry["prompt_version"] == jf.PROMPT_VERSION
    assert entry["novel_id"] == "novel-1" and "scores" in entry


def test_run_calibration_writes_report(tmp_path):
    iaa_file = tmp_path / "iaa_annotation_test.json"
    iaa_file.write_text(json.dumps({"tasks": _iaa_tasks()}, ensure_ascii=False),
                        encoding="utf-8")
    # judge 全部判支持 → 与人工(T1 true / T2,T3 false)部分一致
    llm = MockLLM(calibrate_response={"verdicts": [
        {"index": 0, "supported": True},
        {"index": 1, "supported": True},
        {"index": 2, "supported": True},
    ]})
    report = _run(jf.run_calibration(llm=llm, iaa_file=iaa_file, out_dir=tmp_path))
    assert report["total_pairs"] == 3
    # pairs: (T,F,F) vs (T,T,T) → 一致 1/3,kappa < 阈值 → 未校准
    assert report["observed_agreement"] == pytest.approx(1 / 3, abs=1e-3)
    assert report["calibrated"] is False
    assert "locations" in report["by_kind"]
    assert (tmp_path / "judge_calibration.json").exists()
    assert (tmp_path / "judge_calibration.md").exists()


# ── 报告渲染 ──


def test_render_reports():
    report = {
        "title": "水浒传", "novel_id": "n1", "judge_prompt_version": jf.PROMPT_VERSION,
        "sample_chapters": [1, 2],
        "aggregate": {"precision": 0.9, "faithfulness": 0.8, "comprehensiveness": 0.7,
                      "m5": 0.8, "total_items": 10, "evidence_coverage": 0.9,
                      "span_located_rate": 0.8, "chapters_judged": 2},
        "chapters": [{"chapter_num": 1, "scores": {
            "precision": {"score": 0.9, "reason": "r1"},
            "faithfulness": {"score": 0.8, "reason": "r2"},
            "comprehensiveness": {"score": 0.7, "reason": ""}}}],
    }
    md = jf.render_judge_md(report)
    for needle in ("precision", "faithfulness", "comprehensiveness",
                   "M5 综合", "不进论文冻结数字", "r1"):
        assert needle in md

    cal = {"judge_prompt_version": jf.PROMPT_VERSION, "iaa_file": "x.json",
           "kappa": 0.5, "observed_agreement": 0.8, "agree_count": 8,
           "total_pairs": 10, "threshold": 0.4, "calibrated": True,
           "by_kind": {"locations": {"kappa": 0.5, "total_pairs": 10,
                                     "observed_agreement": 0.8, "agree_count": 8,
                                     "threshold": 0.4, "calibrated": True}}}
    md2 = jf.render_calibration_md(cal)
    assert "已校准" in md2 and "0.500" in md2
