"""一次性迁移:为存量 world_structures 回填 virtual_locations(2026-09-19)。

背景:virtual_locations(语义根/工程根分离,王贺 10.2)随 Phase 1 落地,
但只在新重建时填充。本脚本为存量小说回填**保守子集**(干跑曾发现激进
规则误伤:基地系列的 川陀/端点星 因 LLM tier 误判为 world 被波及;
凡人修仙传的 仙界/灵界 是真实宇宙观节点;西游 幽冥界 是 gold 真实根):
  1. 「主世界」:overworld 译名,任何小说中都是工程脚手架 → 标记
  2. uber_root:仅当全书恰好一个 tier=world 节点、且其名为 天下/主世界/世界、
     且小说不在 REAL_TIANXIA_TITLES(水浒/三国/封神) → 标记
其余(图层名同名节点、多 world 节点的混乱数据)留给下次重建时由
_inject_layer_roots 在全量上下文中精确标记。

幂等:重复运行结果相同(集合语义)。

用法:
    cd backend && .venv/bin/python scripts/mark_virtual_locations.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 迁移目标是真实库,不能用 scratch
from src.infra.config import DB_PATH  # noqa: E402


async def main(dry_run: bool) -> None:
    import aiosqlite

    from src.models.world_structure import WorldStructure
    from src.services.geo_skills.orchestrator import is_real_tianxia_novel

    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    try:
        rows = await (await conn.execute(
            "SELECT w.novel_id, w.structure_json, n.title "
            "FROM world_structures w JOIN novels n ON n.id = w.novel_id"
        )).fetchall()

        changed = 0
        for row in rows:
            ws = WorldStructure.model_validate(json.loads(row["structure_json"]))
            before = set(ws.virtual_locations)

            # 1) 「主世界」永远是工程脚手架(overworld 译名)
            nodes = set(ws.location_tiers) | set(ws.location_parents)
            if "主世界" in nodes:
                ws.virtual_locations.add("主世界")

            # 2) uber_root:恰一个 tier=world 节点且名为通用容器名、
            #    且小说无文本真实「天下」概念时才虚拟化(保守子集)
            world_nodes = [n for n, t in ws.location_tiers.items() if t == "world"]
            if (
                not is_real_tianxia_novel(row["title"] or "")
                and len(world_nodes) == 1
                and world_nodes[0] in ("天下", "主世界", "世界")
            ):
                ws.virtual_locations.add(world_nodes[0])

            if ws.virtual_locations != before:
                changed += 1
                print(
                    f"[{row['title']}] virtual: {sorted(ws.virtual_locations)}"
                )
                if not dry_run:
                    await conn.execute(
                        "UPDATE world_structures SET structure_json=?, "
                        "updated_at=datetime('now') WHERE novel_id=?",
                        (ws.model_dump_json(), row["novel_id"]),
                    )
        if not dry_run:
            await conn.commit()
        print(f"{'[dry-run] ' if dry_run else ''}updated {changed}/{len(rows)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
