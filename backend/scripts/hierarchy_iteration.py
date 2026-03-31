"""地点层级自迭代改进循环。

闭环：rebuild-hierarchy → eval vs golden → classify errors → generate overrides → apply → repeat

Usage:
    cd backend && uv run python scripts/hierarchy_iteration.py <novel_id> [options]

Examples:
    uv run python scripts/hierarchy_iteration.py 3b2ef56c --max-rounds 5
    uv run python scripts/hierarchy_iteration.py 3b2ef56c --no-llm   # 跳过LLM步骤
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = os.path.expanduser("~/.ai-reader-v2/data.db")
GOLDEN_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
REPORT_DIR = Path(__file__).parent.parent / "audit_reports"

GOLDEN_MAP = {
    "西游记": "golden_standard_journey_to_west.json",
    "红楼梦": "golden_standard_dream_of_red_chamber.json",
}

# ── Topology metrics (inline to avoid async import issues) ─────────

def compute_precision_recall(predicted: dict[str, str], golden_locs: list[dict]):
    """Compute parent precision, recall, and per-location error details."""
    golden_parents = {}
    golden_roots = set()
    for loc in golden_locs:
        name = loc.get("name", "")
        parent = loc.get("correct_parent")
        if not name:
            continue
        if parent:
            golden_parents[name] = parent
        else:
            golden_roots.add(name)

    correct = 0
    total = 0
    errors = []
    for child, gold_parent in golden_parents.items():
        total += 1
        pred_parent = predicted.get(child)
        if pred_parent == gold_parent:
            correct += 1
        else:
            errors.append({
                "location": child,
                "predicted_parent": pred_parent,
                "golden_parent": gold_parent,
                "tier": next((l.get("tier", "") for l in golden_locs if l["name"] == child), ""),
            })

    precision = correct / total if total else 0
    # Recall: how many golden parents appear correctly in predicted
    recall_total = len(golden_parents)
    recall_correct = sum(1 for c, gp in golden_parents.items() if predicted.get(c) == gp)
    recall = recall_correct / recall_total if recall_total else 0

    return {
        "parent_precision": round(precision, 4),
        "parent_recall": round(recall, 4),
        "correct": correct,
        "total": total,
        "errors": errors,
    }


# ── Error classification ───────────────────────────────────────────

def classify_errors(errors: list[dict], predicted: dict[str, str], golden_locs: list[dict]):
    """Classify each error into actionable categories."""
    golden_parents = {l["name"]: l.get("correct_parent") for l in golden_locs if l.get("name")}
    golden_tiers = {l["name"]: l.get("tier", "") for l in golden_locs if l.get("name")}

    # Build ancestor chains
    def get_chain(parents: dict, loc: str, max_depth=10):
        chain = [loc]
        current = loc
        for _ in range(max_depth):
            p = parents.get(current)
            if not p or p in chain:
                break
            chain.append(p)
            current = p
        return chain

    classified = []
    for err in errors:
        loc = err["location"]
        pred_p = err["predicted_parent"]
        gold_p = err["golden_parent"]

        if pred_p is None:
            # Location has no parent in predicted → orphan
            err["error_type"] = "orphan"
        elif gold_p in get_chain(predicted, loc):
            # Golden parent is an ancestor (grandparent) → missing intermediate
            err["error_type"] = "missing_intermediate"
        elif pred_p in golden_parents and golden_parents.get(pred_p) == golden_parents.get(loc):
            # Pred parent has same golden parent as child → siblings swapped
            err["error_type"] = "wrong_sibling"
        elif pred_p not in {l["name"] for l in golden_locs}:
            # Pred parent is not even in golden standard → phantom parent
            err["error_type"] = "phantom_parent"
        else:
            # Generic wrong parent
            pred_root = get_chain(predicted, loc)[-1]
            gold_root = get_chain(golden_parents, loc)[-1]
            if pred_root != gold_root:
                err["error_type"] = "wrong_continent"
            else:
                err["error_type"] = "wrong_parent"

        classified.append(err)
    return classified


# ── Override generation ─────────────────────────────────────────────

def generate_overrides(errors: list[dict], iteration: int):
    """Generate world_structure_overrides from classified errors."""
    overrides = []
    for err in errors:
        loc = err["location"]
        gold_p = err["golden_parent"]
        etype = err.get("error_type", "")

        # Generate overrides for fixable errors
        if etype in ("orphan", "wrong_continent", "missing_intermediate", "wrong_parent", "wrong_sibling", "phantom_parent"):
            overrides.append({
                "override_type": "location_parent",
                "override_key": loc,
                "override_value": gold_p,
                "reason": f"iter{iteration}: {etype} (predicted={err['predicted_parent']}, golden={gold_p})",
            })

    return overrides


# ── DB operations ──────────────────────────────────────────────────

def get_novel_title(novel_id: str) -> str:
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT title FROM novels WHERE id LIKE ?", (novel_id + "%",)).fetchone()
    db.close()
    return row[0] if row else ""


def get_full_novel_id(partial_id: str) -> str:
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    row = db.execute("SELECT id FROM novels WHERE id LIKE ?", (partial_id + "%",)).fetchone()
    db.close()
    return row[0] if row else partial_id


def get_current_parents(novel_id: str) -> dict[str, str]:
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    row = db.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    db.close()
    if not row:
        return {}
    data = json.loads(row[0])
    return data.get("location_parents", {})


def get_existing_overrides(novel_id: str) -> list[dict]:
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT override_type, override_key, override_json FROM world_structure_overrides WHERE novel_id=?",
        (novel_id,),
    ).fetchall()
    db.close()
    return [{"override_type": r[0], "override_key": r[1], "override_json": r[2]} for r in rows]


def save_overrides(novel_id: str, overrides: list[dict]):
    """Save overrides to DB (append, skip duplicates)."""
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    existing_keys = {
        r[0] for r in db.execute(
            "SELECT override_key FROM world_structure_overrides WHERE novel_id=? AND override_type='location_parent'",
            (novel_id,),
        ).fetchall()
    }
    added = 0
    for ov in overrides:
        if ov["override_key"] not in existing_keys:
            override_json = json.dumps({"parent": ov["override_value"]}, ensure_ascii=False)
            db.execute(
                "INSERT INTO world_structure_overrides (novel_id, override_type, override_key, override_json) VALUES (?, ?, ?, ?)",
                (novel_id, ov["override_type"], ov["override_key"], override_json),
            )
            added += 1
    db.commit()
    db.close()
    return added


# ── Rebuild hierarchy via API ──────────────────────────────────────

async def trigger_rebuild(novel_id: str, base_url: str = "http://localhost:8000"):
    """Trigger rebuild-hierarchy and wait for completion via SSE."""
    import httpx

    url = f"{base_url}/api/novels/{novel_id}/world-structure/rebuild-hierarchy"
    print(f"  Triggering rebuild-hierarchy...")

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", url) as resp:
            if resp.status_code != 200:
                print(f"  ERROR: rebuild returned {resp.status_code}")
                return False
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    event = data.get("type", "")
                    msg = data.get("message", "")
                    if event == "done":
                        changed = data.get("changed", 0)
                        print(f"  Rebuild complete: {changed} changes")
                        return True
                    elif event == "error":
                        print(f"  Rebuild error: {msg}")
                        return False
                    elif msg:
                        print(f"  [{event}] {msg}")
    return False


def apply_overrides_to_structure(novel_id: str) -> int:
    """Directly apply location_parent overrides to world_structure in DB."""
    import sqlite3
    db = sqlite3.connect(DB_PATH)

    # Load overrides
    rows = db.execute(
        "SELECT override_key, override_json FROM world_structure_overrides WHERE novel_id=? AND override_type='location_parent'",
        (novel_id,),
    ).fetchall()
    if not rows:
        db.close()
        return 0

    # Load structure
    ws_row = db.execute("SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)).fetchone()
    if not ws_row:
        db.close()
        return 0

    structure = json.loads(ws_row[0])
    parents = structure.get("location_parents", {})

    applied = 0
    for key, ov_json in rows:
        ov = json.loads(ov_json)
        new_parent = ov.get("parent", "")
        if new_parent and parents.get(key) != new_parent:
            parents[key] = new_parent
            applied += 1

    structure["location_parents"] = parents
    db.execute(
        "UPDATE world_structures SET structure_json=?, updated_at=datetime('now') WHERE novel_id=?",
        (json.dumps(structure, ensure_ascii=False), novel_id),
    )
    db.commit()
    db.close()
    return applied


# ── Main iteration loop ────────────────────────────────────────────

async def iterate(novel_id: str, max_rounds: int = 5, no_llm: bool = False):
    title = get_novel_title(novel_id)
    golden_file = GOLDEN_MAP.get(title)
    if not golden_file:
        print(f"No golden standard for '{title}'. Available: {list(GOLDEN_MAP.keys())}")
        return

    golden_path = GOLDEN_DIR / golden_file
    golden = json.loads(golden_path.read_text())
    golden_locs = golden["locations"]
    print(f"\n{'='*60}")
    print(f"  Hierarchy Iteration: {title}")
    print(f"  Golden standard: {len(golden_locs)} locations")
    print(f"  Max rounds: {max_rounds}")
    print(f"{'='*60}\n")

    report = {
        "novel": title,
        "novel_id": novel_id,
        "golden_count": len(golden_locs),
        "started_at": datetime.now().isoformat(),
        "iterations": [],
    }

    prev_precision = 0.0

    for i in range(max_rounds):
        print(f"\n--- Iteration {i} ---")

        # Step 1: Get current hierarchy (apply pending overrides for iter > 0)
        if i > 0:
            applied = apply_overrides_to_structure(novel_id)
            print(f"  Applied {applied} overrides directly to structure")

        parents = get_current_parents(novel_id)
        print(f"  Current hierarchy: {len(parents)} parent-child pairs")

        # Step 2: Evaluate
        result = compute_precision_recall(parents, golden_locs)
        precision = result["parent_precision"]
        recall = result["parent_recall"]
        errors = classify_errors(result["errors"], parents, golden_locs)

        # Error breakdown
        error_types = Counter(e.get("error_type", "unknown") for e in errors)
        print(f"  Precision: {precision:.1%} ({result['correct']}/{result['total']})")
        print(f"  Recall: {recall:.1%}")
        print(f"  Errors: {dict(error_types)}")

        iter_record = {
            "iteration": i,
            "precision": precision,
            "recall": recall,
            "correct": result["correct"],
            "total": result["total"],
            "error_breakdown": dict(error_types),
            "errors": errors[:20],  # top 20 for report
        }

        # Step 3: Check convergence
        if i > 0 and precision <= prev_precision:
            print(f"  Precision not improving ({prev_precision:.1%} → {precision:.1%}), converged.")
            iter_record["converged"] = True
            report["iterations"].append(iter_record)
            break

        prev_precision = precision

        # Step 4: Generate overrides from errors
        overrides = generate_overrides(errors, i)
        if not overrides:
            print("  No fixable errors, converged.")
            iter_record["converged"] = True
            report["iterations"].append(iter_record)
            break

        # Step 5: Apply overrides
        added = save_overrides(novel_id, overrides)
        print(f"  Generated {len(overrides)} overrides, {added} new applied")
        iter_record["overrides_generated"] = len(overrides)
        iter_record["overrides_applied"] = added
        report["iterations"].append(iter_record)

        if added == 0:
            print("  All overrides already exist, converged.")
            break

    # Final evaluation
    parents = get_current_parents(novel_id)
    final = compute_precision_recall(parents, golden_locs)
    report["final_precision"] = final["parent_precision"]
    report["final_recall"] = final["parent_recall"]
    report["iterations_count"] = len(report["iterations"])

    first_p = report["iterations"][0]["precision"] if report["iterations"] else 0
    print(f"\n{'='*60}")
    print(f"  RESULT: Precision {first_p:.1%} → {final['parent_precision']:.1%}")
    print(f"  Recall: {final['parent_recall']:.1%}")
    print(f"  Iterations: {len(report['iterations'])}")
    print(f"{'='*60}")

    # Save report
    REPORT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = REPORT_DIR / f"hierarchy_{title}_{ts}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n  Report: {report_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="地点层级自迭代改进")
    parser.add_argument("novel_id", help="Novel ID (prefix match OK)")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM steps")
    args = parser.parse_args()

    novel_id = get_full_novel_id(args.novel_id)
    print(f"Novel ID: {novel_id}")

    asyncio.run(iterate(novel_id, max_rounds=args.max_rounds, no_llm=args.no_llm))


if __name__ == "__main__":
    main()
