"""Phase 1b: C-tier倒置 批量修复器.

针对 errata 标记的 C-tier倒置 错误, 按后缀+语义规则自动修复:

规则:
  - X界/境界  → tier=region (边界区域同级)
  - X水府/神府 → tier=site (水下/神灵宅邸)
  - 人名+府   → tier=site (府邸)
  - X城池/X城 (parent是山/岭/河等region) → reparent 到 grandparent (使其成为region的sibling)
  - mc=0 噪声节点 → delete

Safe: 只处理errata中标记的error/suspect节点, 保留gold作为修复白名单.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.migrate_hierarchy_from_errata import (  # noqa: E402
    DB_PATH, KB_DIR, NOVEL_ID_MAP,
    _delete_in_ws, _reparent_in_ws, _retier_in_ws,
)

# 按后缀模式的修复规则
TIER_FIX_RULES = [
    # (suffix, new_tier) - 按后缀匹配, 优先级从长到短
    ("境界", "region"),
    ("水府", "site"),
    ("神府", "site"),
    ("界", "region"),
]

# 城池/城 reparent 规则: 若 parent 是 region 类(山/岭/河), reparent 到 grandparent
REGION_SUFFIXES_FOR_REPARENT = ("山", "岭", "峰", "河", "江", "湖", "海", "谷", "涧")
CITY_REPARENT_SUFFIXES = ("城池", "城")


def classify_fix(name: str, parent: str, tier: str, ptier: str, mc: int) -> tuple[str, str | None]:
    """返回 (action, param). action ∈ {retier, reparent, delete, skip}."""
    # 规则1: mc=0 + 名字可疑 → 删除
    if mc == 0 and any(name.endswith(s) for s in ("境界", "城池")):
        return ("delete", None)

    # 规则2: 特定后缀 retier
    for suffix, new_tier in TIER_FIX_RULES:
        if name.endswith(suffix) and tier != new_tier:
            return ("retier", new_tier)

    # 规则3: 人名/官职+府 → site  (e.g., 长史府)
    if name.endswith("府") and not name.endswith(("水府", "神府")) and tier == "city":
        # 若非城市/州府级行政府, 降为site
        if ptier == "region":  # 在山里的"府"基本都是府邸
            return ("retier", "site")

    # 规则4: X城/X城池 + parent是region类 → reparent到grandparent
    if any(name.endswith(s) for s in CITY_REPARENT_SUFFIXES) and tier == "city":
        if ptier == "region":
            return ("reparent", "grandparent")

    return ("skip", None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    novel_id = NOVEL_ID_MAP[args.novel]
    gold_path = KB_DIR / f"{args.novel}_errata_gold.json"
    with gold_path.open(encoding="utf-8") as f:
        gold = json.load(f)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,))
    ws = json.loads(cur.fetchone()[0])
    lp = ws["location_parents"]; lt = ws["location_tiers"]

    # 收集所有 C-tier倒置 节点 (error + suspect)
    candidates = []
    for name, node in gold["nodes"].items():
        if "C-tier倒置" not in node.get("error_types", []):
            continue
        if name not in lp and name not in lt:
            continue
        parent = lp.get(name, "")
        tier = lt.get(name, "")
        ptier = lt.get(parent, "")
        mc = int(node.get("mc", 0) or 0)
        action, param = classify_fix(name, parent, tier, ptier, mc)
        candidates.append((name, parent, tier, ptier, mc, action, param))

    # 打印计划
    print(f"[plan] {len(candidates)} C-tier倒置 candidates:")
    plan_stats = {"retier": 0, "reparent": 0, "delete": 0, "skip": 0}
    for name, parent, tier, ptier, mc, action, param in candidates:
        plan_stats[action] += 1
        if args.verbose or action != "skip":
            arrow = f" → {param}" if param else ""
            print(f"  [{action:8}] {name}({tier}) ⊂ {parent}({ptier}) mc={mc}{arrow}")
    print(f"\n[stats] {plan_stats}")

    if not args.apply:
        print("\n[dry-run] Use --apply to execute.")
        return 0

    # Backup
    backup_dir = KB_DIR / "backups"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{args.novel}-ws-tier-fix-{ts}.json"
    with backup_path.open("w", encoding="utf-8") as f:
        json.dump(ws, f, ensure_ascii=False, indent=2)
    print(f"[info] Backup: {backup_path.name}")

    edits = 0
    for name, parent, tier, ptier, mc, action, param in candidates:
        if action == "skip":
            continue
        if action == "retier":
            edits += _retier_in_ws(ws, name, param)
        elif action == "reparent":
            # param="grandparent" → 找 current parent 的 parent
            grandparent = lp.get(parent, "天下") or "天下"
            edits += _reparent_in_ws(ws, name, grandparent)
        elif action == "delete":
            edits += _delete_in_ws(ws, name)

    ws["_migration_metadata"] = ws.get("_migration_metadata", {})
    ws["_migration_metadata"].setdefault("tier_inversion_fixes", []).append({
        "applied_at": datetime.now().isoformat(),
        "backup": backup_path.name,
        "stats": plan_stats,
        "edits": edits,
    })
    cur.execute(
        "UPDATE world_structures SET structure_json=?, updated_at=datetime('now') WHERE novel_id=?",
        (json.dumps(ws, ensure_ascii=False), novel_id),
    )
    conn.commit()
    conn.close()
    print(f"[applied] {plan_stats}, total edits={edits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
