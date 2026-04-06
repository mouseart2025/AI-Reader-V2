"""Errata驱动的地点层级数据迁移脚本.

基于人工标注的errata gold, 对现有world_structure执行精确修复:
  - A类节点 (方位/概念/描述/单字/非地名)  → DELETE + 子节点上提
  - B-字形错误                            → RENAME (级联更新所有引用)
  - B-未合并别名                          → MERGE 到目标节点
  - B-修饰语截断                          → DELETE
  - C-tier错误/倒置 (reason中含→)         → UPDATE tier
  - D-parent错误 (reason中含→)            → REPARENT
  - D-孤立顶层                            → REPARENT 到 天下
  - E-消歧重复                            → DELETE 消歧节点 + 子节点合并到原名

不自动处理 (需人工):
  - E-幻觉父节点 (仅flag)
  - C-tier可疑 (仅flag)
  - 其他可疑(suspect) verdict

用法:
    # Dry-run (默认, 输出计划但不执行)
    uv run python scripts/migrate_hierarchy_from_errata.py --novel=xiyouji

    # 实际执行 (写回DB, 备份到 .pre-migration.json)
    uv run python scripts/migrate_hierarchy_from_errata.py --novel=xiyouji --apply

    # 查看详细操作
    uv run python scripts/migrate_hierarchy_from_errata.py --novel=xiyouji -v
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path.home() / ".ai-reader-v2" / "data.db"
KB_DIR = ROOT / "backend" / "data" / "hierarchy_validation"

NOVEL_ID_MAP = {
    "xiyouji": "3b2ef56c-1a55-466a-a7d1-34272446a198",
    "honglou": "c384901a-8b71-437a-af35-b5ec1c56c696",
    "shuihu": "4ac43c73-f67b-427c-8d6d-e766a1423977",
    "sanguo": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
    "fengshen": "53013970-effd-4f50-aef7-728ca13de69a",
}

# ─────────────────────────────────────────────────────────────────
# 迁移计划模型
# ─────────────────────────────────────────────────────────────────

ActionType = Literal["delete", "rename", "merge", "retier", "reparent", "flag"]


@dataclass
class Action:
    node: str
    action: ActionType
    target: str | None = None  # rename/merge/reparent target
    new_tier: str | None = None  # for retier
    reason: str = ""
    error_types: list[str] = field(default_factory=list)


@dataclass
class MigrationPlan:
    actions: list[Action] = field(default_factory=list)
    flagged: list[tuple[str, str]] = field(default_factory=list)  # (node, reason)

    def by_action(self) -> dict[str, list[Action]]:
        out: dict[str, list[Action]] = defaultdict(list)
        for a in self.actions:
            out[a.action].append(a)
        return dict(out)


# ─────────────────────────────────────────────────────────────────
# Reason 解析 - 从errata的reasons字段提取具体action参数
# ─────────────────────────────────────────────────────────────────

# "tier continent→realm" or "tier continent→realm: 天庭是界域非大陆"
RE_TIER_CHANGE = re.compile(r"tier\s+(\w+)\s*[→]\s*(\w+)")
# "tier应为city" / "tier应为region或city" / "应为city级" / "应为region"
RE_TIER_SHOULD_BE = re.compile(r"tier[应=]为\s*(\w+)")
# More flexible: "应为city" / "应为region级" (without "tier" prefix)
RE_GENERAL_TIER = re.compile(r"应为\s*(world|realm|continent|kingdom|city|region|site|building)")
# "parent 东胜神洲→西牛贺洲"
RE_PARENT_CHANGE = re.compile(r"parent\s+([^\s→]+)\s*[→]\s*([^\s:：]+)")
# "parent应为XXX" / "parent=XXX也不对，应归X"
RE_PARENT_SHOULD_BE = re.compile(r"(?:parent[应=]为|应归入?|应归属)\s*[\"']?([^\s\"'，,。；;]+)")
# "应为XXX" or "应为'XXX'" (general, non-tier context)
RE_SHOULD_BE = re.compile(r"应为[\"'\u201c]?([^\"'\u201d，,。；;\s]+)")
# 别名合并: "同XXX" / "即XXX" / "是XXX的别名"
RE_SAME_AS = re.compile(r"(?:同|即|与)[\"']?([^\"'，,。；;\s]+)")
# "应移除" / "应删除"
RE_SHOULD_DELETE = re.compile(r"应移除|应删除|非地名|非真实地名")


def parse_correction_from_reasons(reasons: str) -> dict:
    """从reasons文本中提取修复指令."""
    out = {}
    # Tier corrections (多种格式)
    m = RE_TIER_CHANGE.search(reasons)
    if m:
        out["tier_to"] = m.group(2)
    if "tier_to" not in out:
        m = RE_TIER_SHOULD_BE.search(reasons)
        if m:
            out["tier_to"] = m.group(1)
    if "tier_to" not in out:
        m = RE_GENERAL_TIER.search(reasons)
        if m:
            out["tier_to"] = m.group(1)
    # Clean tier_to: "region或city" → take first
    if "tier_to" in out and "或" in out["tier_to"]:
        out["tier_to"] = out["tier_to"].split("或")[0]

    # Parent corrections
    m = RE_PARENT_CHANGE.search(reasons)
    if m:
        out["parent_to"] = m.group(2).split("/")[0]
    if "parent_to" not in out:
        m = RE_PARENT_SHOULD_BE.search(reasons)
        if m:
            out["parent_to"] = m.group(1)

    # Rename
    m = RE_SHOULD_BE.search(reasons)
    if m:
        val = m.group(1)
        # 排除: tier names 不算 rename
        if val not in ("world", "realm", "continent", "kingdom", "city", "region", "site", "building"):
            out["rename_to"] = val

    # Merge
    m = RE_SAME_AS.search(reasons)
    if m:
        out["merge_to"] = m.group(1)

    # Delete signal
    if RE_SHOULD_DELETE.search(reasons):
        out["should_delete"] = True
    return out


# ─────────────────────────────────────────────────────────────────
# 计划生成 - 从errata gold生成迁移计划
# ─────────────────────────────────────────────────────────────────

DELETE_CATEGORIES = {
    "A-方位泛称",
    "A-概念非地名",
    "A-描述性短语",
    "A-单字通名",
    "A-非地名",
    "B-修饰语截断",
}


def build_plan_from_gold(gold_nodes: dict) -> MigrationPlan:
    plan = MigrationPlan()

    for name, node in gold_nodes.items():
        if node["verdict"] not in ("error", "错误"):
            continue
        etypes = node["error_types"]
        reasons = node["reasons"] if isinstance(node["reasons"], str) else " ".join(node["reasons"])
        corrections = parse_correction_from_reasons(reasons)

        # 优先级: rename > merge > delete > retier > reparent > flag
        # Extra: "应移除/应删除" in reasons → delete
        if corrections.get("should_delete") and not any(
            et.startswith("B-") for et in etypes
        ):
            plan.actions.append(Action(
                node=name, action="delete", reason=reasons, error_types=etypes,
            ))
            continue

        if "B-字形错误" in etypes and "rename_to" in corrections:
            plan.actions.append(Action(
                node=name, action="rename", target=corrections["rename_to"],
                reason=reasons, error_types=etypes,
            ))
            continue

        if "B-未合并别名" in etypes and "merge_to" in corrections:
            plan.actions.append(Action(
                node=name, action="merge", target=corrections["merge_to"],
                reason=reasons, error_types=etypes,
            ))
            continue

        # 消歧重复: X·Y → 删除X·Y, 子节点挂到Y
        if "E-消歧重复" in etypes and "·" in name:
            target = name.split("·")[-1]
            plan.actions.append(Action(
                node=name, action="merge", target=target,
                reason=reasons, error_types=etypes,
            ))
            continue

        # A类 + B-修饰语截断: 直接删除
        if any(et in DELETE_CATEGORIES for et in etypes):
            plan.actions.append(Action(
                node=name, action="delete", reason=reasons, error_types=etypes,
            ))
            continue

        # C类tier修复 (需要明确target)
        if any(et.startswith("C-tier") for et in etypes) and "tier_to" in corrections:
            plan.actions.append(Action(
                node=name, action="retier", new_tier=corrections["tier_to"],
                reason=reasons, error_types=etypes,
            ))
            continue

        # D类parent修复
        if any(et.startswith("D-parent") for et in etypes) and "parent_to" in corrections:
            plan.actions.append(Action(
                node=name, action="reparent", target=corrections["parent_to"],
                reason=reasons, error_types=etypes,
            ))
            continue

        # D-孤立顶层: 默认挂到天下
        if "D-孤立顶层" in etypes:
            plan.actions.append(Action(
                node=name, action="reparent", target="天下",
                reason=reasons, error_types=etypes,
            ))
            continue

        # 其余: flag only
        plan.flagged.append((name, reasons))

    return plan


# ─────────────────────────────────────────────────────────────────
# 计划应用 - 修改world_structure JSON
# ─────────────────────────────────────────────────────────────────

# world_structure中所有"引用了地点名"的字段（需要级联更新）
REF_FIELDS = [
    "location_parents",  # both key and value
    "location_tiers",  # key
    "location_icons",  # key
    "location_layer_map",  # key
    "location_region_map",  # key
]


def _rename_in_ws(ws: dict, old: str, new: str) -> int:
    """在world_structure所有字段中将 old 重命名为 new. 返回修改点数."""
    changes = 0
    # location_parents: both key (child) and value (parent)
    lp = ws.get("location_parents", {})
    # rename child keys
    if old in lp:
        # 若 new 已存在: 保留new的parent (权威), 删除old的映射
        if new in lp:
            del lp[old]
        else:
            lp[new] = lp.pop(old)
        changes += 1
    # rename parent values
    for k, v in list(lp.items()):
        if v == old:
            lp[k] = new
            changes += 1

    # 其他dict字段: 仅key
    for field_name in ("location_tiers", "location_icons", "location_layer_map", "location_region_map"):
        d = ws.get(field_name, {})
        if old in d:
            if new not in d:
                d[new] = d[old]
            del d[old]
            changes += 1

    # type_hierarchy / cached_skeleton: leave untouched (advisory only)
    return changes


def _delete_in_ws(ws: dict, name: str) -> int:
    """删除节点, 子节点上提到grandparent. 返回修改点数."""
    changes = 0
    lp = ws.get("location_parents", {})
    # 节点的parent (即grandparent of orphans)
    grandparent = lp.get(name, "天下")
    # 子节点上提
    for k, v in list(lp.items()):
        if v == name:
            lp[k] = grandparent
            changes += 1
    # 删除自身
    if name in lp:
        del lp[name]
        changes += 1
    # 从其他dict清理
    for field_name in ("location_tiers", "location_icons", "location_layer_map", "location_region_map"):
        d = ws.get(field_name, {})
        if name in d:
            del d[name]
            changes += 1
    return changes


def _merge_in_ws(ws: dict, source: str, target: str) -> int:
    """将source合并到target: source的子节点挂到target, source删除.
    若target不存在, 降级为rename."""
    lp = ws.get("location_parents", {})
    if target not in lp and target not in lp.values():
        # target doesn't exist yet → rename
        return _rename_in_ws(ws, source, target)
    changes = 0
    # 重新挂接source的子节点到target
    for k, v in list(lp.items()):
        if v == source:
            lp[k] = target
            changes += 1
    # 删除source
    if source in lp:
        del lp[source]
        changes += 1
    for field_name in ("location_tiers", "location_icons", "location_layer_map", "location_region_map"):
        d = ws.get(field_name, {})
        if source in d:
            # target已有则保留target的值
            if target not in d:
                d[target] = d[source]
            del d[source]
            changes += 1
    return changes


def _retier_in_ws(ws: dict, name: str, new_tier: str) -> int:
    """更新tier."""
    tiers = ws.get("location_tiers", {})
    if name in tiers and tiers[name] != new_tier:
        tiers[name] = new_tier
        return 1
    elif name not in tiers:
        tiers[name] = new_tier
        return 1
    return 0


def _reparent_in_ws(ws: dict, name: str, new_parent: str) -> int:
    """重新挂接父节点."""
    lp = ws.get("location_parents", {})
    # 避免自环
    if name == new_parent:
        return 0
    if lp.get(name) == new_parent:
        return 0
    lp[name] = new_parent
    return 1


def apply_plan_to_ws(ws: dict, plan: MigrationPlan, verbose: bool = False) -> dict:
    """执行迁移计划, 返回统计信息."""
    stats = Counter()
    # 顺序: rename/merge先(会级联), 再delete/retier/reparent
    for order_key in ("rename", "merge", "delete", "retier", "reparent"):
        for a in plan.actions:
            if a.action != order_key:
                continue
            if a.action == "rename":
                c = _rename_in_ws(ws, a.node, a.target)
                if verbose:
                    print(f"  [rename] {a.node} → {a.target} ({c} edits)")
            elif a.action == "merge":
                c = _merge_in_ws(ws, a.node, a.target)
                if verbose:
                    print(f"  [merge]  {a.node} → {a.target} ({c} edits)")
            elif a.action == "delete":
                c = _delete_in_ws(ws, a.node)
                if verbose:
                    print(f"  [delete] {a.node} ({c} edits)")
            elif a.action == "retier":
                c = _retier_in_ws(ws, a.node, a.new_tier)
                if verbose:
                    print(f"  [retier] {a.node} → tier={a.new_tier} ({c} edits)")
            elif a.action == "reparent":
                c = _reparent_in_ws(ws, a.node, a.target)
                if verbose:
                    print(f"  [reparent] {a.node} → parent={a.target} ({c} edits)")
            stats[a.action] += 1
            stats["total_edits"] += c
    return dict(stats)


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", required=True, help="novel key (e.g. xiyouji)")
    ap.add_argument("--apply", action="store_true", help="actually write DB (default: dry-run)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--db", help="DB path (default: ~/.ai-reader-v2/data.db)")
    args = ap.parse_args()

    novel_id = NOVEL_ID_MAP.get(args.novel)
    if not novel_id:
        print(f"[error] Unknown novel: {args.novel}", file=sys.stderr)
        return 1

    db_path = Path(args.db) if args.db else DB_PATH
    gold_path = KB_DIR / f"{args.novel}_errata_gold.json"
    if not gold_path.exists():
        print(f"[error] Gold not found: {gold_path}", file=sys.stderr)
        return 1

    # Load gold
    with gold_path.open(encoding="utf-8") as f:
        gold = json.load(f)
    print(f"[info] Loaded gold: {len(gold['nodes'])} nodes, "
          f"{gold['verdict_counts']['错误']} errors, {gold['verdict_counts']['可疑']} suspects")

    # Build plan
    plan = build_plan_from_gold(gold["nodes"])
    by_action = plan.by_action()
    print(f"\n[plan] Generated {len(plan.actions)} actions + {len(plan.flagged)} flagged:")
    for act, items in sorted(by_action.items()):
        print(f"  {act}: {len(items)}")
    print(f"  (flagged, no auto-action): {len(plan.flagged)}")

    if args.verbose:
        print("\n[plan detail]")
        for a in plan.actions:
            tgt = f" → {a.target}" if a.target else ""
            tier = f" tier={a.new_tier}" if a.new_tier else ""
            print(f"  [{a.action:8}] {a.node}{tgt}{tier}  [{','.join(a.error_types)}]")
        if plan.flagged:
            print("\n[flagged]")
            for n, r in plan.flagged[:20]:
                print(f"  {n}: {r[:80]}")

    if not args.apply:
        print("\n[dry-run] No changes applied. Use --apply to execute.")
        return 0

    # Load WS from DB
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,))
    row = cur.fetchone()
    if not row:
        print(f"[error] No world_structure for {novel_id}", file=sys.stderr)
        return 1
    ws = json.loads(row[0])

    # Backup
    backup_dir = KB_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"{args.novel}-ws-{ts}.json"
    with backup_path.open("w", encoding="utf-8") as f:
        json.dump(ws, f, ensure_ascii=False, indent=2)
    print(f"\n[info] Backup saved: {backup_path}")

    # Apply
    print(f"\n[apply] Executing plan...")
    stats = apply_plan_to_ws(ws, plan, verbose=args.verbose)
    print(f"\n[result] {dict(stats)}")

    # Write back
    ws["_migration_metadata"] = {
        "migrated_at": datetime.now().isoformat(),
        "script": "migrate_hierarchy_from_errata.py",
        "gold_source": str(gold_path.name),
        "backup": str(backup_path.name),
        "stats": dict(stats),
    }
    cur.execute(
        "UPDATE world_structures SET structure_json=?, updated_at=datetime('now') WHERE novel_id=?",
        (json.dumps(ws, ensure_ascii=False), novel_id),
    )
    conn.commit()
    conn.close()
    print(f"[info] world_structure updated in DB")
    print(f"\n[next] Run benchmark to verify: "
          f"uv run python scripts/benchmark_hierarchy.py --novel={args.novel} --save-history")

    return 0


if __name__ == "__main__":
    sys.exit(main())
