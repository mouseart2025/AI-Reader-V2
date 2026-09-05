"""Run-to-run 可复现性测量 harness (issue #70)。

对同一文本跑两次完整 fresh 分析(两个独立数据目录 runA/runB,经
AI_READER_DATA_DIR 隔离的子进程),然后比较五层 overlap:

  1. entity    人物/地点/物品/势力实体 canonical 集合(人物经 alias_map 归一)
  2. alias     alias_map 条目 (alias → canonical 对)
  3. relation  关系三元组 (canonical(a), canonical(b), 归一化类型)
  4. event     事件近似匹配(同章内 标题相似度×0.6 + 参与者 Jaccard×0.4,
               阈值 0.5,贪心一对一)
  5. hierarchy 地点层级 parent 边集合 (child → parent)

每层报 |A∩B| / |A∪B| (Jaccard) 和各自规模,最后汇总总表。

实现方式:父进程两次以子进程驱动 worker 模式(同一脚本 --worker),
worker 内复用生产路径 —— 章节切分 → EntityPreScanner → AnalysisService
(ChapterFactExtractor → FactValidator → 幻觉判定 → 落库) →
geo 链(build_default_orchestrator) + entity_resolver,与线上分析一致;
worker 结束时从本 run 的 sqlite 产出 snapshot.json,父进程只做集合运算。

LLM 配置走环境变量 (LLM_PROVIDER/LLM_BASE_URL/LLM_MODEL),key 从
backend/.env dotenv 自动加载(src.infra.config),本脚本绝不打印 key。

Usage:
    cd backend && .venv/bin/python scripts/repro_check.py \
        --novel-file /tmp/sanguo-20.txt --chapters 10
    .venv/bin/python scripts/repro_check.py ... --json          # JSON 打到 stdout
    .venv/bin/python scripts/repro_check.py ... --out-dir DIR   # 报告落盘目录

    # worker 模式(一般由父进程调用,可单独调试):
    AI_READER_DATA_DIR=/tmp/repro/runA .venv/bin/python scripts/repro_check.py \
        --worker --novel-file /tmp/sanguo-20.txt --chapters 10 \
        --snapshot-out /tmp/repro/runA/snapshot.json

Output:
    <out-dir>/repro_check_{YYYYmmdd_HHMMSS}.md / .json
    <out-dir>/repro_runs_{YYYYmmdd_HHMMSS}/runA|runB/  (独立数据目录 + snapshot.json)
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "repro-check-v1"
SNAPSHOT_SCHEMA = "repro-snapshot-v1"
REPORT_DIR = _BACKEND_DIR / "audit_reports"

# event 近似匹配口径(写进报告):
#   仅同章事件互配;score = 0.6×标题 SequenceMatcher 相似度
#   + 0.4×参与者集合 Jaccard;score ≥ EVENT_MATCH_THRESHOLD 视为同一事件,
#   全局贪心(分数降序)一对一匹配。
EVENT_MATCH_THRESHOLD = 0.5
EVENT_TITLE_WEIGHT = 0.6
EVENT_PARTICIPANT_WEIGHT = 0.4


# ── 纯函数:canonical 归一 / 集合 overlap / event 近似匹配(可单测)───────

def canon(name: str, alias_map: dict[str, str]) -> str:
    """经 alias_map 归一实体名;不在映射中则返回自身。"""
    return alias_map.get(name, name)


def jaccard_stats(a: set, b: set) -> dict:
    """|A∩B| / |A∪B| / Jaccard;两边皆空视为完全一致 (1.0)。"""
    inter = len(a & b)
    union = len(a | b)
    return {
        "size_a": len(a),
        "size_b": len(b),
        "intersection": inter,
        "union": union,
        "jaccard": (inter / union) if union else 1.0,
    }


def event_similarity(ea: dict, eb: dict) -> float:
    """事件相似度:标题序列相似度 ×0.6 + 参与者 Jaccard ×0.4。"""
    title_sim = difflib.SequenceMatcher(
        None, ea.get("summary", ""), eb.get("summary", "")
    ).ratio()
    pa, pb = set(ea.get("participants", [])), set(eb.get("participants", []))
    part_sim = len(pa & pb) / len(pa | pb) if (pa | pb) else 0.0
    return EVENT_TITLE_WEIGHT * title_sim + EVENT_PARTICIPANT_WEIGHT * part_sim


def match_events(
    events_a: list[dict],
    events_b: list[dict],
    threshold: float = EVENT_MATCH_THRESHOLD,
) -> dict:
    """同章事件贪心一对一匹配(候选对按分数降序,双方各只配一次)。"""
    candidates: list[tuple[float, int, int]] = []
    for i, ea in enumerate(events_a):
        for j, eb in enumerate(events_b):
            if ea.get("chapter") != eb.get("chapter"):
                continue
            score = event_similarity(ea, eb)
            if score >= threshold:
                candidates.append((score, i, j))
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: list[dict] = []
    for score, i, j in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append({
            "score": round(score, 4),
            "chapter": events_a[i].get("chapter"),
            "summary_a": events_a[i].get("summary", ""),
            "summary_b": events_b[j].get("summary", ""),
        })

    matched = len(pairs)
    union = len(events_a) + len(events_b) - matched
    return {
        "size_a": len(events_a),
        "size_b": len(events_b),
        "intersection": matched,
        "union": union,
        "jaccard": (matched / union) if union else 1.0,
        "match_pairs": pairs,
        "threshold": threshold,
    }


def _entity_set(snapshot: dict, entity_type: str) -> set[str]:
    return {f"{entity_type}:{n}" for n in snapshot["entities"].get(entity_type, [])}


def compare_snapshots(snap_a: dict, snap_b: dict) -> dict:
    """五层 overlap 对比(纯函数,输入两个 snapshot dict)。"""
    layers: dict[str, dict] = {}

    # 1. entity:四类实体合并集合(带类型前缀),另附分类型明细
    ent_a = set().union(*(_entity_set(snap_a, t) for t in ("person", "location", "item", "org")))
    ent_b = set().union(*(_entity_set(snap_b, t) for t in ("person", "location", "item", "org")))
    ent = jaccard_stats(ent_a, ent_b)
    ent["by_type"] = {
        t: jaccard_stats(_entity_set(snap_a, t), _entity_set(snap_b, t))
        for t in ("person", "location", "item", "org")
    }
    ent["only_in_a"] = sorted(ent_a - ent_b)[:20]
    ent["only_in_b"] = sorted(ent_b - ent_a)[:20]
    layers["entity"] = ent

    # 2. alias:alias_map 条目对 (alias != canonical)
    def _alias_pairs(snap: dict) -> set[tuple[str, str]]:
        return {
            (a, c) for a, c in snap.get("alias_map", {}).items() if a != c
        }

    pa, pb = _alias_pairs(snap_a), _alias_pairs(snap_b)
    al = jaccard_stats(pa, pb)
    al["only_in_a"] = sorted(f"{a}→{c}" for a, c in pa - pb)[:20]
    al["only_in_b"] = sorted(f"{a}→{c}" for a, c in pb - pa)[:20]
    layers["alias"] = al

    # 3. relation:三元组 canonical(a)|canonical(b)|归一化类型
    ra = {tuple(r) for r in snap_a.get("relations", [])}
    rb = {tuple(r) for r in snap_b.get("relations", [])}
    rel = jaccard_stats(ra, rb)
    rel["only_in_a"] = sorted("|".join(r) for r in ra - rb)[:20]
    rel["only_in_b"] = sorted("|".join(r) for r in rb - ra)[:20]
    layers["relation"] = rel

    # 4. event:近似匹配
    layers["event"] = match_events(
        snap_a.get("events", []), snap_b.get("events", [])
    )

    # 5. hierarchy:parent 边 child->parent
    ha = {tuple(e) for e in snap_a.get("hierarchy_edges", [])}
    hb = {tuple(e) for e in snap_b.get("hierarchy_edges", [])}
    hier = jaccard_stats(ha, hb)
    hier["only_in_a"] = sorted(f"{c}→{p}" for c, p in ha - hb)[:20]
    hier["only_in_b"] = sorted(f"{c}→{p}" for c, p in hb - ha)[:20]
    layers["hierarchy"] = hier

    # 汇总:五层 Jaccard 的宏平均
    macro = sum(l["jaccard"] for l in layers.values()) / len(layers)
    return {"layers": layers, "macro_jaccard": macro}


# ── worker:单 run 完整 fresh 分析 + snapshot 产出 ─────────────────────

class _UsageTracker:
    """包装 LLM client 单例,累计整轮全部调用(预扫/抽取/场景/幻觉/ER)的用量。"""

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


_TERMINAL_TASK_STATUS = ("completed", "completed_with_errors", "failed", "cancelled")


async def build_snapshot(novel_id: str, chapters: int, label: str) -> dict:
    """从本 run 的 sqlite 产出五层 snapshot(复用生产聚合逻辑)。"""
    from src.db import chapter_fact_store
    from src.db.sqlite_db import get_connection
    from src.db import world_structure_store
    from src.services import alias_resolver
    from src.services.relation_utils import normalize_relation_type

    fact_rows = await chapter_fact_store.get_all_chapter_facts(novel_id)

    # chapters 表 PK → chapter_num 映射
    conn = await get_connection()
    try:
        cur = await conn.execute(
            "SELECT id, chapter_num, analysis_status FROM chapters WHERE novel_id = ?",
            (novel_id,),
        )
        ch_rows = await cur.fetchall()
    finally:
        await conn.close()
    pk_to_num = {r["id"]: r["chapter_num"] for r in ch_rows}
    failed_chapters = sorted(
        r["chapter_num"] for r in ch_rows if r["analysis_status"] == "failed"
    )

    # alias_map 含 entity_resolver 的 llm_merge override(Epic 2 产物)
    alias_map = await alias_resolver.build_alias_map(novel_id)

    entities: dict[str, set[str]] = {
        "person": set(), "location": set(), "item": set(), "org": set(),
    }
    relations: set[tuple[str, str, str]] = set()
    events: list[dict] = []

    for row in fact_rows:
        fact = row["fact"]
        ch_num = pk_to_num.get(row["chapter_id"], row["chapter_id"])
        for c in fact.get("characters", []):
            name = (c.get("name") or "").strip()
            if name:
                entities["person"].add(canon(name, alias_map))
        for loc in fact.get("locations", []):
            name = (loc.get("name") or "").strip()
            if name:
                entities["location"].add(name)
        for ie in fact.get("item_events", []):
            name = (ie.get("item_name") or "").strip()
            if name:
                entities["item"].add(name)
        for oe in fact.get("org_events", []):
            name = (oe.get("org_name") or "").strip()
            if name:
                entities["org"].add(name)
        for r in fact.get("relationships", []):
            a = (r.get("person_a") or "").strip()
            b = (r.get("person_b") or "").strip()
            rtype = normalize_relation_type((r.get("relation_type") or "").strip())
            if a and b:
                relations.add((canon(a, alias_map), canon(b, alias_map), rtype))
        for e in fact.get("events", []):
            events.append({
                "chapter": ch_num,
                "type": (e.get("type") or "").strip(),
                "summary": (e.get("summary") or "").strip(),
                "participants": sorted({
                    canon((p or "").strip(), alias_map)
                    for p in e.get("participants", [])
                    if (p or "").strip()
                }),
            })

    # 层级 parent 边(geo 链产物已写回 world_structures)
    hierarchy_edges: list[list[str]] = []
    ws = await world_structure_store.load(novel_id)
    if ws and ws.location_parents:
        hierarchy_edges = sorted(
            [child, parent] for child, parent in ws.location_parents.items() if parent
        )

    return {
        "schema": SNAPSHOT_SCHEMA,
        "label": label,
        "novel_id": novel_id,
        "chapters": chapters,
        "facts_count": len(fact_rows),
        "failed_chapters": failed_chapters,
        "alias_map": dict(sorted(alias_map.items())),
        "entities": {t: sorted(s) for t, s in entities.items()},
        "relations": sorted([list(r) for r in relations]),
        "events": events,
        "hierarchy_edges": hierarchy_edges,
    }


async def run_worker(args: argparse.Namespace) -> int:
    """单 run:初始化独立 DB → 切章入库 → 完整分析 → post 链 → snapshot。"""
    from src.db import novel_store
    from src.db.sqlite_db import init_db
    from src.db import analysis_task_store
    from src.infra import config
    from src.infra import llm_client as _lc
    from src.infra.context_budget import detect_and_update_context_window
    from src.services.analysis_service import AnalysisService
    from src.utils.chapter_splitter import split_chapters

    t0 = time.time()
    await init_db()

    text = args.novel_file.read_text(encoding="utf-8")
    all_chapters = split_chapters(text)
    chapters = all_chapters[: args.chapters]
    if len(chapters) < args.chapters:
        print(f"[worker-{args.label}] 警告: 只切出 {len(all_chapters)} 章,"
              f"不足 --chapters {args.chapters}", flush=True)
    n = len(chapters)

    novel_id = f"repro-{args.label}"
    await novel_store.insert_novel(
        novel_id=novel_id,
        title=args.novel_file.stem,
        author=None,
        file_hash=f"repro-{args.label}-{len(text)}",
        total_chapters=n,
        total_words=sum(c.word_count for c in chapters),
    )
    await novel_store.insert_chapters(novel_id, chapters)
    print(f"[worker-{args.label}] 入库 {n} 章,共 "
          f"{sum(c.word_count for c in chapters)} 字", flush=True)

    # 与 API lifespan 一致:先探测 context window,避免 8K 保守预算截断章节
    ctx = await detect_and_update_context_window()
    print(f"[worker-{args.label}] context window: {ctx}", flush=True)

    # 包装 LLM 单例,统计整轮用量(不打印任何 key)
    tracker = _UsageTracker(_lc.get_llm_client())
    _lc._client = tracker

    service = AnalysisService()

    # 捕获 post-analysis 后台任务(geo 链 + entity resolution),
    # 变 fire-and-forget 为可 await,保证 snapshot 在其完成后产出
    post_tasks: list[asyncio.Task] = []

    def _capture_post_tasks(nid: str) -> None:
        post_tasks.append(asyncio.create_task(service._run_geo_pipeline(nid)))
        if config.ENTITY_RESOLUTION_ENABLED:
            post_tasks.append(asyncio.create_task(service._auto_entity_resolution(nid)))

    service._schedule_post_analysis = _capture_post_tasks  # type: ignore[method-assign]

    task_id = await service.start(novel_id, 1, n)
    print(f"[worker-{args.label}] 分析任务 {task_id[:8]} 启动 "
          f"(provider={config.LLM_PROVIDER} model={config.get_model_name()})",
          flush=True)

    # 轮询任务状态直到终态
    status = "running"
    last_ch = 0
    while status not in _TERMINAL_TASK_STATUS:
        await asyncio.sleep(5)
        task = await analysis_task_store.get_task(task_id)
        if not task:
            raise RuntimeError(f"任务 {task_id} 消失")
        status = task["status"]
        cur = task.get("current_chapter") or 0
        if cur != last_ch:
            print(f"[worker-{args.label}] 进度: 第 {cur}/{n} 章 "
                  f"({time.time() - t0:.0f}s)", flush=True)
            last_ch = cur

    print(f"[worker-{args.label}] 分析终态: {status},等待 post-analysis 链...",
          flush=True)
    results = await asyncio.gather(*post_tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            print(f"[worker-{args.label}] post-analysis 任务异常(非致命): {r}",
                  flush=True)

    snapshot = await build_snapshot(novel_id, n, args.label)
    snapshot.update({
        "task_status": status,
        "model": config.get_model_name(),
        "provider": config.LLM_PROVIDER,
        "context_window": config.CONTEXT_WINDOW_SIZE,
        "switches": {
            "RELATION_DIMENSIONS_ENABLED": config.RELATION_DIMENSIONS_ENABLED,
            "ENTITY_RESOLUTION_ENABLED": config.ENTITY_RESOLUTION_ENABLED,
            "EVIDENCE_GROUNDING_ENABLED": config.EVIDENCE_GROUNDING_ENABLED,
            "RECALL_PASS_ENABLED": config.RECALL_PASS_ENABLED,
            "HALLUCINATION_REVIEW_ENABLED": config.HALLUCINATION_REVIEW_ENABLED,
        },
        "usage": {
            "llm_calls": tracker.calls,
            "prompt_tokens": tracker.prompt_tokens,
            "completion_tokens": tracker.completion_tokens,
            "total_tokens": tracker.total_tokens,
        },
        "elapsed_s": round(time.time() - t0, 1),
    })

    args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot_out.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[worker-{args.label}] snapshot 已写入 {args.snapshot_out} "
          f"(耗时 {snapshot['elapsed_s']}s, LLM 调用 {tracker.calls} 次)",
          flush=True)
    return 0 if status in ("completed", "completed_with_errors") else 1


# ── 报告渲染 ─────────────────────────────────────────────────────────

def _pct(x: float) -> str:
    return f"{x:.1%}"


def render_report_md(report: dict) -> str:
    """渲染 markdown 总表(纯函数)。"""
    lines = [
        "# Run-to-run 可复现性报告 (issue #70)",
        "",
        f"- 生成时间: {report['generated_at']}",
        f"- 文本: {report['novel_file']} (前 {report['chapters']} 章 ×2 轮)",
        f"- 模型: {report['model']} · provider={report['provider']}",
        f"- runA: {report['runs']['A']['task_status']}"
        f"(耗时 {report['runs']['A']['elapsed_s']}s,"
        f" LLM {report['runs']['A']['usage']['llm_calls']} 次)"
        f" · runB: {report['runs']['B']['task_status']}"
        f"(耗时 {report['runs']['B']['elapsed_s']}s,"
        f" LLM {report['runs']['B']['usage']['llm_calls']} 次)",
        "",
        "## 五层 overlap 总表",
        "",
        "| 层 | |A| | |B| | |A∩B| | |A∪B| | Jaccard |",
        "|----|----|----|-------|-------|---------|",
    ]
    for name, cn in (("entity", "实体"), ("alias", "别名映射"), ("relation", "关系"),
                     ("event", "事件"), ("hierarchy", "层级")):
        l = report["layers"][name]
        lines.append(
            f"| {cn} ({name}) | {l['size_a']} | {l['size_b']} | "
            f"{l['intersection']} | {l['union']} | {_pct(l['jaccard'])} |"
        )
    lines += [
        f"| **宏平均** | | | | | **{_pct(report['macro_jaccard'])}** |",
        "",
        "## 口径说明",
        "",
        "- entity: 人物经 alias_map(含 entity_resolver llm_merge 产物)归一后,"
        "与地点/物品/势力合并为带类型前缀的集合;精确匹配。",
        "- alias: alias_map 中 alias≠canonical 的 (alias, canonical) 条目对;精确匹配。",
        "- relation: (canonical(a), canonical(b), 归一化类型) 三元组,有向,"
        "类型经 relation_utils.normalize_relation_type 归一;精确匹配。",
        f"- event: **近似匹配** —— 仅同章事件互配,"
        f"score = {EVENT_TITLE_WEIGHT}×标题 SequenceMatcher 相似度"
        f" + {EVENT_PARTICIPANT_WEIGHT}×参与者集合 Jaccard,"
        f"score ≥ {EVENT_MATCH_THRESHOLD} 视为同一事件,全局分数降序贪心一对一;"
        f"Jaccard = 匹配对数 / (|A|+|B|−匹配对数)。",
        "- hierarchy: world_structures.location_parents 的 (child→parent) 边集合"
        "(geo 链 Edmonds+SuffixNormalizer 重建后);精确匹配。",
        "",
        "## 分类型实体 overlap",
        "",
        "| 类型 | |A| | |B| | |A∩B| | Jaccard |",
        "|------|----|----|-------|---------|",
    ]
    for t, d in report["layers"]["entity"]["by_type"].items():
        lines.append(
            f"| {t} | {d['size_a']} | {d['size_b']} | "
            f"{d['intersection']} | {_pct(d['jaccard'])} |"
        )
    lines.append("")

    # 差异样例
    for name, cn in (("entity", "实体"), ("alias", "别名"), ("relation", "关系"),
                     ("hierarchy", "层级边")):
        l = report["layers"][name]
        if l.get("only_in_a") or l.get("only_in_b"):
            lines.append(f"### {cn} 差异样例 (各至多 20 条)")
            lines.append("")
            for s in l.get("only_in_a", []):
                lines.append(f"- 仅 A: {s}")
            for s in l.get("only_in_b", []):
                lines.append(f"- 仅 B: {s}")
            lines.append("")

    u_a, u_b = report["runs"]["A"]["usage"], report["runs"]["B"]["usage"]
    lines += [
        "## 成本与耗时",
        "",
        f"- runA: {u_a['llm_calls']} 次调用 · 输入 {u_a['prompt_tokens']:,} tok"
        f" · 输出 {u_a['completion_tokens']:,} tok · {report['runs']['A']['elapsed_s']}s",
        f"- runB: {u_b['llm_calls']} 次调用 · 输入 {u_b['prompt_tokens']:,} tok"
        f" · 输出 {u_b['completion_tokens']:,} tok · {report['runs']['B']['elapsed_s']}s",
        f"- 两轮合计 ≈ ${report['total_cost_usd']:.4f}"
        f"(约 ¥{report['total_cost_usd'] * 7.2:.2f}),定价取 cost_service.get_pricing",
        "",
        f"- 数据目录: {report['runs_dir']}",
        "",
    ]
    if report.get("notes"):
        lines += ["## 备注", ""]
        lines += [f"- {n}" for n in report["notes"]]
        lines.append("")
    return "\n".join(lines)


# ── 父进程:两轮子进程 + 汇总 ─────────────────────────────────────────

def _run_one(args: argparse.Namespace, label: str, runs_dir: Path) -> Path:
    """以子进程跑一轮 fresh 分析,返回 snapshot.json 路径。"""
    data_dir = runs_dir / f"run{label}"
    snapshot_out = data_dir / "snapshot.json"
    env = dict(os.environ)
    env["AI_READER_DATA_DIR"] = str(data_dir)
    cmd = [
        sys.executable, str(Path(__file__).resolve()),
        "--worker", "--label", label,
        "--novel-file", str(args.novel_file),
        "--chapters", str(args.chapters),
        "--snapshot-out", str(snapshot_out),
    ]
    print(f"[repro] ── run{label} 启动 (AI_READER_DATA_DIR={data_dir})", flush=True)
    proc = subprocess.run(cmd, env=env, cwd=str(_BACKEND_DIR))
    if proc.returncode != 0 or not snapshot_out.exists():
        raise RuntimeError(
            f"run{label} 失败 (exit={proc.returncode}),"
            f"snapshot: {snapshot_out}"
        )
    return snapshot_out


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run-to-run 可复现性测量 (issue #70)"
    )
    parser.add_argument("--novel-file", type=Path, required=True)
    parser.add_argument("--chapters", type=int, default=10, help="取前 N 章")
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--json", action="store_true",
                        help="stdout 输出完整 JSON(默认打印 markdown)")
    parser.add_argument("--worker", action="store_true",
                        help=argparse.SUPPRESS)  # 内部:子进程单 run 模式
    parser.add_argument("--label", default="A", help=argparse.SUPPRESS)
    parser.add_argument("--snapshot-out", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.worker:
        if not args.snapshot_out:
            parser.error("--worker 需要 --snapshot-out")
        return await run_worker(args)

    if not args.novel_file.exists():
        parser.error(f"文件不存在: {args.novel_file}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    runs_dir = args.out_dir / f"repro_runs_{ts}"
    runs_dir.mkdir(parents=True, exist_ok=True)

    snap_paths = {
        label: _run_one(args, label, runs_dir) for label in ("A", "B")
    }
    snap_a = json.loads(snap_paths["A"].read_text(encoding="utf-8"))
    snap_b = json.loads(snap_paths["B"].read_text(encoding="utf-8"))

    comparison = compare_snapshots(snap_a, snap_b)

    # 成本:两轮 token 用量 × 生产定价表
    from src.services.cost_service import get_pricing

    model = snap_a.get("model", "")
    price_in, price_out = get_pricing(model)
    total_cost = sum(
        s["usage"]["prompt_tokens"] / 1_000_000 * price_in
        + s["usage"]["completion_tokens"] / 1_000_000 * price_out
        for s in (snap_a, snap_b)
    )

    notes: list[str] = []
    for label, snap in (("A", snap_a), ("B", snap_b)):
        if snap.get("failed_chapters"):
            notes.append(
                f"run{label} 有失败章节: {snap['failed_chapters']}"
                f"(overlap 在成功章节子集上计算)"
            )
    if snap_a.get("model") != snap_b.get("model"):
        notes.append("两轮模型不一致,结果不可比!")

    report = {
        "schema": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "novel_file": str(args.novel_file),
        "chapters": args.chapters,
        "model": model,
        "provider": snap_a.get("provider", ""),
        "switches": snap_a.get("switches", {}),
        "runs": {
            "A": {k: snap_a[k] for k in ("task_status", "usage", "elapsed_s")},
            "B": {k: snap_b[k] for k in ("task_status", "usage", "elapsed_s")},
        },
        "total_cost_usd": round(total_cost, 6),
        "runs_dir": str(runs_dir),
        "notes": notes,
        **comparison,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / f"repro_check_{ts}.json"
    md_path = args.out_dir / f"repro_check_{ts}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    md_path.write_text(render_report_md(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print()
        print(render_report_md(report))
    print(f"[repro] 报告已写入: {json_path}")
    print(f"[repro]            {md_path}")
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    finally:
        # 与 test_quality_dashboard 同款防护:asyncio.run 后恢复主线程 loop
        asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    sys.exit(main())
