"""Evaluate AI Reader extraction on 《星尘劫》 vs the Phase 1 bible gold.

Compares AI Reader's pipeline output against the synthetic novel's ground
truth (derived from the Phase 1 DeepSeek world bible). Produces a 5-dimension
score + Overall in the same format as `paper/evaluation/v071/*-benchmark.json`
so the result is directly quotable in main.tex.

Reads:
    paper/evaluation/contamination-free/novel/gold_standard.json
    paper/evaluation/contamination-free/novel/gold_characters.json
    paper/evaluation/contamination-free/novel/gold_relations.json
    ~/.ai-reader-v2/data.db  (world_structures + chapter_facts)

Writes:
    paper/evaluation/contamination-free/novel/benchmark.json
    paper/evaluation/contamination-free/novel/benchmark.md  (human-readable)

Usage:
    cd backend && uv run python scripts/eval_contamination_free.py
    cd backend && uv run python scripts/eval_contamination_free.py --title 星尘劫
    cd backend && uv run python scripts/eval_contamination_free.py --novel-id <uuid>
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
BASE = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/contamination-free/novel"
)


# =============================================================================
# Load data
# =============================================================================

def lookup_novel_id(title: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM novels WHERE title=?", (title,)).fetchone()
    conn.close()
    return row[0] if row else None


def load_pipeline_output(novel_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    if not row:
        raise SystemExit(f"No world_structure for novel {novel_id}; run analysis first")
    ws = json.loads(row[0])

    # Aggregate characters from chapter_facts
    char_counter: Counter[str] = Counter()
    alias_map: dict[str, str] = {}  # alias → canonical (simplified)
    rel_set: set[tuple[str, str, str]] = set()

    rows = conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
        (novel_id,),
    ).fetchall()
    conn.close()

    for (fact_json_text,) in rows:
        try:
            f = json.loads(fact_json_text)
        except Exception:
            continue
        for ch in f.get("characters") or []:
            name = (ch.get("name") or "").strip()
            if name:
                char_counter[name] += 1
            for a in ch.get("new_aliases") or []:
                if isinstance(a, str) and a.strip():
                    alias_map[a.strip()] = name
        for rel in f.get("relationships") or []:
            a = (rel.get("person_a") or "").strip()
            b = (rel.get("person_b") or "").strip()
            t = (rel.get("relation_type") or "").strip()
            if a and b:
                rel_set.add((a, b, t))

    return {
        "location_parents": ws.get("location_parents") or {},
        "location_tiers": ws.get("location_tiers") or {},
        "characters_counter": char_counter,
        "alias_map": alias_map,
        "relations": rel_set,
        "chapter_facts_loaded": len(rows),
    }


# =============================================================================
# Metrics
# =============================================================================

def evaluate_locations(gold_locs: list[dict], pipe: dict) -> dict:
    """Compute location-level metrics: entity, tier, parent, structural."""
    gold_names = {L["name"] for L in gold_locs}
    gold_parents = {L["name"]: L.get("correct_parent") for L in gold_locs}
    gold_tiers = {L["name"]: L.get("tier") for L in gold_locs}

    pipe_parents = pipe["location_parents"]
    pipe_tiers = pipe["location_tiers"]
    pipe_names = set(pipe_parents.keys()) | set(pipe_parents.values()) | set(pipe_tiers.keys())

    # --- Entity Precision & Recall (locations present) ---
    correctly_extracted = gold_names & pipe_names
    entity_recall = len(correctly_extracted) / len(gold_names) if gold_names else 0.0
    entity_precision = len(correctly_extracted) / len(pipe_names) if pipe_names else 0.0

    # --- Tier Accuracy ---
    tier_total = 0
    tier_correct = 0
    for name in gold_names & set(pipe_tiers.keys()):
        tier_total += 1
        if pipe_tiers[name] == gold_tiers.get(name):
            tier_correct += 1
    tier_acc = tier_correct / tier_total if tier_total else 0.0

    # --- Parent Precision ---
    # For each gold child that pipeline extracted, does pipeline's parent match gold?
    parent_total = 0
    parent_correct = 0
    parent_mismatches: list[tuple[str, str, str]] = []  # (name, gold_parent, pipe_parent)
    for name, gold_p in gold_parents.items():
        if gold_p is None:  # gold root, skip
            continue
        if name in pipe_parents:
            parent_total += 1
            pipe_p = pipe_parents[name]
            if pipe_p == gold_p:
                parent_correct += 1
            else:
                parent_mismatches.append((name, gold_p, pipe_p))
    parent_precision = parent_correct / parent_total if parent_total else 0.0

    # --- Structural Health ---
    # Subgraph restricted to pipeline output
    def has_cycle(parents: dict) -> tuple[bool, list]:
        visiting: set[str] = set()
        visited: set[str] = set()
        cycles: list = []
        for start in parents:
            if start in visited:
                continue
            path: list[str] = []
            cur = start
            while cur:
                if cur in path:
                    cycles.append(path[path.index(cur):] + [cur])
                    break
                if cur in visited:
                    break
                path.append(cur)
                cur = parents.get(cur)
            visited.update(path)
        return bool(cycles), cycles

    cycle, cycle_list = has_cycle(pipe_parents)
    all_nodes = set(pipe_parents.keys()) | set(pipe_parents.values())
    roots = all_nodes - set(pipe_parents.keys())
    max_children = max(Counter(pipe_parents.values()).values(), default=0)

    struct_score = 1.0
    if cycle:
        struct_score -= 0.5
    if len(roots) > 1:
        # Gracefully degrade: 1 root = perfect, 2 roots = 0.8, 5+ = 0
        struct_score -= min(0.5, 0.1 * (len(roots) - 1))
    struct_score = max(0.0, struct_score)

    return {
        "gold_location_count": len(gold_names),
        "pipe_location_count": len(pipe_names),
        "correctly_extracted": len(correctly_extracted),
        "missing_from_pipe": sorted(gold_names - pipe_names),
        "hallucinated_by_pipe": len(pipe_names - gold_names),
        "entity_precision": entity_precision,
        "entity_recall": entity_recall,
        "tier_accuracy": tier_acc,
        "tier_total": tier_total,
        "tier_correct": tier_correct,
        "parent_precision": parent_precision,
        "parent_total": parent_total,
        "parent_correct": parent_correct,
        "parent_mismatches": parent_mismatches[:10],
        "structural_health": struct_score,
        "has_cycle": cycle,
        "cycle_example": cycle_list[:1] if cycle_list else [],
        "root_count": len(roots),
        "roots": sorted(roots),
        "max_children": max_children,
    }


def evaluate_characters(gold_chars: list[dict], pipe: dict) -> dict:
    """Character extraction coverage: do all gold canonical names appear?"""
    gold_canonicals = [c["canonical_name"] for c in gold_chars if c.get("canonical_name")]
    pipe_char_names = set(pipe["characters_counter"].keys())

    # Direct match: canonical in extracted
    direct_hits = sum(1 for c in gold_canonicals if c in pipe_char_names)

    # Alias match: canonical OR any alias appears in extracted
    alias_hits = 0
    for c in gold_chars:
        variants = {c["canonical_name"]} | set(c.get("aliases") or [])
        if variants & pipe_char_names:
            alias_hits += 1

    return {
        "gold_character_count": len(gold_canonicals),
        "pipe_unique_characters": len(pipe_char_names),
        "canonical_direct_hits": direct_hits,
        "alias_hits": alias_hits,
        "character_recall": alias_hits / len(gold_canonicals) if gold_canonicals else 0.0,
        "missing_canonicals": [c for c in gold_canonicals if c not in pipe_char_names
                                and not (set(next((ch.get("aliases") or [] for ch in gold_chars
                                                   if ch.get("canonical_name") == c), []))
                                         & pipe_char_names)],
    }


def evaluate_relations(gold_rels: list[dict], pipe: dict) -> dict:
    """Relation extraction coverage by pair only (type/category fuzzy)."""
    pipe_pairs = {(a, b) for (a, b, _) in pipe["relations"]} | {(b, a) for (a, b, _) in pipe["relations"]}
    gold_pairs = [(r["person_a"], r["person_b"]) for r in gold_rels]
    hits = sum(1 for p in gold_pairs if p in pipe_pairs or (p[1], p[0]) in pipe_pairs)
    return {
        "gold_relation_count": len(gold_pairs),
        "pipe_relation_count": len({(a, b) for (a, b, _) in pipe["relations"]}),
        "pair_hits": hits,
        "relation_recall": hits / len(gold_pairs) if gold_pairs else 0.0,
    }


# =============================================================================
# Report
# =============================================================================

def render_report(novel_title: str, novel_id: str, loc: dict, char: dict, rel: dict,
                  pipe_meta: dict) -> dict:
    """Build the final benchmark JSON + markdown."""

    # Overall = weighted average, matching paper convention:
    # Entity P (0.2) + Name Acc (proxy: alias hit rate, 0.1) + Tier (0.2)
    # + Parent P (0.3) + Struct (0.2) = 1.0
    overall = (
        0.2 * loc["entity_precision"]
        + 0.1 * char["character_recall"]
        + 0.2 * loc["tier_accuracy"]
        + 0.3 * loc["parent_precision"]
        + 0.2 * loc["structural_health"]
    )

    benchmark = {
        "novel": novel_title,
        "novel_id": novel_id,
        "contamination_status": "synthetic (DeepSeek V3 generated, not in LLM pretraining)",
        "chapter_facts_loaded": pipe_meta["chapter_facts_loaded"],
        "scores": {
            "overall": round(overall, 4),
            "entity_precision": round(loc["entity_precision"], 4),
            "entity_recall": round(loc["entity_recall"], 4),
            "tier_accuracy": round(loc["tier_accuracy"], 4),
            "parent_precision": round(loc["parent_precision"], 4),
            "structural_health": round(loc["structural_health"], 4),
            "character_recall": round(char["character_recall"], 4),
            "relation_pair_recall": round(rel["relation_recall"], 4),
        },
        "counts": {
            "gold_locations": loc["gold_location_count"],
            "pipe_locations": loc["pipe_location_count"],
            "gold_characters": char["gold_character_count"],
            "pipe_characters": char["pipe_unique_characters"],
            "gold_relations": rel["gold_relation_count"],
            "pipe_relations": rel["pipe_relation_count"],
        },
        "structural": {
            "has_cycle": loc["has_cycle"],
            "root_count": loc["root_count"],
            "roots": loc["roots"],
            "max_children": loc["max_children"],
        },
        "errors": {
            "missing_locations": loc["missing_from_pipe"],
            "missing_canonicals": char["missing_canonicals"],
            "parent_mismatches_sample": loc["parent_mismatches"],
        },
    }

    # Markdown
    lines: list[str] = []
    lines.append(f"# 《{novel_title}》 Contamination-Free Evaluation")
    lines.append("")
    lines.append(f"- **Novel ID**: `{novel_id}`")
    lines.append(f"- **Source**: DeepSeek V3 synthetic (not in Claude/GPT pretraining)")
    lines.append(f"- **Chapters analyzed**: {pipe_meta['chapter_facts_loaded']}")
    lines.append(f"- **Extraction LLM**: (check Settings page — typically Claude Sonnet 4.6 or DeepSeek V3)")
    lines.append("")
    lines.append("## Paper Table 2 Row")
    lines.append("")
    lines.append("| Novel | Overall | Entity P | Name Acc | Tier Acc | Parent P | Struct. | Errors |")
    lines.append("|-------|---------|----------|----------|----------|----------|---------|--------|")
    s = benchmark["scores"]
    n_gold = loc["gold_location_count"]
    err = int((1 - s["overall"]) * n_gold)
    lines.append(
        f"| 《{novel_title}》(synthetic) | **{s['overall']:.4f}** | {s['entity_precision']:.3f} | "
        f"{s['character_recall']:.3f} | {s['tier_accuracy']:.3f} | {s['parent_precision']:.3f} | "
        f"{s['structural_health']:.3f} | {err}/{n_gold} |"
    )
    lines.append("")

    lines.append("## Structural Properties")
    lines.append("")
    st = benchmark["structural"]
    lines.append(f"- cycle: **{st['has_cycle']}**")
    lines.append(f"- roots: **{st['root_count']}** {st['roots']}")
    lines.append(f"- max_children: **{st['max_children']}**")
    lines.append("")

    lines.append("## Coverage")
    lines.append("")
    c = benchmark["counts"]
    lines.append(f"- Locations: pipe produced {c['pipe_locations']} (gold: {c['gold_locations']}),"
                 f" recall={s['entity_recall']:.1%}")
    lines.append(f"- Characters: pipe {c['pipe_characters']} (gold: {c['gold_characters']}),"
                 f" alias-recall={s['character_recall']:.1%}")
    lines.append(f"- Relations: pipe {c['pipe_relations']} (gold: {c['gold_relations']}),"
                 f" pair-recall={s['relation_pair_recall']:.1%}")
    lines.append("")

    if loc["missing_from_pipe"]:
        lines.append("## Missing Locations (not extracted by pipeline)")
        for m in loc["missing_from_pipe"][:20]:
            lines.append(f"- {m}")
        if len(loc["missing_from_pipe"]) > 20:
            lines.append(f"- ... (+{len(loc['missing_from_pipe']) - 20} more)")
        lines.append("")

    if char["missing_canonicals"]:
        lines.append("## Missing Characters (neither canonical nor any alias extracted)")
        for m in char["missing_canonicals"]:
            lines.append(f"- {m}")
        lines.append("")

    if loc["parent_mismatches"]:
        lines.append("## Parent Mismatches (sample)")
        lines.append("")
        lines.append("| Child | Gold parent | Pipeline parent |")
        lines.append("|-------|-------------|-----------------|")
        for name, gold_p, pipe_p in loc["parent_mismatches"]:
            lines.append(f"| {name} | {gold_p} | {pipe_p} |")
        lines.append("")

    return benchmark, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="星尘劫", help="Novel title (default: 星尘劫)")
    ap.add_argument("--novel-id", default=None, help="Override novel_id directly")
    args = ap.parse_args()

    novel_id = args.novel_id or lookup_novel_id(args.title)
    if not novel_id:
        sys.exit(f"Novel '{args.title}' not found in DB. Upload + analyze first.")
    print(f"[eval] novel_id = {novel_id}")

    # Load golds
    gold_locs = json.loads((BASE / "gold_standard.json").read_text()).get("locations") or []
    gold_chars = json.loads((BASE / "gold_characters.json").read_text()).get("characters") or []
    gold_rels = json.loads((BASE / "gold_relations.json").read_text()).get("relations") or []

    # Load pipeline output
    pipe = load_pipeline_output(novel_id)
    print(f"[eval] pipeline: {len(pipe['location_parents'])} parent mappings,"
          f" {len(pipe['characters_counter'])} unique characters,"
          f" {pipe['chapter_facts_loaded']} chapter_facts rows")

    # Metrics
    loc_metrics = evaluate_locations(gold_locs, pipe)
    char_metrics = evaluate_characters(gold_chars, pipe)
    rel_metrics = evaluate_relations(gold_rels, pipe)

    # Report
    benchmark, md = render_report(args.title, novel_id, loc_metrics, char_metrics,
                                   rel_metrics, {"chapter_facts_loaded": pipe["chapter_facts_loaded"]})

    out_json = BASE / "benchmark.json"
    out_md = BASE / "benchmark.md"
    out_json.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2))
    out_md.write_text(md)
    print()
    print(md)
    print()
    print(f"[eval] saved: {out_json}")
    print(f"[eval] saved: {out_md}")


if __name__ == "__main__":
    main()
