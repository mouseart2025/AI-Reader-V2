"""Compute Cohen's kappa IAA between annotator A (first author, errata gold)
and annotator B (second annotator, submitted IAA file).

Usage:
    cd backend && uv run python scripts/compute_iaa.py [--file <B's json>]

Reads:
    paper/iaa/iaa_annotation_<name>_<date>.json  (Annotator B)
    backend/data/hierarchy_validation/<slug>_errata_gold.json  (Annotator A per novel)

Outputs:
    paper/iaa/iaa_report.md  (human-readable)
    paper/iaa/iaa_report.json  (machine-readable, for paper citation)

Headline metric: binary Cohen's kappa on "system output is correct vs.
needs correction", per-dimension (is_valid, tier, parent), per-novel and
aggregate.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
from collections import Counter
from pathlib import Path

IAA_DIR = Path("/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/iaa")
ERRATA_DIR = Path("/Users/leonfeng/Baiduyun/AISoul/AI-Reader-V2/backend/data/hierarchy_validation")

NOVEL_TO_SLUG = {"西游记": "xiyouji", "红楼梦": "honglou",
                 "水浒传": "shuihu", "三国演义": "sanguo", "封神演义": "fengshen"}


# =============================================================================
# Annotator A: parse errata gold to get per-name verdict + inferred labels
# =============================================================================

def load_errata(slug: str) -> dict[str, dict]:
    p = ERRATA_DIR / f"{slug}_errata_gold.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get("nodes", {})


def a_labels_for_location(errata_node: dict | None, system_tier: str | None,
                          system_parent: str | None) -> dict:
    """Infer A's per-dimension labels for a location task.

    Returns dict with keys: is_valid, tier_agrees_system, parent_agrees_system
    """
    if errata_node is None:
        # Node not in errata — A did not flag it → treat as agreeing with system
        return {"is_valid": True, "tier_agrees": True, "parent_agrees": True,
                "verdict": "未审阅/默认正确"}

    verdict = errata_node.get("verdict", "")
    error_types = set(errata_node.get("error_types") or [])
    reasons = errata_node.get("reasons", "") or ""

    # is_valid inferred: A's B-type errors or verdict mentioning 非地名/应删除
    invalid_markers = any(t.startswith(("B-", "A-"))
                           or "应删除" in t or "非地名" in t or "幻觉" in t
                           for t in error_types)
    invalid_markers = invalid_markers or "应删除" in reasons or "非地名" in reasons

    # tier_agrees: A flagged C-tier error → disagree
    tier_flagged = any("tier" in t.lower() or t.startswith("C-") for t in error_types)

    # parent_agrees: A flagged D-* or parent → disagree
    parent_flagged = any(t.startswith("D-") or "parent" in t.lower()
                          or "错归" in t for t in error_types)

    return {
        "is_valid": not invalid_markers,
        "tier_agrees": not tier_flagged,
        "parent_agrees": not parent_flagged,
        "verdict": verdict,
        "error_types": list(error_types),
    }


# =============================================================================
# Annotator B: extract labels from the submitted file
# =============================================================================

def b_labels_for_location(task: dict) -> dict:
    a = task.get("annotation") or {}
    is_valid = a.get("is_valid")
    her_tier = a.get("correct_tier")
    her_parent = a.get("correct_parent")

    # Normalize is_valid: "true" / True → True
    if isinstance(is_valid, str):
        is_valid = is_valid.strip().lower() in ("true", "是", "1", "yes")
    elif isinstance(is_valid, bool):
        pass
    else:
        is_valid = None

    sys_tier = task.get("tier_system")
    sys_parent = task.get("parent_system")

    tier_agrees = None
    if her_tier and sys_tier:
        tier_agrees = (her_tier == sys_tier)
    parent_agrees = None
    if her_parent and sys_parent:
        parent_agrees = (her_parent == sys_parent)

    return {
        "is_valid": is_valid,
        "tier_agrees": tier_agrees,
        "parent_agrees": parent_agrees,
    }


def b_labels_for_character(task: dict) -> dict:
    a = task.get("annotation") or {}
    is_valid = a.get("is_valid")
    if isinstance(is_valid, str):
        is_valid = is_valid.strip().lower() in ("true", "是", "1", "yes")
    return {"is_valid": is_valid if isinstance(is_valid, bool) else None}


def b_labels_for_relation(task: dict) -> dict:
    a = task.get("annotation") or {}
    her_type = a.get("correct_type")
    her_cat = a.get("correct_category")
    sys_type = task.get("system_type")
    type_agrees = None
    if her_type:
        type_agrees = (her_type == sys_type)
    return {"type_agrees": type_agrees, "category": her_cat}


# =============================================================================
# Cohen's kappa
# =============================================================================

def cohens_kappa(pairs: list[tuple]) -> tuple[float, float, int, int]:
    """Return (kappa, observed_agreement, agree_count, total)."""
    valid = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(valid)
    if n == 0:
        return 0.0, 0.0, 0, 0
    agree = sum(1 for a, b in valid if a == b)
    p_o = agree / n
    # Marginals
    a_vals = [a for a, _ in valid]
    b_vals = [b for _, b in valid]
    categories = set(a_vals) | set(b_vals)
    p_e = sum((a_vals.count(c) / n) * (b_vals.count(c) / n) for c in categories)
    if p_e >= 1:
        kappa = 1.0
    else:
        kappa = (p_o - p_e) / (1 - p_e)
    return kappa, p_o, agree, n


def interpret_kappa(k: float) -> str:
    if k < 0: return "poor (worse than chance)"
    if k < 0.20: return "slight"
    if k < 0.40: return "fair"
    if k < 0.60: return "moderate"
    if k < 0.75: return "substantial"
    if k < 0.81: return "substantial"
    return "almost perfect"


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None, help="Path to B's IAA file (default: newest in iaa/)")
    args = ap.parse_args()

    if args.file:
        b_path = Path(args.file)
    else:
        candidates = sorted(glob.glob(str(IAA_DIR / "iaa_annotation_*.json")))
        if not candidates:
            raise SystemExit("No IAA annotation file found")
        b_path = Path(candidates[-1])

    print(f"Annotator B file: {b_path.name}\n")
    b_data = json.loads(b_path.read_text())
    tasks = b_data.get("tasks", [])

    # Load errata for each novel present
    errata_cache: dict[str, dict] = {}
    for novel in {t["novel"] for t in tasks}:
        slug = NOVEL_TO_SLUG.get(novel)
        if slug:
            errata_cache[novel] = load_errata(slug)

    # Build pairs
    pairs_is_valid: list[tuple] = []
    pairs_tier_agree: list[tuple] = []  # both "agree_with_system" or not
    pairs_parent_agree: list[tuple] = []
    pairs_char_is_valid: list[tuple] = []

    # Track per-novel too
    by_novel: dict[str, dict] = {}

    # Qualitative: dimensions where A and B disagree
    divergences = []

    for t in tasks:
        novel = t["novel"]
        kind = t["kind"]
        name = t.get("name") or f"{t.get('person_a')}+{t.get('person_b')}"
        errata = errata_cache.get(novel, {})

        if kind == "locations":
            sys_tier = t.get("tier_system")
            sys_parent = t.get("parent_system")
            a_node = errata.get(t["name"])
            a = a_labels_for_location(a_node, sys_tier, sys_parent)
            b = b_labels_for_location(t)

            if a["is_valid"] is not None and b["is_valid"] is not None:
                pairs_is_valid.append((a["is_valid"], b["is_valid"]))
                if a["is_valid"] != b["is_valid"]:
                    divergences.append({
                        "task_id": t["task_id"], "novel": novel, "kind": "location/is_valid",
                        "name": name, "a_says": a["is_valid"], "b_says": b["is_valid"],
                        "b_note": (t.get("annotation") or {}).get("note", "")[:100],
                    })
            if a["tier_agrees"] is not None and b["tier_agrees"] is not None:
                pairs_tier_agree.append((a["tier_agrees"], b["tier_agrees"]))
            if a["parent_agrees"] is not None and b["parent_agrees"] is not None:
                pairs_parent_agree.append((a["parent_agrees"], b["parent_agrees"]))

            by_novel.setdefault(novel, {"loc": []})["loc"].append((a, b))

        elif kind == "characters":
            # A for characters: naming verdicts less structured in errata; default to True
            # unless errata explicitly says 应删除 or 幻觉
            a_node = errata.get(t["name"])
            if a_node and any("应删除" in r or "幻觉" in r for r in
                              [a_node.get("verdict", "")] + (a_node.get("error_types") or [])):
                a_valid = False
            else:
                a_valid = True  # default: A saw no issue
            b_lab = b_labels_for_character(t)
            if b_lab["is_valid"] is not None:
                pairs_char_is_valid.append((a_valid, b_lab["is_valid"]))
                if a_valid != b_lab["is_valid"]:
                    divergences.append({
                        "task_id": t["task_id"], "novel": novel, "kind": "character/is_valid",
                        "name": name, "a_says": a_valid, "b_says": b_lab["is_valid"],
                        "b_note": (t.get("annotation") or {}).get("note", "")[:100],
                    })

    # Compute kappas
    results: dict = {
        "annotator_b_file": b_path.name,
        "total_tasks": len(tasks),
        "metrics": {},
    }

    for label, pairs in [
        ("location_is_valid", pairs_is_valid),
        ("location_tier_agrees_system", pairs_tier_agree),
        ("location_parent_agrees_system", pairs_parent_agree),
        ("character_is_valid", pairs_char_is_valid),
    ]:
        k, p_o, agree, n = cohens_kappa(pairs)
        results["metrics"][label] = {
            "kappa": round(k, 3),
            "observed_agreement": round(p_o, 3),
            "agree_count": agree,
            "total_pairs": n,
            "interpretation": interpret_kappa(k),
        }

    # Output
    out_json = IAA_DIR / "iaa_report.json"
    results["divergence_samples"] = divergences[:20]
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    lines: list[str] = []
    lines.append("# Inter-Annotator Agreement (IAA) Report")
    lines.append("")
    lines.append(f"- **Annotator A**: first author (via per-novel `*_errata_gold.json`)")
    lines.append(f"- **Annotator B**: {b_path.name}")
    lines.append(f"- **Sample size**: 200 nodes (100 西游记 + 100 红楼梦)")
    lines.append("")
    lines.append("## Cohen's Kappa per Dimension")
    lines.append("")
    lines.append("| Dimension | κ | Observed Agreement | Pairs | Interpretation |")
    lines.append("|-----------|---|---|---|---|")
    for label, m in results["metrics"].items():
        lines.append(
            f"| {label} | **{m['kappa']:.3f}** | {m['observed_agreement']:.3f} "
            f"({m['agree_count']}/{m['total_pairs']}) | {m['total_pairs']} | {m['interpretation']} |"
        )
    lines.append("")
    lines.append("## Interpretation (Landis & Koch 1977)")
    lines.append("")
    lines.append("- < 0.00: poor   |  0.00-0.20: slight  |  0.21-0.40: fair")
    lines.append("- 0.41-0.60: moderate  |  0.61-0.80: substantial  |  0.81-1.00: almost perfect")
    lines.append("")
    if divergences:
        lines.append(f"## Sample divergences (first {min(len(divergences),10)} of {len(divergences)})")
        lines.append("")
        lines.append("| Task | Kind | Name | A says | B says | B's note |")
        lines.append("|------|------|------|--------|--------|----------|")
        for d in divergences[:10]:
            lines.append(
                f"| {d['task_id']} | {d['kind']} | {d['name']} | "
                f"{d['a_says']} | {d['b_says']} | {d['b_note']} |"
            )

    out_md = IAA_DIR / "iaa_report.md"
    out_md.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\n[saved] {out_json}")
    print(f"[saved] {out_md}")


if __name__ == "__main__":
    main()
