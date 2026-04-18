"""Zero-shot LLM baseline — per-chapter extraction with a simple prompt.

Tests: what does raw LLM extraction (no CoT guide, no context injection, no
FactValidator) produce when aggregated across a whole novel? The result is
the lower bound for the "what does LLM alone buy us" question.

Usage:
    cd backend && uv run python scripts/zero_shot_baseline.py --novel xiyouji
    cd backend && uv run python scripts/zero_shot_baseline.py --novel honglou --max-chapters 20
    cd backend && uv run python scripts/zero_shot_baseline.py --all  # all 5 novels

Each chapter's extraction is cached; re-runs skip already-done chapters.
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

# Load backend/.env (same convention as src.infra.config) so ANTHROPIC_API_KEY
# is sourced from the repo's .env rather than whatever leaks in from the shell.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
OUTPUT_ROOT = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/v071/baselines/zero_shot"
)

# novel_id values that the v071 benchmarks target (see paper/evaluation/v071/*.json)
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
    "shuihu": {
        "title": "水浒传",
        "novel_id": "4ac43c73-f67b-427c-8d6d-e766a1423977",
        "gold_file": "tests/fixtures/golden_standard_water_margin.json",
    },
    "sanguo": {
        "title": "三国演义",
        "novel_id": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
        "gold_file": None,
    },
    "fengshen": {
        "title": "封神演义",
        "novel_id": "53013970-effd-4f50-aef7-728ca13de69a",
        "gold_file": None,
    },
}

MODEL = "claude-sonnet-4-20250514"
MAX_CHAPTER_CHARS = 12000

PROMPT = """请从以下小说章节文本中提取结构化信息，以 JSON 格式输出。

提取以下内容：
1. characters: 所有出现的人物角色，包含 name 和 description
2. locations: 所有出现的地点，包含 name, type, parent（如果能判断包含关系）
3. relationships: 人物之间的关系，包含 person_a, person_b, relation_type
4. spatial_relationships: 地点之间的空间关系，包含 source, target, relation_type (如 contains/direction/adjacent), value

请严格以 JSON 格式输出：
{
  "characters": [{"name": "...", "description": "..."}],
  "locations": [{"name": "...", "type": "...", "parent": "..."}],
  "relationships": [{"person_a": "...", "person_b": "...", "relation_type": "..."}],
  "spatial_relationships": [{"source": "...", "target": "...", "relation_type": "...", "value": "..."}]
}

## 章节文本

"""


def parse_json_from_llm(content: str) -> dict:
    """Strip markdown fences and parse JSON from LLM response."""
    text = content.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    return json.loads(text)


async def extract_chapter(client, ch_num: int, text: str) -> dict:
    resp = await client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": PROMPT + text[:MAX_CHAPTER_CHARS]}],
    )
    content = resp.content[0].text
    data = parse_json_from_llm(content)
    data["_chapter"] = ch_num
    data["_input_tokens"] = resp.usage.input_tokens
    data["_output_tokens"] = resp.usage.output_tokens
    return data


async def run_for_novel(slug: str, max_chapters: int | None = None, concurrency: int = 2):
    meta = NOVELS[slug]
    novel_id = meta["novel_id"]
    title = meta["title"]

    out_dir = OUTPUT_ROOT / slug / "chapters"
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chapter_num, content FROM chapters WHERE novel_id=? AND content IS NOT NULL ORDER BY chapter_num",
        (novel_id,),
    ).fetchall()
    conn.close()

    if max_chapters:
        rows = rows[:max_chapters]

    print(f"\n=== {title} ({slug}) — {len(rows)} chapters, model={MODEL} ===")

    import anthropic

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(concurrency)
    total_in = 0
    total_out = 0
    done = 0
    errors: list[int] = []

    async def worker(ch_num: int, text: str):
        nonlocal total_in, total_out, done
        cache = out_dir / f"ch{ch_num:04d}.json"
        if cache.exists():
            data = json.loads(cache.read_text())
            total_in += data.get("_input_tokens", 0)
            total_out += data.get("_output_tokens", 0)
            done += 1
            return
        async with sem:
            try:
                data = await extract_chapter(client, ch_num, text)
                cache.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                total_in += data["_input_tokens"]
                total_out += data["_output_tokens"]
                done += 1
                if done % 10 == 0:
                    print(f"  [{done}/{len(rows)}] tokens so far: in={total_in:,} out={total_out:,}")
            except Exception as e:
                print(f"  Ch.{ch_num} ERROR: {e}")
                errors.append(ch_num)

    await asyncio.gather(*(worker(ch, txt) for ch, txt in rows))

    # Sonnet 4 pricing: $3/MTok input, $15/MTok output
    cost = total_in / 1_000_000 * 3 + total_out / 1_000_000 * 15
    print(f"  Done: {done}/{len(rows)}. Errors: {len(errors)}. Input={total_in:,} Output={total_out:,} Cost≈${cost:.2f}")
    if errors:
        print(f"  Failed chapters: {errors[:20]}{'…' if len(errors) > 20 else ''}")

    return aggregate_novel(slug)


def aggregate_novel(slug: str) -> dict:
    """Aggregate cached per-chapter extractions and compute summary."""
    out_dir = OUTPUT_ROOT / slug / "chapters"
    files = sorted(out_dir.glob("ch*.json"))
    if not files:
        return {}

    all_chars: Counter[str] = Counter()
    all_locs: Counter[str] = Counter()
    parents: dict[str, Counter[str]] = {}
    contains_total = 0
    contains_rank_ok = 0
    contains_rank_bad = 0
    chapters_loaded = 0

    try:
        from src.extraction.fact_validator import _get_contains_rank
    except Exception:
        _get_contains_rank = lambda _n: None  # type: ignore

    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        chapters_loaded += 1
        for ch in data.get("characters") or []:
            name = (ch.get("name") or "").strip()
            if name:
                all_chars[name] += 1
        for loc in data.get("locations") or []:
            name = (loc.get("name") or "").strip()
            parent = (loc.get("parent") or "").strip()
            if name:
                all_locs[name] += 1
                if parent and parent.lower() not in ("none", "null", ""):
                    parents.setdefault(name, Counter())[parent] += 1
        for sr in data.get("spatial_relationships") or []:
            if sr.get("relation_type") != "contains":
                continue
            contains_total += 1
            src = sr.get("source") or ""
            tgt = sr.get("target") or ""
            if not src or not tgt:
                continue
            src_rank = _get_contains_rank(src)
            tgt_rank = _get_contains_rank(tgt)
            if src_rank is not None and tgt_rank is not None:
                if src_rank <= tgt_rank:
                    contains_rank_ok += 1
                else:
                    contains_rank_bad += 1

    # Majority-vote parent per child (mimics greedy voting aggregation)
    best_parent: dict[str, str] = {}
    for child, cnt in parents.items():
        best_parent[child] = cnt.most_common(1)[0][0]

    # Structural metrics
    parent_to_kids: dict[str, set[str]] = {}
    for child, parent in best_parent.items():
        parent_to_kids.setdefault(parent, set()).add(child)
    max_children = max((len(v) for v in parent_to_kids.values()), default=0)
    all_nodes = set(best_parent.keys()) | set(best_parent.values())
    roots = all_nodes - set(best_parent.keys())

    summary = {
        "slug": slug,
        "chapters_loaded": chapters_loaded,
        "unique_characters": len(all_chars),
        "unique_locations": len(all_locs),
        "top30_characters": all_chars.most_common(30),
        "top30_locations": all_locs.most_common(30),
        "parent_assignments": len(best_parent),
        "parents_map": best_parent,
        "max_children": max_children,
        "root_count": len(roots),
        "contains_total": contains_total,
        "contains_rank_ok": contains_rank_ok,
        "contains_rank_bad": contains_rank_bad,
    }

    # Parent precision against gold (if available)
    meta = NOVELS[slug]
    if meta.get("gold_file"):
        gold_path = Path(__file__).parent.parent / meta["gold_file"]
        if gold_path.exists():
            try:
                from src.utils.topology_metrics import compute_topology_metrics

                gold_locs = json.loads(gold_path.read_text()).get("locations", [])
                topo = compute_topology_metrics(best_parent, gold_locs)
                summary["topology"] = topo
            except Exception as e:
                summary["topology_error"] = str(e)

    out_file = OUTPUT_ROOT / slug / "aggregate.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(f"  Aggregate saved: {out_file}")
    return summary


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", choices=list(NOVELS.keys()), help="Run for a single novel")
    ap.add_argument("--all", action="store_true", help="Run for all 5 novels")
    ap.add_argument("--max-chapters", type=int, default=None, help="Cap chapters (debug)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--aggregate-only", action="store_true", help="Skip LLM calls; re-aggregate cached files")
    args = ap.parse_args()

    if args.all:
        targets = list(NOVELS.keys())
    elif args.novel:
        targets = [args.novel]
    else:
        ap.error("choose --novel <slug> or --all")

    for slug in targets:
        if args.aggregate_only:
            aggregate_novel(slug)
        else:
            await run_for_novel(slug, max_chapters=args.max_chapters, concurrency=args.concurrency)


if __name__ == "__main__":
    asyncio.run(main())
