"""GeoEvolve 阶段 5 决策 1 —— 词表 delta 程序化抽检（并入前审查）。

不重评估指标，只审 142 条 delta 条目本身：
  1. 坐标合法性（lat ∈ [-90,90]，lng ∈ [-180,180]）
  2. 与所附祖先的当前解析坐标距离（haversine；条目坐标=提议时祖先坐标，
     理论距离 0；>1000km 列出——说明祖先解析漂移或条目错挂）
  3. 频次分布（chapter_facts 出现章数）
  4. 名称 sanity：fact_validator 泛称命中（Curator 应已拦截，复核）、
     含标点/数字/拉丁字母、超长（>10 字）
  5. 随机抽 15 条明细（seed 42 固定，可复现）

输出：out/vocab_audit.json + stdout 摘要（markdown 明细表由报告引用）。

Usage:
    cd backend && .venv/bin/python scripts/evolve/audit_vocab_delta.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_PATH = _EVOLVE_DIR / "out" / "vocab_audit.json"


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math

    lat1, lon1, lat2, lon2 = (math.radians(x) for x in (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def audit() -> dict:
    import geo_vocab as gv
    from src.extraction.fact_validator import _is_generic_location
    from src.services.geo_resolver import GeoResolver

    store = gv.load_delta()
    entries = store["entries"]
    resolver = GeoResolver(dataset_key="cn")
    resolver._load_index()

    issues: list[dict] = []
    distances = []
    freqs = []
    rows = []
    for name, e in sorted(entries.items()):
        lat, lng = e["coords"]
        row = {"name": name, "coords": [lat, lng], "ancestor": e.get("ancestor"),
               "novel": e.get("novel"), "frequency": e.get("frequency", 0),
               "generation": e.get("generation")}
        # 1. 坐标合法性
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            row["issue"] = "坐标越界"
            issues.append(row)
        # 2. 与祖先当前解析的距离
        anc = e.get("ancestor")
        dist = None
        if anc:
            anc_res = resolver.resolve_names([anc], None)
            if anc in anc_res:
                dist = _haversine_km((lat, lng), anc_res[anc])
                distances.append(dist)
                if dist > 1000:
                    row["issue"] = f"距祖先 {dist:.0f}km"
                    issues.append(row)
            else:
                row["issue"] = f"祖先 {anc} 当前不可解析"
                issues.append(row)
        row["dist_km"] = round(dist, 1) if dist is not None else None
        # 3. 频次
        freqs.append(e.get("frequency", 0))
        # 4. 名称 sanity
        if _is_generic_location(name) is not None:
            row["issue"] = "泛称命中(Curator 漏网)"
            issues.append(row)
        if len(name) > 10 or any(ch in name for ch in "，。！？、,.!?0123456789"):
            row["issue"] = "名称形态可疑"
            issues.append(row)
        rows.append(row)

    sample = random.Random(42).sample(rows, min(15, len(rows)))
    dist_sorted = sorted(distances)
    report = {
        "total_entries": len(entries),
        "blacklist": len(store.get("rejected", {})),
        "issues": issues,
        "distance_km": {
            "n": len(distances),
            "max": round(dist_sorted[-1], 1) if dist_sorted else None,
            "p50": round(dist_sorted[len(dist_sorted) // 2], 1) if dist_sorted else None,
            "zero_pct": round(sum(1 for d in distances if d < 0.1)
                              / max(len(distances), 1), 4),
        },
        "frequency": {"min": min(freqs), "max": max(freqs),
                      "mean": round(sum(freqs) / len(freqs), 1)},
        "sample15": sample,
        "verdict": "抽检通过" if not issues else f"发现 {len(issues)} 条异常",
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return report


def main() -> int:
    r = audit()
    print(f"[audit] 条目 {r['total_entries']} 黑名单 {r['blacklist']} | "
          f"距离 p50={r['distance_km']['p50']}km max={r['distance_km']['max']}km "
          f"零距离占比={r['distance_km']['zero_pct']} | "
          f"频次 {r['frequency']['min']}-{r['frequency']['max']} "
          f"均值 {r['frequency']['mean']} | 结论: {r['verdict']}")
    if r["issues"]:
        for i in r["issues"][:10]:
            print(f"  ⚠️ {i['name']}: {i['issue']}")
    print("\n随机 15 条明细(seed 42):")
    print("| 名称 | 坐标 | 祖先 | 距祖先km | 频次 | 代 |")
    print("|---|---|---|---|---|---|")
    for s in r["sample15"]:
        print(f"| {s['name']} | ({s['coords'][0]},{s['coords'][1]}) | "
              f"{s['ancestor']} | {s['dist_km']} | {s['frequency']} | "
              f"gen{s['generation']} |")
    print(f"\n[audit] 已写 {OUT_PATH}")
    return 0 if not r["issues"] else 1


if __name__ == "__main__":
    sys.exit(main())
