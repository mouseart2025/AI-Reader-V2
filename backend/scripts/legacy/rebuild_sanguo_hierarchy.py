#!/usr/bin/env python
"""Story 5.5 — 三国 geo 链离线重建 + 重建前后对比导出。

对三国副本跑 ``build_default_orchestrator`` + ``apply_to_world_structure``,
同步刷新 world_structures 的 location_parents 与 location_tiers,
并导出重建前后的层级快照供验收对比。

为何可完全离线(关键事实,勿当理所当然):
- KnowledgePrior 对标题含「三国」的小说走硬编码 ``_SANGUO_PRIORS``,
  ``requires_llm=False``,不触发 ``_llm_priors``(仅未知小说才 fallback 到 LLM)。
- VoteBuilder 从 chapter_facts(SQLite)读票,不依赖 chroma。
- SnapshotStore 写入同一 SQLite 的 hierarchy_snapshots 表,无额外文件目录。

用法:
    AI_READER_DATA_DIR=/tmp/sanguo-rebuild uv run python scripts/rebuild_sanguo_hierarchy.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

# ── 必须在 import src 之前设置 ──────────────────────────────────────────
os.environ.setdefault("AI_READER_DATA_DIR", "/tmp/sanguo-rebuild")

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from src.infra.config import DB_PATH  # noqa: E402

NOVEL_ID = "b1287ef6-c215-4bd2-842c-cb04aec5eb70"   # 三国演义(120 章)
NOVEL_TITLE = "三国演义"
OUT_DIR = _BACKEND / "audit_reports"


def read_state(db_path: Path) -> dict:
    """从 world_structures 读出 location_parents / location_tiers。"""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT structure_json, updated_at FROM world_structures WHERE novel_id=?",
            (NOVEL_ID,),
        ).fetchone()
        if not row:
            raise SystemExit(f"world_structures 无此小说: {NOVEL_ID}")
        payload = json.loads(row["structure_json"])
        return {
            "updated_at": row["updated_at"],
            "location_parents": payload.get("location_parents", {}),
            "location_tiers": payload.get("location_tiers", {}),
        }
    finally:
        con.close()


async def rebuild() -> dict:
    """跑 geo 管线并应用回 world_structure。"""
    from src.services.geo_skills.orchestrator import build_default_orchestrator

    orch = build_default_orchestrator(NOVEL_ID, novel_title=NOVEL_TITLE)
    async for event in orch.run():
        print(f"  [{event.stage}] {event.message}")
    return await orch.apply_to_world_structure()


def diff(before: dict, after: dict) -> dict:
    b, a = before["location_parents"], after["location_parents"]
    changed = {
        k: {"from": b.get(k), "to": a.get(k)}
        for k in set(b) | set(a)
        if b.get(k) != a.get(k)
    }
    bt, at = before["location_tiers"], after["location_tiers"]
    tier_changed = {
        k: {"from": bt.get(k), "to": at.get(k)}
        for k in set(bt) | set(at)
        if bt.get(k) != at.get(k)
    }
    return {"parent_changed": changed, "tier_changed": tier_changed}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"DB: {DB_PATH}")

    before = read_state(DB_PATH)
    print(
        f"重建前: parents={len(before['location_parents'])} "
        f"tiers={len(before['location_tiers'])} updated_at={before['updated_at']}"
    )

    print("\n=== geo 管线 ===")
    result = asyncio.run(rebuild())
    print(f"\napply_to_world_structure: {result}")

    after = read_state(DB_PATH)
    print(
        f"\n重建后: parents={len(after['location_parents'])} "
        f"tiers={len(after['location_tiers'])} updated_at={after['updated_at']}"
    )

    d = diff(before, after)
    print(f"parent 变化: {len(d['parent_changed'])} 条")
    print(f"tier   变化: {len(d['tier_changed'])} 条")

    stamp = os.environ.get("STAMP", "20260908_sanguo_full120")
    (OUT_DIR / f"hierarchy_before_{stamp}.json").write_text(
        json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / f"hierarchy_after_{stamp}.json").write_text(
        json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / f"hierarchy_diff_{stamp}.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已导出 -> {OUT_DIR}/hierarchy_{{before,after,diff}}_{stamp}.json")


if __name__ == "__main__":
    main()
