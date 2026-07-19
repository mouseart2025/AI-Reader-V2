"""L2 · Reproducibility check for paper numbers.

Layer 2 in the 5-layer data integrity framework (see
PAPER_ROOT/paper/roadmap-2026-2030.md or paper-plan.md).

Purpose:
  For each of the 5 paper novels, query the hierarchy_snapshots DB
  and compare metrics-per-stage against paper/evaluation/v071/ablation-by-stage.json.
  Surface any drift so we know paper numbers are currently reproducible.

Usage:
    cd backend && uv run python scripts/reproducibility_check.py
    cd backend && uv run python scripts/reproducibility_check.py --regenerate

Exits 0 if all 5 novels match the JSON; 1 if any drift.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Paths
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
PAPER_EVAL = REPO_ROOT.parent / "ai-reader-internal" / "paper" / "evaluation" / "v071"
STORED_JSON = PAPER_EVAL / "ablation-by-stage.json"
OUT_FRESH_JSON = PAPER_EVAL / "ablation-by-stage-regenerated.json"

# Paper's 5 novels (title → slug)
PAPER_NOVELS = [
    ("西游记", "xiyouji"),
    ("红楼梦", "honglou"),
    ("水浒传", "shuihu"),
    ("三国演义", "sanguo"),
    ("封神演义", "fengshen"),
]

STAGES = ["import", "tier", "votes", "prior", "edmonds", "suffix"]


def db_path() -> Path:
    return Path.home() / ".ai-reader-v2" / "data.db"


def resolve_novel_id(con: sqlite3.Connection, title: str) -> str | None:
    """Pick the novel_id with the most snapshot history for this title.

    If two novels share title, prefer one with 6-stage full pipeline history,
    then one with any snapshots, else arbitrary.
    """
    cur = con.cursor()
    cur.execute(
        """
        SELECT n.id,
               (SELECT COUNT(DISTINCT tag) FROM hierarchy_snapshots WHERE novel_id=n.id) as tag_count,
               (SELECT COUNT(*) FROM hierarchy_snapshots WHERE novel_id=n.id) as ver_count,
               (SELECT COUNT(*) FROM chapter_facts WHERE novel_id=n.id) as fact_count
        FROM novels n
        WHERE n.title = ?
        ORDER BY ver_count DESC, fact_count DESC
        """,
        (title,),
    )
    rows = cur.fetchall()
    return rows[0][0] if rows else None


def latest_metrics_per_stage(con: sqlite3.Connection, novel_id: str) -> dict[str, dict]:
    """Return {stage: {depth, max_ch, roots, nodes}} from latest snapshot at each tag."""
    cur = con.cursor()
    cur.execute(
        """
        SELECT tag, metrics_json
        FROM hierarchy_snapshots
        WHERE novel_id = ?
          AND (novel_id, version) IN (
              SELECT novel_id, MAX(version) FROM hierarchy_snapshots
              WHERE novel_id = ? GROUP BY tag
          )
        """,
        (novel_id, novel_id),
    )
    out: dict[str, dict] = {}
    for tag, metrics_json in cur.fetchall():
        if not metrics_json:
            continue
        m = json.loads(metrics_json)
        out[tag] = {
            "depth": m.get("avg_depth") or m.get("depth"),
            "max_ch": m.get("max_children") or m.get("max_ch"),
            "roots": m.get("root_count") or m.get("roots"),
            "nodes": m.get("node_count") or m.get("nodes"),
        }
    return out


def format_row(stage: str, m: dict | None) -> str:
    if m is None:
        return f"  {stage:<8} (no snapshot)"
    return (
        f"  {stage:<8} depth={m.get('depth','?')} max_ch={m.get('max_ch','?')} "
        f"roots={m.get('roots','?')} nodes={m.get('nodes','?')}"
    )


def diff_metrics(a: dict | None, b: dict | None) -> list[str]:
    """Return list of human-readable diff lines."""
    if a is None and b is None:
        return []
    if a is None:
        return [f"MISSING in DB (JSON has: {b})"]
    if b is None:
        return [f"MISSING in JSON (DB has: {a})"]
    diffs = []
    for k in ("depth", "max_ch", "roots", "nodes"):
        if a.get(k) != b.get(k):
            diffs.append(f"{k}: DB={a.get(k)} vs JSON={b.get(k)}")
    return diffs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Write a fresh ablation-by-stage-regenerated.json from current DB",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Emit machine-readable JSON report to stdout only",
    )
    args = parser.parse_args()

    if not db_path().exists():
        print(f"FATAL: DB not found at {db_path()}", file=sys.stderr)
        sys.exit(2)
    if not STORED_JSON.exists():
        print(f"FATAL: stored JSON not found at {STORED_JSON}", file=sys.stderr)
        sys.exit(2)

    stored = json.loads(STORED_JSON.read_text())
    con = sqlite3.connect(db_path())

    report: dict[str, dict] = {}
    any_drift = False

    for title, slug in PAPER_NOVELS:
        novel_id = resolve_novel_id(con, title)
        if not novel_id:
            report[slug] = {"status": "NOVEL_MISSING", "title": title}
            any_drift = True
            continue

        db_metrics = latest_metrics_per_stage(con, novel_id)
        json_metrics = stored.get(slug, {})

        stage_results = {}
        drift = False
        for stage in STAGES:
            a = db_metrics.get(stage)
            b = json_metrics.get(stage)
            diffs = diff_metrics(a, b)
            stage_results[stage] = {
                "db": a,
                "json": b,
                "diff": diffs,
            }
            if diffs:
                drift = True

        report[slug] = {
            "title": title,
            "novel_id": novel_id,
            "stages": stage_results,
            "drift": drift,
        }
        if drift:
            any_drift = True

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(1 if any_drift else 0)

    # Human-readable report
    print("=" * 70)
    print("L2 · Reproducibility Check — paper v14.1 vs current DB")
    print("=" * 70)
    for slug, data in report.items():
        print()
        if data.get("status") == "NOVEL_MISSING":
            print(f"❌ {data['title']:<10} [novel row missing in DB]")
            continue
        mark = "❌" if data["drift"] else "✅"
        print(f"{mark} {data['title']:<10}  id={data['novel_id'][:8]}  "
              f"drift={'YES' if data['drift'] else 'none'}")
        for stage, sr in data["stages"].items():
            if sr["diff"]:
                print(f"  ⚠️  {stage:<8}  {'; '.join(sr['diff'])}")
            elif sr["db"] is None and sr["json"] is None:
                pass  # no data either side, skip silently
            elif sr["db"] is None:
                print(f"  ❌ {stage:<8}  DB missing  (JSON: {sr['json']})")
            else:
                pass  # match, skip quietly

    print()
    print("=" * 70)
    if any_drift:
        print("❌ RESULT: drift detected. Paper numbers not currently reproducible "
              "from DB snapshots.")
        print("   Next steps:")
        print("   - For novels missing edmonds/suffix stages: rerun full pipeline via "
              "rebuild-hierarchy-v2 API or direct skill invocation")
        print("   - For stage-level drift: investigate code changes since paper data "
              "was generated (git log on services/geo_skills/)")
        print("   - Consider regenerating JSON with --regenerate to snapshot current state")
    else:
        print("✅ RESULT: all 5 novels reproduce paper numbers exactly.")

    if args.regenerate:
        fresh = {}
        for slug, data in report.items():
            if data.get("status") == "NOVEL_MISSING":
                continue
            fresh[slug] = {
                stage: sr["db"]
                for stage, sr in data["stages"].items()
                if sr["db"] is not None
            }
        OUT_FRESH_JSON.write_text(
            json.dumps(fresh, ensure_ascii=False, indent=2)
        )
        print(f"\n📝 Fresh DB state written to: {OUT_FRESH_JSON}")

    sys.exit(1 if any_drift else 0)


if __name__ == "__main__":
    main()
