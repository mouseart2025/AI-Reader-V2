"""FR-3.2/FR-3.3 LLM-as-judge 抽取忠实度校验回路。

对 chapter_facts 按 precision / faithfulness / comprehensiveness 三维度评分:
  - precision        抽取的关系/事件是否都有原文支持、类型是否正确
  - faithfulness     每条 evidence span 是否真实出自原文且支持对应条目
  - comprehensiveness 原文明确的重要关系/事件被抽取覆盖的比例

证据定位的本地预检(子串匹配,归一化空白)复用
chapter_fact_extractor.span_located,与抽取侧口径唯一。

FR-3.3 校准:对 IAA 子集(沿用 compute_iaa.py 的数据约定:标注员 B 文件 +
errata gold)跑同一 judge,报告 judge 与人工的 Cohen's kappa(与
compute_iaa.py 同一实现)。kappa 低于阈值时 Q0 的 M5 标记为"未校准"。
judge 分数只作相对指标(版本间对比),绝不写入任何论文冻结数字。

闭环(NFR-5):
  - 每章评分落 JSONL 审计日志 audit_reports/judge_decisions_log.jsonl
    (entity_resolver 同款模式,含 prompt 版本);
  - 评分报告写 audit_reports/judge_faithfulness_{slug}_{date}.json,其中
    findings 字段与 quality_audit.py 报告同构,可直接被
    generate_review_page.py (review.html) 消费,进入人工复核闭环。

Usage:
    cd backend && .venv/bin/python scripts/judge_extraction_faithfulness.py <novel_id>
    .venv/bin/python scripts/judge_extraction_faithfulness.py <novel_id> --sample 5
    .venv/bin/python scripts/judge_extraction_faithfulness.py --calibrate
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (str(_BACKEND_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用既有实现(单一实现原则):抽样/JSON 解析/seed 约定来自 quality_dashboard,
# kappa 与 IAA 数据约定来自 compute_iaa,evidence 定位来自抽取器。
from quality_dashboard import SEED, parse_llm_json, pick_sample  # noqa: E402
from compute_iaa import (  # noqa: E402
    IAA_DIR,
    NOVEL_TO_SLUG,
    b_labels_for_character,
    b_labels_for_location,
    b_labels_for_relation,
    cohens_kappa,
)
from src.extraction.chapter_fact_extractor import span_located  # noqa: E402

logger = logging.getLogger(__name__)

PROMPT_VERSION = "judge-v1(2026-08-26)"
JUDGE_SAMPLE_CHAPTERS = 5     # 每本抽样章数(seed=42)
CALIBRATION_BATCH = 20        # 校准模式每次 LLM 调用判定的条目数
MAX_CONTENT_CHARS = 12000     # 单章送 judge 的字符上限
MAX_SNIPPET_CHARS = 600       # 校准模式单条语境上限

# FR-3.3:kappa 低于此阈值时 Q0 的 M5 标记为"未校准"
KAPPA_THRESHOLD = 0.40

REPORT_DIR = _BACKEND_DIR / "audit_reports"
# 决策审计日志(JSONL,每行一条章节评分)— 与 entity_resolution_log.jsonl 同目录
AUDIT_LOG_PATH = REPORT_DIR / "judge_decisions_log.jsonl"
CALIBRATION_JSON_PATH = REPORT_DIR / "judge_calibration.json"

# 书名 → slug(与 compute_iaa.NOVEL_TO_SLUG 同一约定:title→slug)
_SLUG_BY_TITLE = NOVEL_TO_SLUG

# ── judge prompt(入库常量,口径可追溯)───────────────────────────────

JUDGE_SYSTEM = (
    "你是小说信息抽取质量评审专家。你只输出严格 JSON,不输出任何其他文字。"
)

JUDGE_USER_TEMPLATE = """以下是小说《{title}》第{chapter_num}章的正文(可能截断)与自动抽取的人物关系(relationships)和事件(events)。请按三个维度评分(0.0-1.0):

1. precision(精确度):抽取的关系/事件本身是否都有原文支持、类型/内容是否正确。有错抽、编造则扣分
2. faithfulness(忠实度):每条 evidence 是否真实出自原文、且能直接支持对应条目。evidence 缺失、伪造或与条目无关则扣分
3. comprehensiveness(完整性):原文中明确写出的重要人物关系/事件被抽取覆盖的比例。明显遗漏则扣分

## 原文
{content}

## 抽取结果(共 {n_items} 条)
{items_block}

严格输出 JSON:
{{"precision": {{"score": 0.0, "reason": "..."}},
  "faithfulness": {{"score": 0.0, "reason": "..."}},
  "comprehensiveness": {{"score": 0.0, "reason": "..."}},
  "item_verdicts": [{{"label": "条目序号", "supported": true, "reason": "..."}}]}}

要求:item_verdicts 逐条覆盖全部 {n_items} 条(label 用上面的序号);reason 用中文简述;不要输出任何解释性文字、不要用 markdown 代码块。"""

CALIBRATE_SYSTEM = (
    "你是小说信息抽取质量评审专家。你只输出严格 JSON,不输出任何其他文字。"
)

CALIBRATE_USER_TEMPLATE = """以下是自动抽取系统的输出论断及其原文语境。逐条判断该论断是否有原文支持(supported=true/false)。判断标准:语境能直接支持论断为 true;语境与论断矛盾、或语境完全无法支持该论断(如该实体在语境中不存在)为 false。

{items_block}

严格输出 JSON:
{{"verdicts": [{{"index": 0, "supported": true}}, ...]}}

要求:覆盖全部 {n_items} 条;不要输出任何解释、不要用 markdown 代码块。"""


# ═══════════════════════════════════════════════════════════════════
# 纯函数(单元测试直接覆盖)
# ═══════════════════════════════════════════════════════════════════

def build_judge_items(fact: dict) -> list[dict]:
    """从 chapter fact JSON 提取待评条目(关系 + 事件),附本地定位所需字段。"""
    items: list[dict] = []
    for rel in fact.get("relationships") or []:
        label = f"关系: {rel.get('person_a', '?')}—{rel.get('person_b', '?')} ({rel.get('relation_type', '?')})"
        items.append({
            "kind": "relationship",
            "label": label,
            "evidence": (rel.get("evidence") or "").strip(),
        })
    for ev in fact.get("events") or []:
        items.append({
            "kind": "event",
            "label": f"事件: {ev.get('summary', '?')}",
            "evidence": (ev.get("evidence") or "").strip(),
        })
    return items


def check_spans_locally(items: list[dict], chapter_text: str) -> dict:
    """evidence span 本地预检(不调 LLM):定位率 + 覆盖率。"""
    total = len(items)
    with_evidence = 0
    located = 0
    for item in items:
        ev = item["evidence"]
        item["span_located"] = bool(ev) and span_located(ev, chapter_text)
        if ev:
            with_evidence += 1
        if item["span_located"]:
            located += 1
    return {
        "total_items": total,
        "evidence_coverage": (with_evidence / total) if total else None,
        "span_located_rate": (located / total) if total else None,
    }


def parse_judge_scores(parsed: dict) -> dict:
    """从 judge 的 JSON 输出提取三维度分数(钳制到 [0,1])与逐条裁定。"""

    def _dim(name: str) -> dict:
        d = parsed.get(name) or {}
        try:
            score = float(d.get("score"))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            score = max(0.0, min(1.0, score))
        return {"score": score, "reason": str(d.get("reason", ""))[:300]}

    verdicts = []
    for v in parsed.get("item_verdicts") or []:
        if not isinstance(v, dict):
            continue
        verdicts.append({
            "label": str(v.get("label", "")),
            "supported": bool(v.get("supported")),
            "reason": str(v.get("reason", ""))[:300],
        })
    return {
        "precision": _dim("precision"),
        "faithfulness": _dim("faithfulness"),
        "comprehensiveness": _dim("comprehensiveness"),
        "item_verdicts": verdicts,
    }


def aggregate_scores(chapter_results: list[dict]) -> dict:
    """跨章聚合:三维度均值 + M5 综合分(三维度均值)+ evidence 覆盖/定位率。"""
    dims = ("precision", "faithfulness", "comprehensiveness")
    means: dict[str, float | None] = {}
    for dim in dims:
        vals = [
            r["scores"][dim]["score"] for r in chapter_results
            if r.get("scores", {}).get(dim, {}).get("score") is not None
        ]
        means[dim] = sum(vals) / len(vals) if vals else None
    scored = [m for m in means.values() if m is not None]
    total_items = sum(r["span_check"]["total_items"] for r in chapter_results)
    coverage_vals = [
        r["span_check"]["evidence_coverage"] for r in chapter_results
        if r["span_check"].get("evidence_coverage") is not None
    ]
    located_vals = [
        r["span_check"]["span_located_rate"] for r in chapter_results
        if r["span_check"].get("span_located_rate") is not None
    ]
    return {
        "precision": means["precision"],
        "faithfulness": means["faithfulness"],
        "comprehensiveness": means["comprehensiveness"],
        "m5": (sum(scored) / len(scored)) if scored else None,
        "total_items": total_items,
        "evidence_coverage": (sum(coverage_vals) / len(coverage_vals)) if coverage_vals else None,
        "span_located_rate": (sum(located_vals) / len(located_vals)) if located_vals else None,
        "chapters_judged": len(chapter_results),
    }


def verdicts_to_findings(chapter_num: int, items: list[dict],
                         scores: dict) -> dict:
    """把不支持的条目转为 quality_audit 同构 findings,供 review.html 人工复核。"""
    unsupported = {v["label"] for v in scores.get("item_verdicts", [])
                   if not v.get("supported")}
    reasons = {v["label"]: v.get("reason", "") for v in scores.get("item_verdicts", [])}
    findings = []
    for item in items:
        short = item["label"].split(": ", 1)[-1]
        if item["label"] in unsupported or any(
            short and short in u for u in unsupported
        ):
            findings.append({
                "entity_name": short[:60],
                "entity_type": item["kind"],
                "error_type": "unsupported_by_text",
                "confidence": "medium",
                "reason": reasons.get(item["label"], "") or "judge 判定该条无原文支持",
                "fix_target": "prompt",
            })
        elif item["evidence"] and not item.get("span_located"):
            findings.append({
                "entity_name": short[:60],
                "entity_type": item["kind"],
                "error_type": "evidence_not_locatable",
                "confidence": "high",
                "reason": f"evidence 无法在原文定位: {item['evidence'][:50]}",
                "fix_target": "prompt",
            })
    return {"chapter_num": chapter_num, "findings": findings}


def compute_calibration_kappa(pairs: list[tuple]) -> dict:
    """judge vs 人工的 Cohen's kappa(复用 compute_iaa.cohens_kappa,口径一致)。"""
    k, p_o, agree, n = cohens_kappa(pairs)
    return {
        "kappa": round(k, 3),
        "observed_agreement": round(p_o, 3),
        "agree_count": agree,
        "total_pairs": n,
        "threshold": KAPPA_THRESHOLD,
        "calibrated": bool(n) and k >= KAPPA_THRESHOLD,
    }


# ═══════════════════════════════════════════════════════════════════
# 报告渲染(纯函数)
# ═══════════════════════════════════════════════════════════════════

def _pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x:.1%}"


def render_judge_md(report: dict) -> str:
    agg = report["aggregate"]
    lines = [
        f"# Judge 抽取忠实度报告:{report['title']}",
        "",
        f"- novel_id: `{report['novel_id']}`",
        f"- prompt 版本: {report['judge_prompt_version']} · seed: {SEED}",
        f"- 抽样章节: {report['sample_chapters']}",
        "- ⚠️ judge 分数只作相对指标(版本间对比),不进论文冻结数字",
        "",
        "## 三维度汇总",
        "",
        "| 维度 | 分数 |",
        "|---|---|",
        f"| precision(精确度) | {_pct(agg['precision'])} |",
        f"| faithfulness(忠实度) | {_pct(agg['faithfulness'])} |",
        f"| comprehensiveness(完整性) | {_pct(agg['comprehensiveness'])} |",
        f"| **M5 综合** | **{_pct(agg['m5'])}** |",
        "",
        f"- 条目总数: {agg['total_items']} · evidence 覆盖率: "
        f"{_pct(agg['evidence_coverage'])} · span 可定位率: {_pct(agg['span_located_rate'])}",
        "",
        "## 抽样理由(逐章)",
        "",
    ]
    for ch in report["chapters"]:
        s = ch["scores"]
        lines.append(
            f"- 第 {ch['chapter_num']} 章: precision={_pct(s['precision']['score'])} "
            f"faithfulness={_pct(s['faithfulness']['score'])} "
            f"comprehensiveness={_pct(s['comprehensiveness']['score'])}"
        )
        for dim in ("precision", "faithfulness", "comprehensiveness"):
            reason = s[dim].get("reason")
            if reason:
                lines.append(f"  - {dim}: {reason}")
    lines.append("")
    return "\n".join(lines)


def render_calibration_md(report: dict) -> str:
    lines = [
        "# Judge 校准报告(FR-3.3)",
        "",
        f"- IAA 文件: `{report['iaa_file']}`",
        f"- prompt 版本: {report['judge_prompt_version']}",
        "- 口径: judge 二值判定(有/无原文支持) vs 标注员 B 标签;"
        "Cohen's kappa 复用 compute_iaa.py 同一实现",
        "- ⚠️ judge 分数只作相对指标,不进论文冻结数字",
        "",
        "## 总体",
        "",
        f"- 条目数: {report['total_pairs']} · 一致率: {report['observed_agreement']:.3f} "
        f"({report['agree_count']}/{report['total_pairs']})",
        f"- **Cohen's kappa: {report['kappa']:.3f}**"
        f"(阈值 ≥{report['threshold']:.2f} → "
        f"{'**已校准**' if report['calibrated'] else '**未校准**'})",
        "",
        "## 分类别",
        "",
        "| 类别 | 条目数 | kappa |",
        "|---|---|---|",
    ]
    for kind, m in report.get("by_kind", {}).items():
        lines.append(f"| {kind} | {m['total_pairs']} | {m['kappa']:.3f} |")
    lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 审计日志(NFR-5,entity_resolver 同款 JSONL 模式)
# ═══════════════════════════════════════════════════════════════════

def write_judge_audit(entry: dict, log_path: Path | None = None) -> Path:
    """追加一条 judge 评分到 JSONL 审计日志。"""
    path = log_path or AUDIT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        **entry,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ═══════════════════════════════════════════════════════════════════
# 以下为运行时(DB / LLM)代码
# ═══════════════════════════════════════════════════════════════════

def _db_path() -> Path:
    return Path(os.environ.get(
        "AI_READER_DATA_DIR", str(Path.home() / ".ai-reader-v2")
    )) / "data.db"


def load_chapters_with_facts(novel_id: str) -> list[dict]:
    """只读加载:有 chapter_facts 的章节(content + fact_json)。"""
    conn = sqlite3.connect(str(_db_path()))
    rows = conn.execute(
        """
        SELECT c.chapter_num, c.title, c.content, cf.fact_json
        FROM chapters c
        JOIN chapter_facts cf ON c.id = cf.chapter_id AND c.novel_id = cf.novel_id
        WHERE c.novel_id = ?
        ORDER BY c.chapter_num
        """,
        (novel_id,),
    ).fetchall()
    title_row = conn.execute(
        "SELECT title FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    conn.close()
    return (
        title_row[0] if title_row else "unknown",
        [
            {"chapter_num": r[0], "title": r[1], "content": r[2], "fact_json": r[3]}
            for r in rows
        ],
    )


async def _llm_generate(llm, system: str, user: str) -> object:
    """统一 LLM 调用;返回原始 result(str 或 dict)。"""
    result, _usage = await llm.generate(
        system=system,
        prompt=user,
        format=None,
        temperature=0.0,
        max_tokens=4096,
        timeout=300,
    )
    return result


def _parse_llm_result(result: object) -> dict:
    if isinstance(result, dict):
        return result
    return parse_llm_json(str(result))


async def judge_chapter(llm, title: str, chapter: dict) -> dict:
    """judge 单章:本地 span 预检 + LLM 三维度评分。"""
    fact = json.loads(chapter["fact_json"])
    full_content = chapter["content"] or ""
    items = build_judge_items(fact)
    # 本地预检对全章原文做(送 LLM 的文本会截断,但 span 定位不应受截断影响)
    span_check = check_spans_locally(items, full_content)

    scores = {
        "precision": {"score": None, "reason": ""},
        "faithfulness": {"score": None, "reason": ""},
        "comprehensiveness": {"score": None, "reason": ""},
        "item_verdicts": [],
    }
    if items:
        items_block = "\n".join(
            f"{i + 1}. [{it['kind']}] {it['label']}\n   evidence: {it['evidence'] or '(缺失)'}"
            for i, it in enumerate(items)
        )
        user = JUDGE_USER_TEMPLATE.format(
            title=title, chapter_num=chapter["chapter_num"],
            content=full_content[:MAX_CONTENT_CHARS],
            n_items=len(items), items_block=items_block,
        )
        try:
            result = await _llm_generate(llm, JUDGE_SYSTEM, user)
            scores = parse_judge_scores(_parse_llm_result(result))
            # judge 按序号返回裁定,映射回条目标签,供 findings 匹配
            for v in scores["item_verdicts"]:
                if v["label"].isdigit() and 1 <= int(v["label"]) <= len(items):
                    v["label"] = items[int(v["label"]) - 1]["label"]
        except Exception as err:
            logger.warning(
                "judge 第 %s 章 LLM 评分失败: %s", chapter["chapter_num"], err,
            )

    return {
        "chapter_num": chapter["chapter_num"],
        "scores": scores,
        "span_check": span_check,
        "items": items,
    }


async def run_judge_for_novel(
    novel_id: str,
    sample: int = JUDGE_SAMPLE_CHAPTERS,
    llm=None,
    out_dir: Path = REPORT_DIR,
    audit_path: Path | None = None,
) -> dict:
    """单本 judge 全流程:抽样 → 逐章评分 → 聚合 → 报告 + 审计日志。"""
    if llm is None:
        from src.infra.llm_client import get_llm_client
        llm = get_llm_client()

    title, chapters = load_chapters_with_facts(novel_id)
    if not chapters:
        raise RuntimeError(f"novel {novel_id} 无已抽取章节")
    sampled = pick_sample(chapters, min(sample, len(chapters)))

    chapter_results = []
    for ch in sampled:
        r = await judge_chapter(llm, title, ch)
        chapter_results.append(r)
        write_judge_audit({
            "novel_id": novel_id, "title": title,
            "chapter_num": r["chapter_num"],
            "scores": {k: v["score"] for k, v in r["scores"].items() if k != "item_verdicts"},
            "total_items": r["span_check"]["total_items"],
            "evidence_coverage": r["span_check"]["evidence_coverage"],
            "span_located_rate": r["span_check"]["span_located_rate"],
        }, log_path=audit_path)
        logger.info(
            "judge %s 第 %s 章: %d 条, coverage=%s",
            title, r["chapter_num"], r["span_check"]["total_items"],
            r["span_check"]["evidence_coverage"],
        )

    slug = _SLUG_BY_TITLE.get(title, title)
    report = {
        "novel_id": novel_id,
        "title": title,
        "slug": slug,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        "judge_prompt_version": PROMPT_VERSION,
        # judge 模型必须落报告:跨厂商/跨模型对比与校准(NFR-5 可审计)依赖它,
        # 同名报告换 judge 模型时以此区分(如 20260827-deepseek vs -qwen)。
        "judge_model": os.environ.get("LLM_MODEL", ""),
        "judge_base_url": os.environ.get("LLM_BASE_URL", ""),
        "seed": SEED,
        "sample_chapters": [r["chapter_num"] for r in chapter_results],
        "chapters": chapter_results,
        "aggregate": aggregate_scores(chapter_results),
        # quality_audit 同构 findings,供 generate_review_page.py (review.html) 消费
        "findings": [
            verdicts_to_findings(r["chapter_num"], r["items"], r["scores"])
            for r in chapter_results
        ],
        "note": "judge 分数只作相对指标(版本间对比),不进论文冻结数字",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"judge_faithfulness_{slug}_{date_str}.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"judge_faithfulness_{slug}_{date_str}.md").write_text(
        render_judge_md(report), encoding="utf-8"
    )
    print(f"[judge] {title}: M5={_pct(report['aggregate']['m5'])} → {json_path}")
    return report


# ── FR-3.3 校准 ─────────────────────────────────────────────────────

def build_calibration_items(tasks: list[dict]) -> list[dict]:
    """IAA 任务 → judge 校准条目(论断 + 语境 + 人工标签),人工标签缺失则跳过。"""
    items = []
    for t in tasks:
        kind = t.get("kind")
        if kind == "locations":
            human = b_labels_for_location(t)["is_valid"]
            claim = (
                f"地点「{t.get('name')}」是原文中真实出现的地名"
                f"(系统标注: tier={t.get('tier_system')}, parent={t.get('parent_system')})"
            )
        elif kind == "characters":
            human = b_labels_for_character(t)["is_valid"]
            claim = f"人物「{t.get('name')}」是原文中真实存在的人物"
        elif kind == "relations":
            human = b_labels_for_relation(t)["type_agrees"]
            claim = (
                f"人物关系:「{t.get('person_a')}」与「{t.get('person_b')}」"
                f"是「{t.get('system_type')}」关系"
            )
        else:
            continue
        if human is None:
            continue
        context = "\n".join(
            (s or "")[:MAX_SNIPPET_CHARS] for s in (t.get("context_snippets") or [])
        )
        items.append({
            "task_id": t.get("task_id"),
            "kind": kind,
            "claim": claim,
            "context": context,
            "human": bool(human),
        })
    return items


async def run_calibration(
    llm=None,
    iaa_file: Path | None = None,
    out_dir: Path = REPORT_DIR,
) -> dict:
    """FR-3.3:对 IAA 子集跑 judge,报告与人工标注的 Cohen's kappa。"""
    if llm is None:
        from src.infra.llm_client import get_llm_client
        llm = get_llm_client()

    if iaa_file is None:
        candidates = sorted(glob.glob(str(IAA_DIR / "iaa_annotation_*.json")))
        if not candidates:
            raise RuntimeError(f"IAA 目录无标注文件: {IAA_DIR}")
        iaa_file = Path(candidates[-1])
    tasks = json.loads(iaa_file.read_text(encoding="utf-8")).get("tasks", [])
    items = build_calibration_items(tasks)
    if not items:
        raise RuntimeError("IAA 文件中无可用校准条目(人工标签全缺失)")

    # 分批送 judge(每批 CALIBRATION_BATCH 条)
    judge_labels: dict[int, bool] = {}
    for i in range(0, len(items), CALIBRATION_BATCH):
        chunk = items[i:i + CALIBRATION_BATCH]
        block = "\n\n".join(
            f"{j}. 论断: {it['claim']}\n   语境: {it['context'] or '(无)'}"
            for j, it in enumerate(chunk)
        )
        user = CALIBRATE_USER_TEMPLATE.format(items_block=block, n_items=len(chunk))
        try:
            result = await _llm_generate(llm, CALIBRATE_SYSTEM, user)
            for v in (_parse_llm_result(result).get("verdicts") or []):
                idx = v.get("index")
                if isinstance(idx, int) and 0 <= idx < len(chunk):
                    judge_labels[i + idx] = bool(v.get("supported"))
        except Exception as err:
            logger.warning("校准批次 %d 失败,该批记为缺失: %s", i // CALIBRATION_BATCH + 1, err)

    pairs_all: list[tuple] = []
    by_kind_pairs: dict[str, list[tuple]] = {}
    for idx, it in enumerate(items):
        j = judge_labels.get(idx)
        if j is None:
            continue
        pairs_all.append((it["human"], j))
        by_kind_pairs.setdefault(it["kind"], []).append((it["human"], j))

    overall = compute_calibration_kappa(pairs_all)
    report = {
        "judge_prompt_version": PROMPT_VERSION,
        "judge_model": os.environ.get("LLM_MODEL", ""),
        "judge_base_url": os.environ.get("LLM_BASE_URL", ""),
        "iaa_file": iaa_file.name,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        **overall,
        "by_kind": {k: compute_calibration_kappa(p) for k, p in by_kind_pairs.items()},
        "note": "judge 分数只作相对指标,不进论文冻结数字",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "judge_calibration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "judge_calibration.md").write_text(
        render_calibration_md(report), encoding="utf-8"
    )
    print(f"[judge][校准] kappa={overall['kappa']:.3f} "
          f"({'已校准' if overall['calibrated'] else '未校准'}) "
          f"→ {out_dir / 'judge_calibration.json'}")
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-as-judge 抽取忠实度校验 (FR-3.2/FR-3.3)")
    parser.add_argument("novel_id", nargs="?", help="小说 ID(与 --calibrate 互斥)")
    parser.add_argument("--calibrate", action="store_true", help="对 IAA 子集跑校准 (FR-3.3)")
    parser.add_argument("--iaa-file", type=Path, default=None, help="IAA 标注文件(默认取最新)")
    parser.add_argument("--sample", type=int, default=JUDGE_SAMPLE_CHAPTERS, help="每本抽样章数")
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()

    # 读取 backend/.env(LLM 配置)
    env_path = _BACKEND_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    if args.calibrate:
        await run_calibration(iaa_file=args.iaa_file, out_dir=args.out_dir)
        return 0
    if not args.novel_id:
        parser.error("请指定 novel_id 或使用 --calibrate")
    await run_judge_for_novel(args.novel_id, sample=args.sample, out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
