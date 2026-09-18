"""GeoEvolve 阶段 1 —— 每代 EVAL 子进程：重算五本 geo.unresolved_rate。

为什么在子进程：APPLY 阶段会把候选 delta 写进 geo_resolver.py 源文件，
子进程 fresh import 自然加载候选状态；主进程内存中的 geo_resolver 保持
pristine（不 reload），提议器状态由 vocab_delta.json 推导，两侧不串。

输出（stdout 最后一行 JSON）：
  {"xiyouji": {"names": N, "resolved": M, "unresolved_rate": r}, ...}

Usage:
    cd backend && .venv/bin/python scripts/evolve/compute_geo_metrics.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    from geo_vocab import NOVELS, compute_novel_geo, open_db_readonly, resolve_novel_ids

    from src.services.geo_resolver import GeoResolver

    resolver = GeoResolver(dataset_key="cn")
    resolver._load_index()  # 一次性 ~5s；TSV 已本地缓存，无网络

    conn = open_db_readonly()
    try:
        ids = resolve_novel_ids(conn)
        out = {}
        for slug, _title, _ in NOVELS:
            snap = compute_novel_geo(conn, ids[slug], resolver)
            out[slug] = {
                "names": snap["names"],
                "resolved": snap["resolved"],
                "unresolved_rate": snap["unresolved_rate"],
            }
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
