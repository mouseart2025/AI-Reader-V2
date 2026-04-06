"""跨小说 errata 候选生成器.

基于规则引擎 (15 条自动化规则) 对任意小说的 world_structure 运行,
输出 CSV 格式候选错误清单, 用户只需review标记verdict即可得到gold.

这是 Phase 0 benchmark 的跨小说扩展 - 让其他小说快速拥有 gold annotation.

用法:
    uv run python scripts/generate_errata_candidates.py --novel-id=<uuid>
    uv run python scripts/generate_errata_candidates.py --novel-id=<uuid> --out=/tmp/candidates.csv
    uv run python scripts/generate_errata_candidates.py --all  # 处理所有经典小说

输出CSV列:
    name, parent, tier, layer, mc, children, depth,
    rule_verdict, rule_error_types, rule_reasons,
    verdict (空, 待人工review), notes (空)
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.services.hierarchy_validator import KnowledgeBase, RuleValidator  # noqa: E402

DB_PATH = Path.home() / ".ai-reader-v2" / "data.db"
OUTPUT_DIR = ROOT / "backend" / "data" / "hierarchy_validation" / "candidates"

# 经典中文小说 (有较完整 world_structure 的)
CLASSICAL_NOVELS = {
    "honglou": "c384901a-8b71-437a-af35-b5ec1c56c696",  # 红楼梦
    "shuihu": "4ac43c73-f67b-427c-8d6d-e766a1423977",   # 水浒传
    "sanguo": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",   # 三国演义
    "fengshen": "53013970-effd-4f50-aef7-728ca13de69a", # 封神演义
}


def load_snapshot(novel_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    row = cur.execute(
        "SELECT title FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Novel {novel_id} not found")
    title = row["title"]

    row = cur.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"No world_structure for {novel_id}")
    ws = json.loads(row["structure_json"])
    lp = ws.get("location_parents", {})
    lt = ws.get("location_tiers", {})
    layer_map = ws.get("location_layer_map", {})

    # Compute mentions
    mentions: Counter = Counter()
    for (fj,) in cur.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ):
        d = json.loads(fj)
        for loc in (d.get("locations") or []):
            if loc.get("name"):
                mentions[loc["name"]] += 1
    conn.close()
    return title, lp, lt, layer_map, mentions


def compute_depth(name: str, lp: dict[str, str], memo: dict) -> int:
    if name in memo:
        return memo[name]
    seen = {name}
    cur_n = name
    d = 0
    while cur_n in lp:
        p = lp[cur_n]
        if p in seen or p == cur_n or not p:
            break
        seen.add(p)
        d += 1
        cur_n = p
        if d > 50:
            break
    memo[name] = d
    return d


def generate_candidates(novel_key: str, novel_id: str, out_dir: Path = OUTPUT_DIR):
    print(f"\n[{novel_key}] Loading snapshot...")
    title, lp, lt, layer_map, mentions = load_snapshot(novel_id)
    all_nodes = (set(lp.keys()) | set(lp.values()) | set(lt.keys())) - {"", None}
    children_count: Counter = Counter()
    for c, p in lp.items():
        children_count[p] += 1
    print(f"  title='{title}' nodes={len(all_nodes)} mentions={sum(mentions.values())}")

    # Run rule engine
    kb = KnowledgeBase.load()
    rv = RuleValidator(kb)
    verdicts = rv.validate_snapshot(lp, lt, dict(mentions))

    # Structural flags (not in rule engine, but useful for human review)
    # - 幻觉父: mc≤2 AND children≥10
    # - 零证据有子: mc=0 AND children≥1
    structural_flags: dict[str, list[str]] = {}
    for name in all_nodes:
        flags = []
        mc = mentions.get(name, 0)
        ch = children_count.get(name, 0)
        if mc <= 2 and ch >= 10:
            flags.append("E-幻觉父节点")
        if mc == 0 and ch >= 1:
            flags.append("E-零证据有子")
        # E-消歧重复 (X·Y 且 Y 也存在)
        if "·" in name:
            short = name.split("·")[-1]
            if short in all_nodes:
                flags.append("E-消歧重复")
        if flags:
            structural_flags[name] = flags

    # Generate candidates (only nodes that triggered at least one flag)
    depth_memo: dict[str, int] = {}
    rows = []
    for name in sorted(all_nodes, key=lambda n: (-mentions.get(n, 0), n)):
        v = verdicts.get(name)
        structural = structural_flags.get(name, [])
        rule_errors = v.error_types if v and v.verdict == "error" else []
        all_errors = list(dict.fromkeys(rule_errors + structural))
        if not all_errors:
            continue
        rule_reasons = (v.reasons[0] if v and v.reasons else "")
        rows.append({
            "name": name,
            "parent": lp.get(name, ""),
            "tier": lt.get(name, ""),
            "layer": layer_map.get(name, ""),
            "mc": mentions.get(name, 0),
            "children": children_count.get(name, 0),
            "depth": compute_depth(name, lp, depth_memo),
            "rule_verdict": "error" if all_errors else "correct",
            "rule_error_types": "|".join(all_errors),
            "rule_reasons": rule_reasons,
            "verdict": "",  # for human review
            "final_error_types": "",  # for human review
            "notes": "",
        })

    # Write CSV
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{novel_key}_candidates.csv"
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  → {len(rows)} candidates → {out_path.name}")

    # Stats
    by_type: Counter = Counter()
    for r in rows:
        for t in r["rule_error_types"].split("|"):
            if t:
                by_type[t] += 1
    print(f"  Rule-flagged types:")
    for t, c in by_type.most_common():
        print(f"    {t}: {c}")

    return out_path, len(rows), by_type


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel-id", help="specific novel uuid")
    ap.add_argument("--novel-key", help="key name (e.g. honglou)")
    ap.add_argument("--all", action="store_true", help="process all classical novels")
    ap.add_argument("--out-dir", help="output directory")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else OUTPUT_DIR

    if args.all:
        summary = []
        for key, nid in CLASSICAL_NOVELS.items():
            try:
                path, n, types = generate_candidates(key, nid, out_dir)
                summary.append((key, n, dict(types)))
            except Exception as e:
                print(f"  [error] {key}: {e}", file=sys.stderr)
        print("\n=== SUMMARY ===")
        for key, n, _ in summary:
            print(f"  {key}: {n} candidates")
        return 0

    if args.novel_id:
        key = args.novel_key or args.novel_id[:8]
        generate_candidates(key, args.novel_id, out_dir)
    else:
        ap.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
