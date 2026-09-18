"""GeoEvolve R2 —— OOD 护栏集（跨体裁）基线测量 + baseline.json 写入。

背景：进化选择集（5 本古典章回小说）是单一分布，存在跨体裁过拟合风险
（用户提出，2026-09-18）。OOD 护栏集 = 凡人修仙传（修仙网文）/ 魔戒全集
（西方奇幻译本）/ 平凡的世界（现代现实主义），纯规则指标三件套：
  M1 层级健康（orphan_rate/roots/max_children，quality_dashboard.compute_m1）
  M4 泛称残留（compute_m4 + fact_validator._is_generic_location）
  geo.unresolved_rate（GeoResolver 生产函数，cn 数据集）

写入 baseline.json 的 "ood_guard" 键（不覆盖既有 inner/holdout 结构）。
变异门禁对照此基线（eval_policy v4 ood_guard 节）。

Usage:
    cd backend && .venv/bin/python scripts/evolve/build_ood_baseline.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import geo_vocab as gv  # noqa: E402
import quality_dashboard as qd  # noqa: E402

# slug → (标题, 体裁注记)。id 按标题从 novels 表解析（防重复行,核对唯一）
OOD_NOVELS: dict[str, dict] = {
    "fanren": {"title_like": "凡人修仙传", "genre_note": "修仙网文(2451 章)"},
    "motrings": {"title_like": "魔戒全集", "genre_note": "西方奇幻译本(82 章)"},
    "pingfan": {"title_like": "平凡的世界", "genre_note": "现代现实主义(171 章)"},
}


def compute_ood_metrics(resolver=None) -> dict[str, dict]:
    """三本 OOD 小说的纯规则指标。供基线与门禁共用（单一实现）。"""
    from src.extraction.fact_validator import _is_generic_location
    from src.services.geo_resolver import GeoResolver

    if resolver is None:
        resolver = GeoResolver(dataset_key="cn")
        resolver._load_index()
    conn = gv.open_db_readonly()
    try:
        out: dict[str, dict] = {}
        for slug, info in OOD_NOVELS.items():
            rows = conn.execute("SELECT id, title FROM novels WHERE title LIKE ?",
                                (f"%{info['title_like']}%",)).fetchall()
            if len(rows) != 1:
                raise RuntimeError(f"{info['title_like']} 匹配 {len(rows)} 行")
            nid, title = rows[0]
            ws = json.loads(conn.execute(
                "SELECT structure_json FROM world_structures WHERE novel_id=?",
                (nid,)).fetchone()[0])
            lp = ws.get("location_parents") or {}
            genre = ws.get("novel_genre_hint")
            uber = qd.find_uber_root(lp) if lp else None
            evidence_names: set[str] = set()
            for (fj,) in conn.execute(
                    "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (nid,)):
                try:
                    fact = json.loads(fj)
                except Exception:
                    continue
                for loc in fact.get("locations") or []:
                    for k in ("name", "parent"):
                        v = (loc.get(k) or "").strip()
                        if v:
                            evidence_names.add(v)
                for sr in fact.get("spatial_relationships") or []:
                    for k in ("source", "target"):
                        v = (sr.get(k) or "").strip()
                        if v:
                            evidence_names.add(v)
            m1 = qd.compute_m1(lp, uber, evidence_names)
            universe = set(lp) | {p for p in lp.values() if p}
            m4 = qd.compute_m4(universe, lambda n, g=genre: _is_generic_location(n, g))
            names = sorted(universe)
            resolved = resolver.resolve_names(names, lp) if names else {}
            unresolved_rate = 1 - len(resolved) / len(names) if names else None
            out[slug] = {
                "title": title, "genre_note": info["genre_note"],
                "genre": genre, "nodes": len(universe),
                "m1.orphan_rate": m1["orphan_rate"],
                "m1.roots": m1["roots"],
                "m1.max_children": m1["max_children"],
                "m4.generic_residue": m4["generic_residue"],
                "geo.unresolved_rate": unresolved_rate,
            }
            print(f"[ood] {slug}({title}): nodes={len(universe)} "
                  f"orphan={m1['orphan_rate']:.3f} generic={m4['generic_residue']:.3f} "
                  f"unresolved={unresolved_rate:.3f}")
        return out
    finally:
        conn.close()


def main() -> int:
    metrics = compute_ood_metrics()
    baseline_path = _EVOLVE_DIR / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["ood_guard"] = {
        "note": "OOD 跨体裁护栏基线(单一古典分布过拟合修正,用户提出 2026-09-18);"
                "级 3/4 变异门禁对照此值,级 1 豁免(保留抽检),级 2 沿用分小说记账",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "novels": metrics,
    }
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"[ood] 已写入 {baseline_path} 的 ood_guard 节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
