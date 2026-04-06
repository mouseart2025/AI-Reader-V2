"""Phase 1b: E-幻觉父节点 弱证据子节点清理器.

针对 errata 标记的 E-幻觉父节点 (mc≤2 但 children≥10):
  - 将 mc=0 子节点 (零章节证据) 上提到 grandparent
  - 保留 mc≥1 子节点 (有证据, 不冒然移动)
  - 目标: 把 children_count 降到 < 10 同时保留有证据的结构

策略: 弱证据优先上提, 保持 idempotent.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.migrate_hierarchy_from_errata import (  # noqa: E402
    DB_PATH, KB_DIR, NOVEL_ID_MAP,
)


def load_mentions(conn, novel_id) -> Counter:
    c = Counter()
    for (fj,) in conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ):
        d = json.loads(fj)
        for loc in (d.get("locations") or []):
            if loc.get("name"):
                c[loc["name"]] += 1
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--target-children", type=int, default=9,
                    help="target children_count for phantom parents (default 9)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    novel_id = NOVEL_ID_MAP[args.novel]
    gold_path = KB_DIR / f"{args.novel}_errata_gold.json"
    with gold_path.open(encoding="utf-8") as f:
        gold = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    ws = json.loads(
        conn.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
        ).fetchone()[0]
    )
    lp = ws["location_parents"]
    mentions = load_mentions(conn, novel_id)

    # 收集 E-幻觉父节点
    phantoms = []
    for name, node in gold["nodes"].items():
        if "E-幻觉父节点" not in node.get("error_types", []):
            continue
        if name not in lp and name not in lp.values():
            continue
        phantoms.append(name)

    print(f"[plan] {len(phantoms)} phantom parents to treat:")
    total_lifts = 0
    lift_plan: list[tuple[str, str, str]] = []  # (child, old_parent, new_parent)
    for phantom in phantoms:
        children = [c for c, p in lp.items() if p == phantom]
        grandparent = lp.get(phantom, "天下") or "天下"
        # 按mc升序排序, 优先上提mc=0
        children.sort(key=lambda c: (mentions.get(c, 0), c))
        current_count = len(children)
        print(f"\n  {phantom} (mc={mentions.get(phantom,0)}) → kids={current_count}, "
              f"grandparent={grandparent}")
        lifted = 0
        # 两轮: 先 mc=0, 若仍超阈值再 mc=1 (mc≥2 绝不上提)
        for max_mc in (0, 1):
            for c in children:
                if (c, phantom, grandparent) in lift_plan:
                    continue
                if len(children) - lifted <= args.target_children:
                    break
                mc = mentions.get(c, 0)
                if mc > max_mc:
                    continue
                lift_plan.append((c, phantom, grandparent))
                lifted += 1
                if args.verbose:
                    print(f"    lift: {c} (mc={mc})")
            if len(children) - lifted <= args.target_children:
                break
        print(f"  → lifting {lifted}, remaining={current_count - lifted}")
        total_lifts += lifted

    print(f"\n[stats] total lifts: {total_lifts}")
    if not args.apply:
        print("\n[dry-run] Use --apply to execute.")
        return 0

    # Backup
    backup_dir = KB_DIR / "backups"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{args.novel}-ws-phantom-fix-{ts}.json"
    with backup_path.open("w", encoding="utf-8") as f:
        json.dump(ws, f, ensure_ascii=False, indent=2)
    print(f"[info] Backup: {backup_path.name}")

    for child, _, new_parent in lift_plan:
        lp[child] = new_parent

    ws["_migration_metadata"] = ws.get("_migration_metadata", {})
    ws["_migration_metadata"].setdefault("phantom_fixes", []).append({
        "applied_at": datetime.now().isoformat(),
        "backup": backup_path.name,
        "lifts": total_lifts,
    })
    conn.execute(
        "UPDATE world_structures SET structure_json=?, updated_at=datetime('now') WHERE novel_id=?",
        (json.dumps(ws, ensure_ascii=False), novel_id),
    )
    conn.commit()
    conn.close()
    print(f"[applied] {total_lifts} children lifted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
