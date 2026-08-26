"""Epic 1–4 抽取管线真实 LLM 端到端冒烟 (质量改进循环 · 任务 A)。

对 sample-novels/西游记-样本.txt 的第 1 章(项目既有章节切分器切分)跑
全开关端到端管线:
    ChapterFactExtractor(关系三维 + 证据锚定 + recall pass,全开关开)
    → FactValidator(规则层)
    → hallucination_reviewer(幻觉人物 LLM 判定层)

产出指标报告(打印 + 落盘):
  - 关系维度字段填充率(polarity/rel_subtype/closeness 非空占比)
  - 越界维度值拦截次数 / 投票 override 次数(经日志捕获,口径与管线一致)
  - evidence 覆盖率(关系+事件非空占比)与 span_located 定位率
    (复用 chapter_fact_extractor.span_located,与 judge 同口径)
  - recall pass 补漏条数(source="recall_pass")
  - 幻觉判定候选数 / 裁决结果(读回 reviewer 的 JSONL 审计条目)
  - token 用量与估算成本(定价复用 cost_service.get_pricing)

LLM 配置: get_llm_client() 读 src.infra.config,云端经环境变量注入:
    LLM_PROVIDER=openai LLM_BASE_URL=https://api.deepseek.com LLM_MODEL=deepseek-chat
API key 读 backend/.env (dotenv 自动加载),本脚本绝不打印 key。

Usage:
    cd backend && LLM_PROVIDER=openai LLM_BASE_URL=https://api.deepseek.com \
        LLM_MODEL=deepseek-chat .venv/bin/python scripts/smoke_extraction_quality.py
    .venv/bin/python scripts/smoke_extraction_quality.py --mock     # 离线 mock(测试用)
    .venv/bin/python scripts/smoke_extraction_quality.py --dry-run  # 只打印计划,不调用

Output:
    audit_reports/smoke_extraction_quality_{date}.json / .md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from src.infra.llm_client import LlmUsage  # noqa: E402

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "smoke-extraction-quality-v1"

DEFAULT_NOVEL_FILE = _BACKEND_DIR / "sample-novels" / "西游记-样本.txt"
REPORT_DIR = _BACKEND_DIR / "audit_reports"
# 幻觉判定审计条目落盘路径(真实跑写这里;mock/测试由调用方传入 tmp 路径)
SMOKE_REVIEW_LOG = REPORT_DIR / "smoke_hallucination_review_log.jsonl"


# ── 管线日志质量事件捕获 ────────────────────────────────────────────
# 越界拦截/投票 override 等事件由管线内部 logger 输出(单一实现,不在此处
# 重复判定逻辑);本 handler 只计数,不改行为。

class PipelineLogStats(logging.Handler):
    """捕获抽取管线日志中的质量事件计数。"""

    def __init__(self) -> None:
        super().__init__()
        self.invalid_dimension_values = {"polarity": 0, "rel_subtype": 0, "closeness": 0}
        self.vote_overrides = 0
        self.vote_samples_dropped = 0
        self.evidence_missing_warnings = 0
        self.evidence_unlocated_warnings = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        for field in self.invalid_dimension_values:
            if f"invalid {field}" in msg:
                self.invalid_dimension_values[field] += 1
        if "rel_subtype vote override" in msg:
            self.vote_overrides += 1
        if "vote sample returned invalid" in msg:
            self.vote_samples_dropped += 1
        if "缺少 evidence" in msg:
            self.evidence_missing_warnings += 1
        if "evidence 无法在原文定位" in msg:
            self.evidence_unlocated_warnings += 1

    def as_dict(self) -> dict:
        return {
            "invalid_dimension_values": dict(self.invalid_dimension_values),
            "invalid_dimension_total": sum(self.invalid_dimension_values.values()),
            "vote_overrides": self.vote_overrides,
            "vote_samples_dropped": self.vote_samples_dropped,
            "evidence_missing_warnings": self.evidence_missing_warnings,
            "evidence_unlocated_warnings": self.evidence_unlocated_warnings,
        }


# ── 指标计算(纯函数,可单测)─────────────────────────────────────────

def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def dimension_metrics(fact) -> dict:
    """关系维度字段填充率(Epic 1):polarity/rel_subtype/closeness 非空占比。"""
    rels = fact.relationships
    n = len(rels)
    return {
        "total_relations": n,
        "polarity_filled": sum(1 for r in rels if r.polarity),
        "polarity_fill_rate": _rate(sum(1 for r in rels if r.polarity), n),
        "rel_subtype_filled": sum(1 for r in rels if r.rel_subtype),
        "rel_subtype_fill_rate": _rate(sum(1 for r in rels if r.rel_subtype), n),
        "closeness_filled": sum(1 for r in rels if r.closeness),
        "closeness_fill_rate": _rate(sum(1 for r in rels if r.closeness), n),
        "relations_with_vote": sum(1 for r in rels if r.subtype_vote),
    }


def evidence_metrics(fact, chapter_text: str) -> dict:
    """evidence 覆盖率与 span_located 定位率(Epic 3)。

    定位判定复用 chapter_fact_extractor.span_located,与 judge 口径唯一。
    """
    from src.extraction.chapter_fact_extractor import span_located

    rel_ev = [r.evidence.strip() for r in fact.relationships]
    ev_ev = [e.evidence.strip() for e in fact.events]
    all_ev = rel_ev + ev_ev
    non_empty = [e for e in all_ev if e]
    located = sum(1 for e in non_empty if span_located(e, chapter_text))
    return {
        "relation_coverage": _rate(sum(1 for e in rel_ev if e), len(rel_ev)),
        "event_coverage": _rate(sum(1 for e in ev_ev if e), len(ev_ev)),
        "total_items": len(all_ev),
        "non_empty": len(non_empty),
        "overall_coverage": _rate(len(non_empty), len(all_ev)),
        "span_located": located,
        "span_located_rate": _rate(located, len(non_empty)),
    }


def recall_metrics(fact) -> dict:
    """recall pass 补漏条数(Epic 4):source="recall_pass" 的记录数。"""
    return {
        "characters": sum(1 for c in fact.characters if c.source == "recall_pass"),
        "relationships": sum(1 for r in fact.relationships if r.source == "recall_pass"),
        "events": sum(1 for e in fact.events if e.source == "recall_pass"),
    }


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """估算成本:定价表复用 cost_service.get_pricing(单一实现)。"""
    from src.services.cost_service import get_pricing

    price_in, price_out = get_pricing(model)
    return prompt_tokens / 1_000_000 * price_in + completion_tokens / 1_000_000 * price_out


# ── LLM 调用包装:累计 token 用量(不改 client 行为)─────────────────

class UsageTracker:
    """包装 LLM client,累计全部调用的 token 用量。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    async def generate(self, *args, **kwargs):
        result, usage = await self._inner.generate(*args, **kwargs)
        self.calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        return result, usage

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ── Mock LLM(--mock 离线模式 / 测试用)──────────────────────────────
# 按 system prompt 内容区分管线内的四类调用(首遍抽取/子类型投票/查漏/幻觉
# 判定),返回固定 JSON,覆盖各指标路径:越界维度值、投票 override、编造
# evidence、缺 evidence、recall 补漏、幻觉候选。

_MOCK_MAIN_RESULT = {
    "characters": [
        {"name": "石猴", "new_aliases": ["美猴王"]},
        {"name": "众猴"},
        {"name": "不存在之人"},
    ],
    "relationships": [
        {
            "person_a": "众猴", "person_b": "石猴", "relation_type": "臣服",
            "polarity": "positive", "rel_subtype": "主从", "closeness": "close",
            "evidence": "众猴听说，即拱伏无违，朝上礼拜",
        },
        {
            "person_a": "石猴", "person_b": "不存在之人", "relation_type": "结拜",
            "polarity": "友好", "rel_subtype": "结拜", "closeness": "亲密",
            "evidence": "此乃原文中根本没有的编造引用片段",
        },
    ],
    "locations": [
        {"name": "花果山", "type": "山"},
        {"name": "水帘洞", "type": "洞府", "parent": "花果山"},
    ],
    "events": [
        {
            "summary": "石猴跳入瀑布泉发现水帘洞", "type": "成长",
            "importance": "high", "participants": ["石猴"], "location": "水帘洞",
            "evidence": "将身一纵，径跳入瀑布泉中",
        },
        {
            "summary": "众猴拜石猴为王", "type": "社交",
            "importance": "medium", "participants": ["石猴", "众猴"],
            "location": "花果山", "evidence": "",
        },
    ],
}

_MOCK_VOTE_RESULT = {
    "votes": [
        {"index": 0, "rel_subtype": "主从"},
        {"index": 1, "rel_subtype": "敌对"},
    ]
}

_MOCK_RECALL_RESULT = {
    "characters": [{"name": "石猿"}],
    "relationships": [],
    "events": [],
}

_MOCK_VERDICT_RESULT = {
    "verdicts": [
        {"name": "不存在之人", "is_real": False, "confidence": "high",
         "reason": "原文通篇无此人物"},
    ]
}


class MockLLM:
    """离线 mock:按 system prompt 分发固定响应,token 计数为定值。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, system: str, prompt: str, **kwargs):
        self.calls.append(system[:30])
        if "查漏专家" in system:
            result = dict(_MOCK_RECALL_RESULT)
        elif "关系类型判定专家" in system:
            result = dict(_MOCK_VOTE_RESULT)
        elif "人物真实性审核专家" in system:
            result = dict(_MOCK_VERDICT_RESULT)
        else:
            result = json.loads(json.dumps(_MOCK_MAIN_RESULT))
        return result, LlmUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)


# ── 冒烟主流程 ─────────────────────────────────────────────────────

def load_chapter(novel_file: Path, chapter_num: int) -> tuple[str, str]:
    """用项目既有章节切分器取第 N 章,返回 (标题, 正文)。"""
    from src.utils.chapter_splitter import split_chapters

    text = novel_file.read_text(encoding="utf-8")
    chapters = split_chapters(text)
    if not (1 <= chapter_num <= len(chapters)):
        raise ValueError(
            f"章节号 {chapter_num} 超出范围(切分出 {len(chapters)} 章)"
        )
    ch = chapters[chapter_num - 1]
    return ch.title, ch.content


def current_switches() -> dict:
    """五个质量开关当前状态(记录进报告,便于质量历史对比)。"""
    from src.infra import config

    return {
        "RELATION_DIMENSIONS_ENABLED": config.RELATION_DIMENSIONS_ENABLED,
        "RELATION_SUBTYPE_VOTE_SAMPLES": config.RELATION_SUBTYPE_VOTE_SAMPLES,
        "ENTITY_RESOLUTION_ENABLED": config.ENTITY_RESOLUTION_ENABLED,
        "EVIDENCE_GROUNDING_ENABLED": config.EVIDENCE_GROUNDING_ENABLED,
        "RECALL_PASS_ENABLED": config.RECALL_PASS_ENABLED,
        "HALLUCINATION_REVIEW_ENABLED": config.HALLUCINATION_REVIEW_ENABLED,
    }


async def run_smoke(
    chapter_text: str,
    llm,
    *,
    novel_id: str = "smoke-xiyouji",
    chapter_id: int = 1,
    genre_hint: str = "fantasy",
    review_log_path: Path | None = None,
    record_cost: bool = False,
) -> dict:
    """端到端冒烟:抽取 → 规则校验 → 幻觉 LLM 判定。返回报告 dict。

    record_cost=False:冒烟不记 cost_service 月度账本(避免 DB 副作用),
    成本由 UsageTracker 统计 + get_pricing 估算。
    """
    from src.extraction.chapter_fact_extractor import ChapterFactExtractor
    from src.extraction.fact_validator import FactValidator
    from src.extraction.hallucination_reviewer import review_chapter_characters

    tracker = UsageTracker(llm)
    stats = PipelineLogStats()
    extractor_logger = logging.getLogger("src.extraction.chapter_fact_extractor")
    # 投票 override 等质量事件是 INFO 级;冒烟期间临时放开该 logger 级别
    old_level = extractor_logger.level
    extractor_logger.setLevel(logging.INFO)
    extractor_logger.addHandler(stats)
    try:
        extractor = ChapterFactExtractor(llm=llm)
        # 云端判定在 __init__ 按 isinstance 完成;之后换成 tracker 累计用量
        extractor.llm = tracker
        fact, _usage, meta = await extractor.extract(
            novel_id=novel_id,
            chapter_id=chapter_id,
            chapter_text=chapter_text,
            genre_hint=genre_hint,
        )

        validator = FactValidator(genre=genre_hint)
        fact = validator.validate(fact)

        log_path = review_log_path or SMOKE_REVIEW_LOG
        # 清掉上一轮的审计条目,保证读回的是本次裁决
        if log_path.exists():
            log_path.unlink()
        fact = await review_chapter_characters(
            fact,
            chapter_text=chapter_text,
            llm=tracker,
            novel_id=novel_id,
            chapter_id=chapter_id,
            log_path=log_path,
            record_cost=record_cost,
        )
    finally:
        extractor_logger.removeHandler(stats)
        extractor_logger.setLevel(old_level)

    # 幻觉判定:读回 reviewer 的 JSONL 审计条目(候选 + 裁决)
    hallucination = {"candidates": [], "actions": [], "log_path": str(log_path)}
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            entry = json.loads(lines[-1])
            hallucination["candidates"] = entry.get("candidates", [])
            hallucination["actions"] = entry.get("actions", [])

    return {
        "fact": fact,
        "meta": meta,
        "sanitize": stats.as_dict(),
        "dimensions": dimension_metrics(fact),
        "evidence": evidence_metrics(fact, chapter_text),
        "recall_pass": recall_metrics(fact),
        "hallucination": hallucination,
        "usage": {
            "llm_calls": tracker.calls,
            "prompt_tokens": tracker.prompt_tokens,
            "completion_tokens": tracker.completion_tokens,
            "total_tokens": tracker.total_tokens,
        },
    }


def build_report(
    smoke: dict,
    *,
    mode: str,
    model: str,
    chapter_title: str,
    chapter_id: int,
    chapter_chars: int,
    findings: list[str] | None = None,
) -> dict:
    """聚合冒烟结果为报告 dict(纯函数)。"""
    fact = smoke["fact"]
    meta = smoke["meta"]
    usage = dict(smoke["usage"])
    usage["cost_usd"] = round(
        estimate_cost_usd(usage["prompt_tokens"], usage["completion_tokens"], model), 6
    )
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "model": model,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "chapter_chars": chapter_chars,
        "switches": current_switches(),
        "extraction": {
            "characters": len(fact.characters),
            "relationships": len(fact.relationships),
            "locations": len(fact.locations),
            "events": len(fact.events),
            "is_truncated": meta.is_truncated,
            "segment_count": meta.segment_count,
        },
        "dimensions": smoke["dimensions"],
        "sanitize": smoke["sanitize"],
        "evidence": smoke["evidence"],
        "recall_pass": smoke["recall_pass"],
        "hallucination": smoke["hallucination"],
        "usage": usage,
        "findings": findings or [],
    }


def _pct(x: float | None) -> str:
    return f"{x:.1%}" if x is not None else "N/A"


def render_report_md(report: dict) -> str:
    """渲染 markdown 报告(纯函数)。"""
    r = report
    d, s, ev = r["dimensions"], r["sanitize"], r["evidence"]
    rec, hall, u = r["recall_pass"], r["hallucination"], r["usage"]
    sw = r["switches"]
    lines = [
        f"# 抽取管线冒烟报告({r['mode']})",
        "",
        f"- 生成时间: {r['generated_at']}",
        f"- 模型: {r['model']} · 章节: 第 {r['chapter_id']} 章《{r['chapter_title']}》"
        f"({r['chapter_chars']} 字)",
        "- 开关: " + " ".join(f"{k}={v}" for k, v in sw.items()),
        "",
        "## 抽取规模",
        "",
        f"- 人物 {r['extraction']['characters']} · 关系 {r['extraction']['relationships']}"
        f" · 地点 {r['extraction']['locations']} · 事件 {r['extraction']['events']}"
        f"(分段 {r['extraction']['segment_count']},截断={r['extraction']['is_truncated']})",
        "",
        "## Epic 1 关系维度",
        "",
        f"- 填充率: polarity {_pct(d['polarity_fill_rate'])}"
        f" · rel_subtype {_pct(d['rel_subtype_fill_rate'])}"
        f" · closeness {_pct(d['closeness_fill_rate'])}(共 {d['total_relations']} 条关系)",
        f"- 越界值拦截: polarity={s['invalid_dimension_values']['polarity']}"
        f" rel_subtype={s['invalid_dimension_values']['rel_subtype']}"
        f" closeness={s['invalid_dimension_values']['closeness']}"
        f"(合计 {s['invalid_dimension_total']})",
        f"- 投票 override: {s['vote_overrides']} 次"
        f"(废票 {s['vote_samples_dropped']} 张,参与投票关系 {d['relations_with_vote']} 条)",
        "",
        "## Epic 3 证据锚定",
        "",
        f"- evidence 覆盖率: 关系 {_pct(ev['relation_coverage'])}"
        f" · 事件 {_pct(ev['event_coverage'])} · 总体 {_pct(ev['overall_coverage'])}",
        f"- span 定位率: {_pct(ev['span_located_rate'])}"
        f"({ev['span_located']}/{ev['non_empty']} 条非空 evidence)",
        "",
        "## Epic 4 recall pass 与幻觉判定",
        "",
        f"- recall 补漏: 人物 {rec['characters']} · 关系 {rec['relationships']}"
        f" · 事件 {rec['events']}",
        f"- 幻觉候选 {len(hall['candidates'])} 个: "
        + (", ".join(hall["candidates"]) or "(无)"),
    ]
    for a in hall["actions"]:
        lines.append(
            f"  - {a['name']}: {a['action']}"
            f"(is_real={a.get('is_real')}, confidence={a.get('confidence')}, {a.get('reason', '')})"
        )
    lines += [
        "",
        "## 成本",
        "",
        f"- LLM 调用 {u['llm_calls']} 次 · 输入 {u['prompt_tokens']:,} tok"
        f" · 输出 {u['completion_tokens']:,} tok ≈ **${u['cost_usd']:.4f}**",
        "",
    ]
    if r["findings"]:
        lines += ["## 发现与建议", ""]
        lines += [f"- {f}" for f in r["findings"]]
        lines.append("")
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path = REPORT_DIR) -> tuple[Path, Path]:
    """落盘 JSON + MD,文件名带日期(与 judge 报告同约定)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    json_path = out_dir / f"smoke_extraction_quality_{date_str}.json"
    md_path = out_dir / f"smoke_extraction_quality_{date_str}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md_path.write_text(render_report_md(report), encoding="utf-8")
    return json_path, md_path


def derive_findings(report: dict) -> list[str]:
    """根据报告指标自动生成"发现与建议"(规则化,真实跑后人工补充)。"""
    findings: list[str] = []
    s, ev, d = report["sanitize"], report["evidence"], report["dimensions"]
    if d.get("total_relations") == 0:
        findings.append(
            "本章最终 0 条关系,Epic 1 维度填充率不可测;真实跑中 LLM 抽出的"
            "关系实体(如 群猴)可能被 FactValidator 泛称规则过滤导致关系级联删除,"
            "建议用关系更密集的章节复测,或评估泛称人物过滤对关系的连带影响(设计级)。"
        )
    if s["invalid_dimension_total"]:
        findings.append(
            f"模型返回了 {s['invalid_dimension_total']} 个越界维度值"
            f"(明细 {s['invalid_dimension_values']}),已被 sanitize 拦截置 None;"
            "可考虑在 dimension guide 中强化取值表约束,或按值表做模糊纠偏。"
        )
    if ev["span_located_rate"] is not None and ev["span_located_rate"] < 0.9:
        findings.append(
            f"evidence span 定位率仅 {_pct(ev['span_located_rate'])},"
            "存在编造/改写引用;可考虑对定位失败的 evidence 降权或要求重引。"
        )
    if ev["overall_coverage"] is not None and ev["overall_coverage"] < 1.0:
        findings.append(
            f"evidence 覆盖率 {_pct(ev['overall_coverage'])},"
            "缺证据的记录已被降置信度并标记,留待 judge/人工复核。"
        )
    for field in ("polarity", "rel_subtype", "closeness"):
        rate = d.get(f"{field}_fill_rate")
        if rate is not None and rate < 0.8:
            findings.append(
                f"维度字段 {field} 填充率仅 {_pct(rate)},"
                "检查 dimension guide 注入是否生效或模型遵循度。"
            )
    if not findings:
        findings.append("各项指标未见明显异常。")
    return findings


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Epic 1–4 抽取管线端到端冒烟")
    parser.add_argument("--novel-file", type=Path, default=DEFAULT_NOVEL_FILE)
    parser.add_argument("--chapter", type=int, default=1, help="章节号(默认 1)")
    parser.add_argument("--genre", default="fantasy", help="题材提示(默认 fantasy)")
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式,不调真实 API")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划,不调用 LLM")
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    title, chapter_text = load_chapter(args.novel_file, args.chapter)
    switches = current_switches()
    print(f"[smoke] 文件: {args.novel_file.name} 第 {args.chapter} 章《{title}》"
          f"({len(chapter_text)} 字)")
    print(f"[smoke] 开关: {switches}")

    if args.dry_run:
        print("[smoke] dry-run: 计划执行 ChapterFactExtractor → FactValidator"
              " → hallucination_reviewer,不调用 LLM。")
        return 0

    if args.mock:
        llm = MockLLM()
        from src.infra import config
        model = f"mock({config.get_model_name()})"
        mode = "mock"
        review_log_path = args.out_dir / "smoke_hallucination_review_log.mock.jsonl"
    else:
        from src.infra import config
        from src.infra.llm_client import get_llm_client

        llm = get_llm_client()
        model = config.get_model_name()
        mode = "real"
        review_log_path = SMOKE_REVIEW_LOG
        print(f"[smoke] LLM: provider={config.LLM_PROVIDER} model={model}")

    smoke = await run_smoke(
        chapter_text,
        llm,
        chapter_id=args.chapter,
        genre_hint=args.genre,
        review_log_path=review_log_path,
    )
    report = build_report(
        smoke,
        mode=mode,
        model=model,
        chapter_title=title,
        chapter_id=args.chapter,
        chapter_chars=len(chapter_text),
    )
    report["findings"] = derive_findings(report)

    json_path, md_path = write_report(report, args.out_dir)
    print()
    print(render_report_md(report))
    print(f"[smoke] 报告已写入: {json_path}")
    print(f"[smoke]           {md_path}")
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    finally:
        # 与 test_quality_dashboard 同款防护:asyncio.run 后恢复主线程 loop
        asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    sys.exit(main())
