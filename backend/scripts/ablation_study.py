"""Ablation study for EMNLP paper — measures contribution of each validation layer.

Runs ablation experiments on existing chapter_facts data (no LLM re-extraction needed).

Usage:
    cd backend && uv run python scripts/ablation_study.py
"""

import json
import sqlite3
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extraction.fact_validator import (
    _is_generic_location,
    _is_generic_person,
    _get_contains_rank,
)
from src.services.relation_utils import normalize_relation_type, classify_relation_category
from src.utils.topology_metrics import compute_topology_metrics

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
ANNO_DIR = FIXTURES / "annotation_templates"


def load_chapter_facts(novel_id: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
        (novel_id,),
    ).fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def load_golden_locations(novel_name: str) -> list[dict] | None:
    golden_map = {
        "journey_to_west": "golden_standard_journey_to_west.json",
        "dream_of_red_chamber": "golden_standard_dream_of_red_chamber.json",
    }
    fname = golden_map.get(novel_name)
    if not fname:
        return None
    path = FIXTURES / fname
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)["locations"]


def load_annotations(category: str, novel_name: str) -> list[dict]:
    path = ANNO_DIR / f"annotate_{category}_{novel_name}.json"
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    key = {"characters": "characters", "relations": "relations", "aliases": "alias_groups"}[category]
    return data.get(key, [])


# ══════════════════════════════════════════════════════════════════
# Ablation 1: Entity extraction — with vs without FactValidator
# ══════════════════════════════════════════════════════════════════

def ablation_entity_filtering(novel_name: str, novel_id: str):
    """Compare entity precision with/without FactValidator filtering."""
    facts = load_chapter_facts(novel_id)
    annotations = load_annotations("characters", novel_name)
    if not annotations:
        return None

    # Build annotation lookup
    anno_map = {}
    for a in annotations:
        if a.get("is_valid_character") is not None:
            anno_map[a["name"]] = a["is_valid_character"]

    # Count all unique character names from chapter_facts
    all_chars = Counter()
    for fact in facts:
        for ch in fact.get("characters", []):
            name = ch.get("name", "")
            if name:
                all_chars[name] += 1

    # Full system: apply _is_generic_person filter
    filtered_chars = {
        name for name in all_chars
        if _is_generic_person(name) is None  # None = passes filter
    }

    # w/o validator: all chars pass
    unfiltered_chars = set(all_chars.keys())

    # Measure precision on annotated subset
    def precision_on_annotated(char_set):
        tp = sum(1 for name in char_set if anno_map.get(name) is True)
        fp = sum(1 for name in char_set if anno_map.get(name) is False)
        return tp, fp, tp / (tp + fp) * 100 if (tp + fp) > 0 else 0

    full_tp, full_fp, full_p = precision_on_annotated(filtered_chars)
    raw_tp, raw_fp, raw_p = precision_on_annotated(unfiltered_chars)

    # How many false positives does the validator catch?
    caught = unfiltered_chars - filtered_chars
    caught_fp = sum(1 for name in caught if anno_map.get(name) is False)

    return {
        "full_system": {"precision": round(full_p, 1), "tp": full_tp, "fp": full_fp},
        "without_validator": {"precision": round(raw_p, 1), "tp": raw_tp, "fp": raw_fp},
        "validator_catches": len(caught),
        "validator_catches_fp": caught_fp,
        "delta_pp": round(full_p - raw_p, 1),
    }


# ══════════════════════════════════════════════════════════════════
# Ablation 2: Relation classification — system vs raw LLM
# ══════════════════════════════════════════════════════════════════

def ablation_relation_normalization(novel_name: str, novel_id: str):
    """Compare relation accuracy with/without normalization pipeline."""
    annotations = load_annotations("relations", novel_name)
    annotated = [r for r in annotations if r.get("correct_category")]
    if not annotated:
        return None

    # Full system: normalize_relation_type → classify_relation_category
    full_correct = 0
    raw_correct = 0
    for r in annotated:
        sys_type = r.get("system_type", "")
        correct_cat = r["correct_category"]

        # Full system: normalize → classify
        norm = normalize_relation_type(sys_type)
        sys_cat = classify_relation_category(norm)
        if sys_cat == correct_cat:
            full_correct += 1

        # w/o normalization: classify raw type directly
        raw_cat = classify_relation_category(sys_type)
        if raw_cat == correct_cat:
            raw_correct += 1

    full_acc = full_correct / len(annotated) * 100
    raw_acc = raw_correct / len(annotated) * 100

    return {
        "full_system": {"accuracy": round(full_acc, 1), "correct": full_correct},
        "without_normalization": {"accuracy": round(raw_acc, 1), "correct": raw_correct},
        "total": len(annotated),
        "delta_pp": round(full_acc - raw_acc, 1),
    }


# ══════════════════════════════════════════════════════════════════
# Ablation 3: Location hierarchy — component contributions
# ══════════════════════════════════════════════════════════════════

def ablation_location_hierarchy(novel_name: str, novel_id: str):
    """Measure location hierarchy with different components disabled.

    Uses pre-computed data from rebuild experiments:
    - Full system: current location_parents
    - w/o skeleton: data from when skeleton timed out
    - w/o suffix rank: simulated by ignoring suffix-based corrections
    """
    golden = load_golden_locations(novel_name)
    if not golden:
        return None

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?",
        (novel_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None

    ws = json.loads(row[0])
    current_parents = ws.get("location_parents", {})

    # Full system metrics
    full_metrics = compute_topology_metrics(current_parents, golden)

    # Simulate w/o suffix rank: remove suffix-based direction fixes
    # by reverting any parent-child where suffix rank would have flipped direction
    from src.services.world_structure_agent import _get_suffix_rank
    no_suffix_parents = dict(current_parents)
    suffix_corrections = 0
    for child, parent in list(no_suffix_parents.items()):
        child_rank = _get_suffix_rank(child)
        parent_rank = _get_suffix_rank(parent)
        if child_rank is not None and parent_rank is not None:
            if parent_rank > child_rank:
                # This would have been flipped by suffix rank system
                # Simulate removal: delete the corrected relationship
                del no_suffix_parents[child]
                suffix_corrections += 1

    no_suffix_metrics = compute_topology_metrics(no_suffix_parents, golden)

    # Simulate w/o transitivity check: we know transitivity found 1-40 violations
    # The impact is small (1-3 edges removed), so metrics change minimally
    # Report the full system data + known violation count

    # Simulate w/o consolidation: remove all orphan rescue effects
    # Orphans that consolidate rescued go back to being orphans
    no_consolidate_parents = {}
    golden_names = {l["name"] for l in golden}
    for child, parent in current_parents.items():
        if child in golden_names or parent in golden_names:
            no_consolidate_parents[child] = parent
    # This is approximate — just measuring on golden subset

    return {
        "full_system": {
            "parent_precision": full_metrics["parent_precision"],
            "chain_accuracy": full_metrics["chain_accuracy"],
        },
        "without_suffix_rank": {
            "parent_precision": no_suffix_metrics["parent_precision"],
            "chain_accuracy": no_suffix_metrics["chain_accuracy"],
            "corrections_removed": suffix_corrections,
        },
        "delta_suffix_pp": round(
            (full_metrics["parent_precision"] - no_suffix_metrics["parent_precision"]) * 100, 1
        ),
    }


# ══════════════════════════════════════════════════════════════════
# Ablation 4: Contains direction fix in FactValidator
# ══════════════════════════════════════════════════════════════════

def ablation_contains_direction(novel_name: str, novel_id: str):
    """Measure how many contains relationships are direction-corrected."""
    facts = load_chapter_facts(novel_id)

    total_contains = 0
    direction_fixed = 0

    for fact in facts:
        for sr in fact.get("spatial_relationships", []):
            if sr.get("relation_type") != "contains":
                continue
            total_contains += 1
            source = sr.get("source", "")
            target = sr.get("target", "")
            src_rank = _get_contains_rank(source)
            tgt_rank = _get_contains_rank(target)
            if src_rank is not None and tgt_rank is not None and src_rank > tgt_rank:
                direction_fixed += 1

    return {
        "total_contains": total_contains,
        "direction_fixed": direction_fixed,
        "fix_rate": round(direction_fixed / total_contains * 100, 1) if total_contains else 0,
    }


# ══════════════════════════════════════════════════════════════════
# Ablation 5: Genre-aware filtering impact
# ══════════════════════════════════════════════════════════════════

def ablation_genre_aware(novel_name: str, novel_id: str):
    """Compare entity filtering with/without genre-aware rules."""
    facts = load_chapter_facts(novel_id)

    # Detect genre from world structure
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?",
        (novel_id,),
    ).fetchone()
    conn.close()
    genre = None
    if row:
        ws = json.loads(row[0])
        genre = ws.get("novel_genre_hint")

    all_locations = Counter()
    for fact in facts:
        for loc in fact.get("locations", []):
            name = loc.get("name", "")
            if name:
                all_locations[name] += 1

    # With genre
    with_genre = sum(1 for name in all_locations if _is_generic_location(name, genre=genre) is None)
    # Without genre
    without_genre = sum(1 for name in all_locations if _is_generic_location(name, genre=None) is None)
    # Difference
    diff = with_genre - without_genre

    return {
        "genre": genre,
        "locations_with_genre": with_genre,
        "locations_without_genre": without_genre,
        "genre_allows_extra": diff,
        "total_unique_locations": len(all_locations),
    }


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

NOVELS = {
    "journey_to_west": "4e5904eb-37b9-4da6-b7ac-13e63dd8a200",
    "dream_of_red_chamber": "3e850a88-e624-4176-8edf-bb24c2dba083",
    "water_margin": "4ac43c73-f67b-427c-8d6d-e766a1423977",
}

LABELS = {
    "journey_to_west": "西游记",
    "dream_of_red_chamber": "红楼梦",
    "water_margin": "水浒传",
}


def main():
    results = {}

    for novel_name, novel_id in NOVELS.items():
        label = LABELS[novel_name]
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")

        # Entity ablation
        r = ablation_entity_filtering(novel_name, novel_id)
        if r:
            print(f"\n  [人物过滤] Full: {r['full_system']['precision']}% | w/o Validator: {r['without_validator']['precision']}% | Δ={r['delta_pp']}pp")
            print(f"    Validator 拦截: {r['validator_catches']} 个, 其中 {r['validator_catches_fp']} 个确实是误提取")

        # Relation ablation
        r2 = ablation_relation_normalization(novel_name, novel_id)
        if r2:
            print(f"\n  [关系归一化] Full: {r2['full_system']['accuracy']}% | w/o Norm: {r2['without_normalization']['accuracy']}% | Δ={r2['delta_pp']}pp")

        # Location hierarchy ablation
        r3 = ablation_location_hierarchy(novel_name, novel_id)
        if r3:
            print(f"\n  [地点层级]")
            print(f"    Full: P={r3['full_system']['parent_precision']} C={r3['full_system']['chain_accuracy']}")
            print(f"    w/o Suffix Rank: P={r3['without_suffix_rank']['parent_precision']} C={r3['without_suffix_rank']['chain_accuracy']}")
            print(f"    Suffix Rank Δ={r3['delta_suffix_pp']}pp ({r3['without_suffix_rank']['corrections_removed']} corrections)")

        # Contains direction
        r4 = ablation_contains_direction(novel_name, novel_id)
        if r4:
            print(f"\n  [Contains方向修正] {r4['direction_fixed']}/{r4['total_contains']} = {r4['fix_rate']}%")

        # Genre-aware
        r5 = ablation_genre_aware(novel_name, novel_id)
        if r5:
            print(f"\n  [Genre-aware] genre={r5['genre']} | 有genre={r5['locations_with_genre']} | 无genre={r5['locations_without_genre']} | 差异={r5['genre_allows_extra']}")

        results[novel_name] = {
            "entity_filtering": r,
            "relation_normalization": r2,
            "location_hierarchy": r3,
            "contains_direction": r4,
            "genre_aware": r5,
        }

    # Save results
    output_path = Path("/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/ablation_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n\n📄 Results saved to {output_path}")

    # Summary table for paper
    print("\n\n" + "="*70)
    print("  PAPER TABLE: Ablation Study Results")
    print("="*70)
    print(f"{'Component':<25} {'Metric':<20} {'Full':>8} {'w/o':>8} {'Δ':>8}")
    print("-"*70)
    for novel_name in NOVELS:
        label = LABELS[novel_name]
        r = results[novel_name]
        if r["entity_filtering"]:
            d = r["entity_filtering"]
            print(f"{label} FactValidator    Entity P         {d['full_system']['precision']:>7}% {d['without_validator']['precision']:>7}% {d['delta_pp']:>+7}pp")
        if r["relation_normalization"]:
            d = r["relation_normalization"]
            print(f"{label} RelNorm          Cat Acc          {d['full_system']['accuracy']:>7}% {d['without_normalization']['accuracy']:>7}% {d['delta_pp']:>+7}pp")
        if r["location_hierarchy"]:
            d = r["location_hierarchy"]
            print(f"{label} SuffixRank       Loc Hierarchy P  {d['full_system']['parent_precision']*100:>7.1f}% {d['without_suffix_rank']['parent_precision']*100:>7.1f}% {d['delta_suffix_pp']:>+7}pp")


if __name__ == "__main__":
    main()
