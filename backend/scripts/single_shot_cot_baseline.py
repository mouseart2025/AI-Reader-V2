"""Single-shot LLM-CoT baseline — whole-novel hierarchy construction.

Given the SAME evidence that the Edmonds pipeline sees (location set + per-
location parent candidates with vote counts + optional short chapter
summaries), ask Claude Sonnet 4 to reason once and emit the full containment
tree as JSON. This is the directly comparable "can LLM do what Edmonds does"
baseline.

Usage:
    cd backend && uv run python scripts/single_shot_cot_baseline.py --novel xiyouji
    cd backend && uv run python scripts/single_shot_cot_baseline.py --novel honglou

Output: paper/evaluation/v071/baselines/single_shot_cot/<slug>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
OUTPUT_ROOT = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/v071/baselines/single_shot_cot"
)

NOVELS: dict[str, dict] = {
    "xiyouji": {
        "title": "西游记",
        "novel_id": "3b2ef56c-1a55-466a-a7d1-34272446a198",
        "gold_file": "tests/fixtures/golden_standard_journey_to_west.json",
    },
    "honglou": {
        "title": "红楼梦",
        "novel_id": "c384901a-8b71-437a-af35-b5ec1c56c696",
        "gold_file": "tests/fixtures/golden_standard_dream_of_red_chamber.json",
    },
}

MODEL = "claude-sonnet-4-20250514"
MAX_OUTPUT_TOKENS = 16384

SYSTEM_PROMPT = """你是古典中文小说地理层级的分析专家。你将读到一本小说的地点集合和每个地点观察到的父级候选证据,你的任务是推理出最合理的地点包含树。

输出要求:
1. 严格 JSON 格式: {"父子关系": {"子地点名": "父地点名", ...}, "顶级地点": ["根1", "根2", ...]}
2. 同一个地点只能有一个父级,不能出现环
3. 没有明确父级的地点归入"顶级地点"
4. 输出所有输入地点,不得遗漏或新增地点
5. 返回纯 JSON,不要有 markdown 代码块,不要有解释"""


def load_evidence(novel_id: str) -> dict:
    """Gather the evidence that Edmonds pipeline sees: locations + parent votes."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT c.chapter_num, c.title, cf.fact_json FROM chapter_facts cf "
        "JOIN chapters c ON c.id=cf.chapter_id WHERE cf.novel_id=? ORDER BY c.chapter_num",
        (novel_id,),
    ).fetchall()
    conn.close()

    loc_mentions: Counter[str] = Counter()
    parent_votes: dict[str, Counter[str]] = {}
    chapter_summaries: list[tuple[int, str, list[str]]] = []

    for ch_num, title, fact_json_text in rows:
        try:
            fact = json.loads(fact_json_text)
        except Exception:
            continue
        locs = fact.get("locations") or []
        locs_in_ch: list[str] = []
        for loc in locs:
            name = (loc.get("name") or "").strip()
            parent = (loc.get("parent") or "").strip()
            if not name:
                continue
            loc_mentions[name] += 1
            locs_in_ch.append(name)
            if parent and parent.lower() not in ("none", "null", ""):
                parent_votes.setdefault(name, Counter())[parent] += 1
        # Spatial contains relations also contribute evidence
        for sr in fact.get("spatial_relationships") or []:
            if sr.get("relation_type") == "contains":
                src = (sr.get("source") or "").strip()
                tgt = (sr.get("target") or "").strip()
                # contains(src, tgt) means src contains tgt → parent=src, child=tgt
                if src and tgt:
                    parent_votes.setdefault(tgt, Counter())[src] += 1
        chapter_summaries.append((ch_num, title or f"第{ch_num}回", locs_in_ch[:5]))

    return {
        "loc_mentions": loc_mentions,
        "parent_votes": parent_votes,
        "chapter_summaries": chapter_summaries,
    }


def build_prompt(evidence: dict, title: str) -> str:
    loc_mentions: Counter[str] = evidence["loc_mentions"]
    parent_votes: dict[str, Counter[str]] = evidence["parent_votes"]
    chapter_summaries = evidence["chapter_summaries"]

    locations_sorted = [n for n, _ in loc_mentions.most_common()]

    # Compact evidence table: for each location, list top-3 parent candidates with counts
    evidence_lines: list[str] = []
    for name in locations_sorted:
        votes = parent_votes.get(name)
        if votes:
            top = votes.most_common(3)
            evidence_lines.append(
                f"- {name}(出现{loc_mentions[name]}次) → 父级候选: "
                + ", ".join(f"{p}×{c}" for p, c in top)
            )
        else:
            evidence_lines.append(f"- {name}(出现{loc_mentions[name]}次) → 父级候选: (无)")

    # Brief chapter summaries: "第N回 <title>: loc1, loc2, loc3"
    summary_lines = [
        f"第{ch}回 {t}: {', '.join(locs) if locs else '(无地点)'}"
        for ch, t, locs in chapter_summaries
    ]

    return f"""小说《{title}》共 {len(chapter_summaries)} 回,提取出 {len(locations_sorted)} 个地点。

## 章节地点概要
{chr(10).join(summary_lines)}

## 每个地点的父级候选证据
{chr(10).join(evidence_lines)}

## 任务
基于以上证据和你对《{title}》地理的理解,为每个地点选择最合适的父级(或标为顶级)。
输出严格 JSON:
{{
  "父子关系": {{"地点1": "父级1", "地点2": "父级2", ...}},
  "顶级地点": ["根A", "根B", ...]
}}

注意:
1. 覆盖全部 {len(locations_sorted)} 个地点
2. 无环、单根或少根
3. 专名后缀隐含等级(界>国>城>谷>洞>殿),违反此序的父子关系不合理
"""


async def run_cot(slug: str) -> dict:
    meta = NOVELS[slug]
    title = meta["title"]

    out_dir = OUTPUT_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{slug}.json"

    print(f"\n=== {title} ({slug}) — single-shot CoT, model={MODEL} ===")
    print("  Gathering evidence...")
    evidence = load_evidence(meta["novel_id"])
    print(f"  Locations: {len(evidence['loc_mentions'])}")
    print(f"  Chapters with facts: {len(evidence['chapter_summaries'])}")

    prompt = build_prompt(evidence, title)
    prompt_len = len(prompt)
    print(f"  Prompt length: {prompt_len:,} chars (~{prompt_len // 2:,} tokens)")

    import anthropic

    client = anthropic.AsyncAnthropic()
    print("  Calling Claude...")
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    content = resp.content[0].text
    in_tok = resp.usage.input_tokens
    out_tok = resp.usage.output_tokens
    cost = in_tok / 1_000_000 * 3 + out_tok / 1_000_000 * 15
    print(f"  Tokens: input={in_tok:,} output={out_tok:,} Cost≈${cost:.3f}")

    # Parse JSON (LLM may still wrap in markdown despite the prompt)
    text = content.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        # Save raw response so we can debug
        raw_file = out_dir / f"{slug}-raw.txt"
        raw_file.write_text(content)
        print(f"  Raw saved: {raw_file}")
        return {"error": "json_parse_error", "raw_file": str(raw_file)}

    predicted_parents: dict[str, str] = parsed.get("父子关系") or parsed.get("parents") or {}
    stated_roots: list[str] = parsed.get("顶级地点") or parsed.get("roots") or []

    # Structural metrics
    parent_to_kids: dict[str, set[str]] = {}
    for child, parent in predicted_parents.items():
        parent_to_kids.setdefault(parent, set()).add(child)
    max_children = max((len(v) for v in parent_to_kids.values()), default=0)

    # Cycle check via DFS
    def has_cycle() -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            p = predicted_parents.get(node)
            if p and dfs(p):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(dfs(n) for n in predicted_parents)

    cycle = has_cycle()

    # Roots (nodes not appearing as child)
    all_nodes = set(predicted_parents.keys()) | set(predicted_parents.values()) | set(stated_roots)
    roots = all_nodes - set(predicted_parents.keys())

    # Coverage check
    input_locs = set(evidence["loc_mentions"].keys())
    output_locs = set(predicted_parents.keys()) | set(stated_roots)
    missed = input_locs - output_locs
    hallucinated = output_locs - input_locs

    result = {
        "slug": slug,
        "model": MODEL,
        "title": title,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 4),
        "prompt_chars": prompt_len,
        "input_locations": len(input_locs),
        "output_parent_assignments": len(predicted_parents),
        "stated_roots": len(stated_roots),
        "max_children": max_children,
        "computed_roots": len(roots),
        "has_cycle": cycle,
        "missed_locations": sorted(missed)[:30],
        "missed_count": len(missed),
        "hallucinated_locations": sorted(hallucinated)[:30],
        "hallucinated_count": len(hallucinated),
        "parents": predicted_parents,
        "stated_roots_list": stated_roots,
    }

    # Parent precision vs gold (if available)
    gold_file = meta.get("gold_file")
    if gold_file:
        gold_path = Path(__file__).parent.parent / gold_file
        if gold_path.exists():
            try:
                from src.utils.topology_metrics import compute_topology_metrics

                gold_locs = json.loads(gold_path.read_text()).get("locations", [])
                topo = compute_topology_metrics(predicted_parents, gold_locs)
                result["topology"] = topo
            except Exception as e:
                result["topology_error"] = str(e)

    # Compute avg_depth
    def depth(node: str, seen: set[str]) -> int:
        if node in seen:
            return 0
        seen = seen | {node}
        parent = predicted_parents.get(node)
        if not parent:
            return 0
        return 1 + depth(parent, seen)

    depths = [depth(n, set()) for n in predicted_parents]
    result["avg_depth"] = round(sum(depths) / len(depths), 2) if depths else 0.0

    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"  Saved: {out_file}")
    print(
        f"  Summary: locs={len(input_locs)} → assignments={len(predicted_parents)} "
        f"max_ch={max_children} roots={len(roots)} cycle={'YES' if cycle else 'no'} "
        f"missed={len(missed)} halluc={len(hallucinated)}"
    )
    if "topology" in result:
        t = result["topology"]
        print(
            f"  vs gold: parent_P={t.get('parent_precision', 0):.3f} "
            f"recall={t.get('parent_recall', 0):.3f} chain={t.get('chain_accuracy', 0):.3f}"
        )
    return result


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", choices=list(NOVELS.keys()), help="Single novel")
    ap.add_argument("--both", action="store_true", help="Run both xiyouji and honglou")
    args = ap.parse_args()

    if args.both:
        targets = list(NOVELS.keys())
    elif args.novel:
        targets = [args.novel]
    else:
        ap.error("choose --novel <slug> or --both")

    for slug in targets:
        await run_cot(slug)


if __name__ == "__main__":
    asyncio.run(main())
