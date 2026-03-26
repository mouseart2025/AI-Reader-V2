"""Zero-shot LLM baseline for EMNLP paper.

Sends raw chapter text to Claude with a simple extraction prompt (no CoT,
no context injection, no FactValidator) and compares with AI Reader's
full pipeline output.

Usage:
    cd backend && uv run python scripts/zero_shot_baseline.py
"""

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"

# Novel: Journey to the West (v2)
NOVEL_ID = "4e5904eb-37b9-4da6-b7ac-13e63dd8a200"

# Simple extraction prompt — no CoT, no context, no suffix rules
ZERO_SHOT_PROMPT = """请从以下小说章节文本中提取结构化信息，以 JSON 格式输出。

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


async def run_zero_shot(chapter_texts: list[tuple[int, str]]):
    """Run zero-shot extraction on multiple chapters."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    model_name = "claude-sonnet-4-20250514"

    print(f"  Using: {model_name}")
    results = []

    for ch_num, text in chapter_texts:
        print(f"  Chapter {ch_num}: extracting...", end=" ", flush=True)
        try:
            resp = await client.messages.create(
                model=model_name,
                max_tokens=8192,
                messages=[{
                    "role": "user",
                    "content": ZERO_SHOT_PROMPT + text[:12000],
                }],
            )
            response = resp.content[0].text
            content = response

            # Parse JSON from response
            # Try to find JSON block
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content)
            results.append((ch_num, data))
            chars = len(data.get("characters", []))
            locs = len(data.get("locations", []))
            rels = len(data.get("relationships", []))
            spats = len(data.get("spatial_relationships", []))
            print(f"chars={chars} locs={locs} rels={rels} spatial={spats}")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append((ch_num, None))

    return results


def compare_with_pipeline(zero_shot_results: list, pipeline_facts: list):
    """Compare zero-shot vs pipeline extraction quality."""

    # Load golden standards
    with open(FIXTURES / "golden_standard_journey_to_west.json") as f:
        golden_locs = json.load(f)["locations"]
    golden_parents = {l["name"]: l["correct_parent"] for l in golden_locs if l.get("correct_parent")}

    # Load character annotations
    anno_path = FIXTURES / "annotation_templates" / "annotate_characters_journey_to_west.json"
    with open(anno_path) as f:
        char_annos = json.load(f)["characters"]
    valid_chars = {c["name"] for c in char_annos if c.get("is_valid_character") is True}
    invalid_chars = {c["name"] for c in char_annos if c.get("is_valid_character") is False}
    anno_chars = valid_chars | invalid_chars

    print("\n" + "=" * 60)
    print("  ZERO-SHOT vs PIPELINE COMPARISON")
    print("=" * 60)

    # ── Character comparison ──
    # Aggregate across all chapters
    zs_chars = Counter()
    pipeline_chars = Counter()
    for ch_num, zs_data in zero_shot_results:
        if zs_data:
            for ch in zs_data.get("characters", []):
                zs_chars[ch.get("name", "")] += 1

    for fact in pipeline_facts:
        for ch in fact.get("characters", []):
            pipeline_chars[ch.get("name", "")] += 1

    # Check against annotations
    zs_top50 = [name for name, _ in zs_chars.most_common(50)]
    pipe_top50 = [name for name, _ in pipeline_chars.most_common(50)]

    zs_valid = sum(1 for n in zs_top50 if n in valid_chars)
    zs_invalid = sum(1 for n in zs_top50 if n in invalid_chars)
    pipe_valid = sum(1 for n in pipe_top50 if n in valid_chars)
    pipe_invalid = sum(1 for n in pipe_top50 if n in invalid_chars)

    print("\n--- CHARACTERS (top-50 overlap with annotations) ---")
    print(f"  Zero-shot:  {zs_valid} valid, {zs_invalid} invalid in top-50")
    print(f"  Pipeline:   {pipe_valid} valid, {pipe_invalid} invalid in top-50")
    print(f"  Zero-shot unique chars: {len(zs_chars)}")
    print(f"  Pipeline unique chars:  {len(pipeline_chars)}")

    # ── Location hierarchy comparison ──
    # Build parent maps from zero-shot
    zs_parents = {}
    for ch_num, zs_data in zero_shot_results:
        if zs_data:
            for loc in zs_data.get("locations", []):
                name = loc.get("name", "")
                parent = loc.get("parent")
                if name and parent and parent != "None" and parent != "null":
                    zs_parents[name] = parent

    # Pipeline parents from DB
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT structure_json FROM world_structures WHERE novel_id=?", (NOVEL_ID,)).fetchone()
    conn.close()
    pipe_parents = json.loads(row[0]).get("location_parents", {}) if row else {}

    # Evaluate against golden standard
    from src.utils.topology_metrics import compute_topology_metrics
    zs_metrics = compute_topology_metrics(zs_parents, golden_locs)
    pipe_metrics = compute_topology_metrics(pipe_parents, golden_locs)

    print("\n--- LOCATION HIERARCHY ---")
    print(f"  {'Metric':<25} {'Zero-shot':>10} {'Pipeline':>10} {'Delta':>10}")
    print(f"  {'-'*55}")
    for key in ["parent_precision", "parent_recall", "chain_accuracy"]:
        zs_val = zs_metrics[key]
        pipe_val = pipe_metrics[key]
        delta = (pipe_val - zs_val) * 100
        print(f"  {key:<25} {zs_val*100:>9.1f}% {pipe_val*100:>9.1f}% {delta:>+9.1f}pp")

    # ── Contains direction ──
    zs_contains_total = 0
    zs_contains_correct = 0
    for ch_num, zs_data in zero_shot_results:
        if zs_data:
            for sr in zs_data.get("spatial_relationships", []):
                if sr.get("relation_type") == "contains":
                    zs_contains_total += 1
                    source = sr.get("source", "")
                    target = sr.get("target", "")
                    from src.extraction.fact_validator import _get_contains_rank
                    src_rank = _get_contains_rank(source)
                    tgt_rank = _get_contains_rank(target)
                    if src_rank is not None and tgt_rank is not None:
                        if src_rank <= tgt_rank:  # Correct direction
                            zs_contains_correct += 1

    print(f"\n--- CONTAINS DIRECTION (zero-shot) ---")
    if zs_contains_total:
        print(f"  Total contains: {zs_contains_total}")
        print(f"  Correct direction: {zs_contains_correct} ({zs_contains_correct/zs_contains_total*100:.1f}%)")
        print(f"  Inverted: {zs_contains_total - zs_contains_correct} ({(zs_contains_total-zs_contains_correct)/zs_contains_total*100:.1f}%)")
    else:
        print("  No contains relationships extracted")

    # ── Summary table for paper ──
    print("\n" + "=" * 60)
    print("  PAPER TABLE: Zero-shot vs Full Pipeline")
    print("=" * 60)
    print(f"  {'Metric':<30} {'Claude Zero-shot':>16} {'AI Reader':>12} {'Δ':>8}")
    print(f"  {'-'*66}")
    print(f"  {'Location Hierarchy P':<30} {zs_metrics['parent_precision']*100:>15.1f}% {pipe_metrics['parent_precision']*100:>11.1f}% {(pipe_metrics['parent_precision']-zs_metrics['parent_precision'])*100:>+7.1f}pp")
    print(f"  {'Chain Accuracy':<30} {zs_metrics['chain_accuracy']*100:>15.1f}% {pipe_metrics['chain_accuracy']*100:>11.1f}% {(pipe_metrics['chain_accuracy']-zs_metrics['chain_accuracy'])*100:>+7.1f}pp")
    if zs_contains_total:
        zs_dir_acc = zs_contains_correct / zs_contains_total * 100
        print(f"  {'Contains Direction Acc':<30} {zs_dir_acc:>15.1f}% {'—':>12} {'—':>8}")

    return {
        "zero_shot_metrics": zs_metrics,
        "pipeline_metrics": pipe_metrics,
        "zero_shot_chars": len(zs_chars),
        "pipeline_chars": len(pipeline_chars),
        "zero_shot_parents": len(zs_parents),
        "zero_shot_contains_total": zs_contains_total,
        "zero_shot_contains_correct": zs_contains_correct,
    }


async def main():
    # Load first 5 chapters from DB
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT chapter_num, content FROM chapters WHERE novel_id=? ORDER BY chapter_num LIMIT 5",
        (NOVEL_ID,),
    ).fetchall()

    # Also load pipeline facts for comparison
    fact_rows = conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id LIMIT 5",
        (NOVEL_ID,),
    ).fetchall()
    conn.close()

    chapter_texts = [(r[0], r[1]) for r in rows if r[1]]
    pipeline_facts = [json.loads(r[0]) for r in fact_rows]

    print(f"Loaded {len(chapter_texts)} chapters for zero-shot extraction")
    print(f"Zero-shot baseline experiment\n")

    # Run zero-shot extraction
    print("=== Zero-shot Extraction ===")
    results = await run_zero_shot(chapter_texts)

    # Compare
    comparison = compare_with_pipeline(results, pipeline_facts)

    # Save results
    output_path = Path("/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/zero_shot_results.json")
    with open(output_path, "w") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
