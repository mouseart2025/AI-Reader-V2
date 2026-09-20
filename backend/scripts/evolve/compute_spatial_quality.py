"""GeoEvolve —— 五本小说空间质量指标离线汇总(纯计算,零 LLM 调用)。

对每本小说:
  1. 从冻结 DB 只读 chapter_facts,算重访一致性(compute_revisit_consistency);
  2. 加载最新层级快照(hierarchy_snapshots,缺失时回退 world_structures),
     跑结构约束(check_spatial_constraints,含 virtual_roots 豁免);
  3. 有金标 fixture 的小说附 per-level 分层精度(compute_per_level_metrics)。

单本读取失败记 error 字段继续,不整脚本崩。
输出(stdout 最后一行 JSON):
  {"xiyouji": {"revisit": {...}, "constraints": {...}, "per_level": {...}}, ...}

Usage:
    cd backend && .venv/bin/python scripts/evolve/compute_spatial_quality.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_FIXTURES_DIR = _BACKEND_DIR / "tests" / "fixtures"
_GOLDEN_BY_SLUG = {
    "xiyouji": "golden_standard_journey_to_west.json",
    "honglou": "golden_standard_dream_of_red_chamber.json",
    "shuihu": "golden_standard_water_margin.json",
}


def _resolve_novel_id(conn: sqlite3.Connection, title: str,
                      fixed_id: str | None) -> str:
    if fixed_id:
        return fixed_id
    rows = conn.execute("SELECT id FROM novels WHERE title=?", (title,)).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"title={title!r} 匹配 {len(rows)} 行,无法唯一解析")
    return rows[0][0]


def _load_chapter_facts(conn: sqlite3.Connection, novel_id: str) -> list[dict]:
    facts = []
    for (text,) in conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
        (novel_id,),
    ):
        try:
            facts.append(json.loads(text))
        except Exception:
            continue
    return facts


def _load_hierarchy(conn: sqlite3.Connection, novel_id: str,
                    ) -> tuple[dict, dict, set[str]]:
    """最新层级快照 (parents, tiers, virtual_roots)。

    快照缺失/表不存在时回退 world_structures.structure_json;
    virtual_roots 始终取 world_structures.virtual_locations(工程根豁免集)。
    """
    parents: dict = {}
    tiers: dict = {}
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM hierarchy_snapshots "
            "WHERE novel_id=? ORDER BY version DESC LIMIT 1",
            (novel_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        snap = json.loads(row[0])
        parents = snap.get("location_parents") or {}
        tiers = snap.get("location_tiers") or {}

    virtual: set[str] = set()
    ws_row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?",
        (novel_id,),
    ).fetchone()
    if ws_row:
        ws = json.loads(ws_row[0])
        virtual = set(ws.get("virtual_locations") or [])
        if not parents:
            parents = ws.get("location_parents") or {}
            tiers = ws.get("location_tiers") or {}
    if not parents:
        raise RuntimeError("无可用层级快照(快照表与 world_structures 均为空)")
    return parents, tiers, virtual


def _load_golden(slug: str) -> list[dict] | None:
    filename = _GOLDEN_BY_SLUG.get(slug)
    if not filename:
        return None
    path = _FIXTURES_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["locations"]


def _compute_one(conn: sqlite3.Connection, slug: str, title: str,
                 fixed_id: str | None) -> dict:
    from src.utils.spatial_quality import (
        check_spatial_constraints,
        compute_per_level_metrics,
        compute_revisit_consistency,
    )

    novel_id = _resolve_novel_id(conn, title, fixed_id)
    entry: dict = {"novel_id": novel_id}

    facts = _load_chapter_facts(conn, novel_id)
    revisit = compute_revisit_consistency(facts)
    entry["revisit"] = {
        "chapters": len(facts),
        "parent_conflicts": revisit["parent_conflicts"],
        "children_multi_asserted": revisit["children_multi_asserted"],
        "parent_consistency": revisit["parent_consistency"],
        "direction_conflicts": revisit["direction_conflicts"],
        "cases_count": len(revisit["cases"]),
    }

    parents, tiers, virtual = _load_hierarchy(conn, novel_id)
    violations = check_spatial_constraints(
        parents, location_tiers=tiers, virtual_roots=virtual)
    by_code = Counter(v["code"] for v in violations)
    entry["constraints"] = {
        "total": len(violations),
        "errors": sum(1 for v in violations if v["severity"] == "error"),
        "warnings": sum(1 for v in violations if v["severity"] == "warning"),
        "by_code": dict(sorted(by_code.items())),
    }

    golden = _load_golden(slug)
    if golden:
        per_level = compute_per_level_metrics(parents, golden)
        entry["per_level"] = {
            "macro_precision": per_level["macro_precision"],
            "levels": per_level["levels"],
        }
    return entry


def main() -> int:
    from geo_vocab import NOVELS, REAL_DB, open_db_readonly

    if not REAL_DB.exists():
        print(json.dumps({"error": f"db not found: {REAL_DB}"},
                         ensure_ascii=False))
        return 1

    conn = open_db_readonly()
    out: dict = {}
    try:
        for slug, title, fixed_id in NOVELS:
            try:
                out[slug] = _compute_one(conn, slug, title, fixed_id)
            except Exception as exc:  # 单本失败不拖垮整脚本
                out[slug] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
