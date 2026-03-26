"""Evaluation dashboard for EMNLP paper — computes all quality metrics across dimensions.

Usage:
    uv run python -m src.utils.eval_dashboard [novel_id]

Dimensions:
    1. Entity (character) precision/recall
    2. Relationship type accuracy
    3. Location hierarchy (topology metrics)
    4. Alias resolution precision/recall
    5. Processing efficiency (time/tokens/cost)
"""

from __future__ import annotations

import json
import sqlite3
import os
from pathlib import Path
from collections import Counter

from src.utils.topology_metrics import compute_topology_metrics

FIXTURES_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures"
ANNOTATION_DIR = FIXTURES_DIR / "annotation_templates"
DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")


def _load_annotation(filename: str) -> dict | None:
    """Load an annotation file (with human labels filled in)."""
    path = ANNOTATION_DIR / filename
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def eval_characters(novel_name: str) -> dict | None:
    """Evaluate character extraction precision/recall."""
    data = _load_annotation(f"annotate_characters_{novel_name}.json")
    if not data:
        return None

    chars = data.get("characters", [])
    annotated = [c for c in chars if c.get("is_valid_character") is not None]
    if not annotated:
        return {"status": "not_annotated", "total": len(chars)}

    tp = sum(1 for c in annotated if c["is_valid_character"] is True)
    fp = sum(1 for c in annotated if c["is_valid_character"] is False)
    total_annotated = len(annotated)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    # Recall requires knowing total true characters (from annotation)
    # For top-50, recall is approximate
    return {
        "precision": round(precision, 4),
        "true_positives": tp,
        "false_positives": fp,
        "annotated": total_annotated,
        "total_extracted": len(chars),
    }


def eval_relations(novel_name: str) -> dict | None:
    """Evaluate relationship type classification accuracy."""
    data = _load_annotation(f"annotate_relations_{novel_name}.json")
    if not data:
        return None

    rels = data.get("relations", [])
    annotated = [r for r in rels if r.get("correct_type") is not None]
    if not annotated:
        return {"status": "not_annotated", "total": len(rels)}

    # Type accuracy: system_type matches correct_type
    type_correct = sum(
        1 for r in annotated
        if r["system_type"] == r["correct_type"]
    )
    # Category accuracy: system category matches correct_category
    from src.services.relation_utils import normalize_relation_type, classify_relation_category
    cat_correct = 0
    cat_annotated = 0
    for r in annotated:
        if r.get("correct_category"):
            cat_annotated += 1
            sys_cat = classify_relation_category(normalize_relation_type(r["system_type"]))
            if sys_cat == r["correct_category"]:
                cat_correct += 1

    return {
        "type_accuracy": round(type_correct / len(annotated), 4) if annotated else 0.0,
        "category_accuracy": round(cat_correct / cat_annotated, 4) if cat_annotated else 0.0,
        "type_correct": type_correct,
        "category_correct": cat_correct,
        "annotated": len(annotated),
    }


def eval_aliases(novel_name: str) -> dict | None:
    """Evaluate alias resolution quality."""
    data = _load_annotation(f"annotate_aliases_{novel_name}.json")
    if not data:
        return None

    groups = data.get("alias_groups", [])
    annotated = [g for g in groups if g.get("is_correct_grouping") is not None]
    if not annotated:
        return {"status": "not_annotated", "total": len(groups)}

    correct = sum(1 for g in annotated if g["is_correct_grouping"] is True)
    wrong_aliases_total = sum(len(g.get("wrong_aliases", [])) for g in annotated)
    missing_aliases_total = sum(len(g.get("missing_aliases", [])) for g in annotated)

    return {
        "group_accuracy": round(correct / len(annotated), 4) if annotated else 0.0,
        "correct_groups": correct,
        "annotated_groups": len(annotated),
        "wrong_aliases_found": wrong_aliases_total,
        "missing_aliases_found": missing_aliases_total,
    }


def eval_topology(novel_id: str, novel_name: str) -> dict | None:
    """Evaluate location hierarchy using golden standard."""
    # Map novel names to golden standard files
    golden_map = {
        "journey_to_west": "golden_standard_journey_to_west.json",
        "dream_of_red_chamber": "golden_standard_dream_of_red_chamber.json",
    }
    golden_file = golden_map.get(novel_name)
    if not golden_file:
        return None

    golden_path = FIXTURES_DIR / golden_file
    if not golden_path.exists():
        return None

    with open(golden_path, encoding="utf-8") as f:
        golden_data = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    conn.close()

    if not row:
        return None

    ws = json.loads(row[0])
    predicted = ws.get("location_parents", {})

    return compute_topology_metrics(predicted, golden_data["locations"])


def eval_efficiency(novel_id: str) -> dict | None:
    """Collect processing efficiency metrics from DB."""
    conn = sqlite3.connect(DB_PATH)

    # Get chapter-level timing from analysis_tasks
    task_row = conn.execute(
        "SELECT timing_summary FROM analysis_tasks WHERE novel_id=? ORDER BY updated_at DESC LIMIT 1",
        (novel_id,),
    ).fetchone()

    # Get cost data
    try:
        cost_rows = conn.execute(
            "SELECT SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), COUNT(*) "
            "FROM cost_tracking WHERE novel_id=?",
            (novel_id,),
        ).fetchone()
    except Exception:
        cost_rows = None

    # Get chapter count
    ch_count = conn.execute(
        "SELECT COUNT(*) FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ).fetchone()[0]

    conn.close()

    result = {"chapters_analyzed": ch_count}

    if task_row and task_row[0]:
        try:
            timing = json.loads(task_row[0])
            if isinstance(timing, dict):
                total_ms = timing.get("total_ms") or timing.get("elapsed_ms", 0)
                if total_ms:
                    result["total_time_s"] = round(total_ms / 1000, 1)
                    result["avg_chapter_time_s"] = round(total_ms / 1000 / ch_count, 1) if ch_count else 0
        except (json.JSONDecodeError, TypeError):
            pass

    if cost_rows and cost_rows[0]:
        result["total_input_tokens"] = cost_rows[0]
        result["total_output_tokens"] = cost_rows[1]
        result["total_cost_usd"] = round(cost_rows[2], 4) if cost_rows[2] else None
        result["avg_tokens_per_chapter"] = round(
            (cost_rows[0] + cost_rows[1]) / ch_count
        ) if ch_count else 0

    return result


def run_dashboard(novel_id: str, novel_name: str) -> dict:
    """Run full evaluation dashboard for a novel."""
    results = {
        "novel": novel_name,
        "novel_id": novel_id,
        "characters": eval_characters(novel_name),
        "relations": eval_relations(novel_name),
        "aliases": eval_aliases(novel_name),
        "topology": eval_topology(novel_id, novel_name),
        "efficiency": eval_efficiency(novel_id),
    }
    return results


def print_dashboard(results: dict) -> None:
    """Pretty-print dashboard results."""
    print(f"\n{'='*60}")
    print(f"  EVALUATION DASHBOARD: {results['novel']}")
    print(f"{'='*60}")

    for dim_name, dim_data in results.items():
        if dim_name in ("novel", "novel_id"):
            continue
        print(f"\n--- {dim_name.upper()} ---")
        if dim_data is None:
            print("  (no data / no golden standard)")
        elif isinstance(dim_data, dict):
            for k, v in dim_data.items():
                if k.startswith("_"):
                    continue
                print(f"  {k}: {v}")
        else:
            print(f"  {dim_data}")


if __name__ == "__main__":
    import sys

    novels = {
        "journey_to_west": "4e5904eb-37b9-4da6-b7ac-13e63dd8a200",
        "dream_of_red_chamber": "3e850a88-e624-4176-8edf-bb24c2dba083",
        "water_margin": "4ac43c73-f67b-427c-8d6d-e766a1423977",
    }

    if len(sys.argv) > 1:
        target = sys.argv[1]
        if target in novels:
            r = run_dashboard(novels[target], target)
            print_dashboard(r)
        else:
            print(f"Unknown novel. Available: {list(novels.keys())}")
    else:
        for name, nid in novels.items():
            r = run_dashboard(nid, name)
            print_dashboard(r)
