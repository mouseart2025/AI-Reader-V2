"""Aggregate GraphRAG-style / HippoRAG-style / ARBOR comparison table (Epic 5, FR-5.1).

Reads:
- paper/evaluation/v071/baselines/graphrag_style/{slug}.json   (Louvain baseline)
- paper/evaluation/v071/baselines/hipporag_style/{slug}.json   (PPR baseline)
- ARBOR deployed hierarchies from world_structures.location_parents in the DB
- gold fixtures from backend/tests/fixtures/

Writes:
- paper/evaluation/v074/graphrag-comparison-2026-08-30/comparison.json
- paper/evaluation/v074/graphrag-comparison-2026-08-30/comparison.md

Cost model (documented estimates, NOT measured API calls):
- Extraction phase is held FIXED across methods (all baselines reuse ARBOR's
  per-chapter extraction from the same DB snapshot), so extraction tokens are
  excluded from the comparison by construction.
- MS-GraphRAG-style pipeline additionally generates one LLM community report
  per community per level. Report-call token estimate: ~3000 input + ~800
  output tokens per community (GraphRAG default report prompts carry the
  community's entity/relation records; order-of-magnitude estimate).
- HippoRAG-style approximation and ARBOR aggregation are pure algorithms:
  0 additional LLM tokens.
- External anchors (GraphRAG-Bench, arXiv:2506.05690v3): MS-GraphRAG global
  search ~330k tokens PER QUERY vs HippoRAG2 ~1k; MS-GraphRAG entity
  resolution is name-matching only.

Usage:
    cd backend && .venv/bin/python scripts/build_graphrag_comparison.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from graphrag_style_baseline import (  # noqa: E402
    DB_PATH,
    NOVELS,
    OUTPUT_ROOT,
    PPR_OUTPUT_ROOT,
    compute_metrics,
)

PAPER_EVAL = Path.home() / "Baiduyun/AISoul/ai-reader-internal/paper/evaluation"
OUT_DIR = PAPER_EVAL / "v074" / "graphrag-comparison-2026-08-30"

# Cost model constants (documented estimates)
COMMUNITY_REPORT_INPUT_TOKENS = 3000
COMMUNITY_REPORT_OUTPUT_TOKENS = 800
ARBOR_AGG_TOKENS = 0  # Edmonds MWA is a pure graph algorithm
ARBOR_AGG_RUNTIME_MS_PAPER = 130  # paper-reported, ~1k-node graphs, single machine

PIPELINE_VERSION = "v0.74.1"
EXTRACTION_SNAPSHOT = ("chapter_facts / world_structures DB snapshot, "
                       "extracted with v0.71-era pipeline (2026-04); frozen for paper")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def arbor_deployed_metrics(slug: str, gold_locs: list[dict]) -> dict:
    """Compute the same metrics for ARBOR's deployed hierarchy (from DB)."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT structure_json, updated_at FROM world_structures WHERE novel_id=?",
        (NOVELS[slug]["novel_id"],),
    ).fetchone()
    conn.close()
    if not row:
        return {"error": "no world_structure"}
    structure = json.loads(row[0])
    parent_map = {k: v for k, v in (structure.get("location_parents") or {}).items() if v}
    m = compute_metrics(parent_map, gold_locs)
    m["deployed_updated_at"] = row[1]
    return m


def corpus_stats(slug: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    n_ch, n_chars = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0) FROM chapters WHERE novel_id=?",
        (NOVELS[slug]["novel_id"],),
    ).fetchone()
    n_facts = conn.execute(
        "SELECT COUNT(*) FROM chapter_facts WHERE novel_id=?",
        (NOVELS[slug]["novel_id"],),
    ).fetchone()[0]
    conn.close()
    return {"chapters": n_ch, "chapter_facts": n_facts, "chars": n_chars}


def main() -> None:
    today = date.today().isoformat()
    comparison: dict = {
        "generated": today,
        "pipeline_version": PIPELINE_VERSION,
        "extraction_snapshot": EXTRACTION_SNAPSHOT,
        "cost_model": {
            "extraction_tokens": "excluded (held fixed: all methods reuse the same extraction snapshot)",
            "community_report_tokens_per_call": {
                "input": COMMUNITY_REPORT_INPUT_TOKENS,
                "output": COMMUNITY_REPORT_OUTPUT_TOKENS,
                "basis": "order-of-magnitude estimate from MS-GraphRAG default community-report prompts",
            },
            "external_anchors": {
                "graphrag_bench": ("arXiv:2506.05690v3 — MS-GraphRAG global search ~330k tokens/query "
                                   "vs HippoRAG2 ~1k tokens/query; MS-GraphRAG entity resolution is "
                                   "name-matching only"),
            },
        },
        "novels": {},
    }

    md_rows: list[str] = []

    for slug, meta in NOVELS.items():
        gold_path = Path(__file__).parent.parent / meta["gold_file"]
        gold_locs = json.loads(gold_path.read_text()).get("locations", [])

        louvain = load_json(OUTPUT_ROOT / f"{slug}.json")
        ppr = load_json(PPR_OUTPUT_ROOT / f"{slug}.json")
        arbor = arbor_deployed_metrics(slug, gold_locs)
        corpus = corpus_stats(slug)

        n_reports = sum(louvain["debug"]["communities_per_level"])
        report_tokens = n_reports * (COMMUNITY_REPORT_INPUT_TOKENS + COMMUNITY_REPORT_OUTPUT_TOKENS)

        def topo(m: dict) -> dict:
            return m.get("topology_vs_gold", {})

        entry = {
            "title": meta["title"],
            "corpus": corpus,
            "methods": {
                "arbor_deployed": {
                    "description": "ARBOR full pipeline (voting + priors + Edmonds MWA), deployed hierarchy from DB",
                    "metrics": arbor,
                    "aggregation_extra_llm_tokens": ARBOR_AGG_TOKENS,
                    "aggregation_runtime_ms": f"~{ARBOR_AGG_RUNTIME_MS_PAPER} (paper-reported, Edmonds on ~1k nodes)",
                },
                "graphrag_style_louvain": {
                    "description": louvain["method"],
                    "metrics": louvain["metrics"],
                    "communities_per_level": louvain["debug"]["communities_per_level"],
                    "aggregation_runtime_ms_measured": louvain.get("aggregation_runtime_ms"),
                    "community_report_calls_if_full_ms_graphrag": n_reports,
                    "community_report_tokens_est": report_tokens,
                },
                "hipporag_style_ppr": {
                    "description": ppr["method"],
                    "metrics": ppr["metrics"],
                    "aggregation_runtime_ms_measured": ppr.get("aggregation_runtime_ms"),
                    "aggregation_extra_llm_tokens": 0,
                },
            },
        }
        # Gold coverage denominator (exact-match topology metrics)
        gold_pairs = {l["name"]: l["correct_parent"] for l in gold_locs
                      if l.get("name") and l.get("tier") != "DELETE" and l.get("correct_parent")}
        entry["gold_parent_pairs"] = len(gold_pairs)
        comparison["novels"][slug] = entry

        # Markdown rows
        for method_key, label in [
            ("arbor_deployed", "ARBOR (deployed)"),
            ("graphrag_style_louvain", "Louvain on co-occurrence (MS-GraphRAG core)"),
            ("hipporag_style_ppr", "PPR attachment (HippoRAG-style approx.)"),
        ]:
            m = entry["methods"][method_key]["metrics"]
            t = topo(m)
            md_rows.append(
                f"| {meta['title']} | {label} | {m['total_nodes']} | {m['root_count']} "
                f"| {'✓' if m['valid_tree'] else '✗'} | {m['max_children']} | {m['avg_depth']:.2f} "
                f"| {t.get('parent_precision', 0):.3f} | {t.get('parent_recall', 0):.3f} "
                f"| {t.get('chain_accuracy', 0):.3f} |"
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, default=str))

    gold_desc = " / ".join(
        f"{meta['title']} {comparison['novels'][slug]['gold_parent_pairs']} 对"
        for slug, meta in NOVELS.items()
    )

    md = f"""# GraphRAG 系 vs ARBOR 构图对比 — 数据表 (Epic 5 / FR-5.1)

- 生成日期: {today}
- 管线版本: {PIPELINE_VERSION}（仓库当前版本）
- 抽取快照: {EXTRACTION_SNAPSHOT}
- 口径说明: 三种方法**共用同一份抽取结果**（同一 DB 快照），对比只隔离"聚合/构图算法"一层；
  金标准为 `backend/tests/fixtures/golden_standard_*.json`,DELETE tier 已按
  `compute_topology_metrics` 规则剔除。金标准 parent 标注对数:{gold_desc}
  (parent P/R 的分母;西游、红楼金标准为小样本人工标注,水浒 254 条)。
- 注意 1: 论文 Table 6 冻结数字（西游 19.6% / 红楼 11.1% parent precision）对应 2026-03-26
  金标准修正前的旧跑分；本表为当日重跑，西游 Louvain parent_P=0.214，方向与量级结论不变。
- 注意 2: 本表 parent P/R 为 **exact-match 拓扑口径**(`compute_topology_metrics`,要求
  predicted parent 与 gold `correct_parent` 完全一致);论文正文报告的是**勘误口径**
  (`hierarchy_validator.compute_metrics_from_gold`, 1 − 未解决人工勘误错误率),两者不同。
  勘误口径冻结值: 西游 0.9975 / 红楼 0.994 / 水浒 1.0(v071 benchmark)。
  ARBOR 行取自 live DB 的 world_structures(2026-04 更新),与金标准标注时点存在快照漂移
  (如别名变体 东土大唐/大唐国 计为 mismatch),exact-match 口径会低估 ARBOR 绝对值;
  冻结结构数字(paper Table 5/8, Hybrid): max_children 西游 67 / 红楼 76 / 水浒 47,
  root=1, valid ✓。方法间相对比较不受此影响(三方法同快照同口径)。

## 结构 + 金标准指标（同口径）

| Novel | Method | nodes | roots | valid tree | max children | avg depth | parent P | parent R | chain acc |
|-------|--------|-------|-------|-----------|--------------|-----------|----------|----------|-----------|
{chr(10).join(md_rows)}

## 构图阶段成本（聚合层，抽取成本各方法相同、已排除）

| Novel | MS-GraphRAG 全管线额外成本（社区报告,估算） | HippoRAG 风格近似 | ARBOR 聚合 |
|-------|--------------------------------------------|-------------------|-----------|
"""
    for slug, meta in NOVELS.items():
        e = comparison["novels"][slug]
        g = e["methods"]["graphrag_style_louvain"]
        p = e["methods"]["hipporag_style_ppr"]
        md += (f"| {meta['title']} | {g['community_report_calls_if_full_ms_graphrag']} 次报告调用 "
               f"≈ {g['community_report_tokens_est']:,} tokens(估) | 0 tokens, 实测 "
               f"{p['aggregation_runtime_ms_measured']:.0f} ms | 0 tokens, "
               f"~{ARBOR_AGG_RUNTIME_MS_PAPER} ms(论文报告值) |\n")

    md += """
成本口径: 社区报告 token 为量级估算（每社区每层 1 次调用，~3000 输入 + ~800 输出 tokens，
依据 MS-GraphRAG 默认报告 prompt 规模）。外部锚点（GraphRAG-Bench, arXiv:2506.05690v3）:
MS-GraphRAG 全局检索单次 query ~33 万 tokens,HippoRAG2 ~1k tokens/query;
MS-GraphRAG 实体消解仅按名字匹配。
"""
    (OUT_DIR / "comparison.md").write_text(md)
    print(f"Wrote {OUT_DIR / 'comparison.json'}")
    print(f"Wrote {OUT_DIR / 'comparison.md'}")

    # Sanity check vs frozen ablation numbers (pure_mwa_ablation.json Hybrid rows)
    frozen = {"xiyouji": 67, "honglou": 76, "shuihu": 47}
    for slug, expect_mc in frozen.items():
        got = comparison["novels"][slug]["methods"]["arbor_deployed"]["metrics"]["max_children"]
        status = "OK" if got == expect_mc else "MISMATCH"
        print(f"  sanity max_children[{slug}]: computed={got} frozen={expect_mc} -> {status}")


if __name__ == "__main__":
    main()
