#!/usr/bin/env python
"""Story 5.5 人工抽查表生成 —— 分层抽样 180 条层级边。

验收要求(epics-quality-hardening-v076.md Story 5.5):
  人工抽查 180 条层级边(分层抽样:核心/常规/微观各 1/3),记录错边数与类型,
  抽查表落 backend/audit_reports/,错边 ≤2/180。

分层口径(按 location_tiers):
  核心 = world / continent / kingdom / region
  常规 = city
  微观 = site / building
每层内按 child 名稳定排序后等距抽样,保证可复现。

用法:
    AI_READER_DATA_DIR=~/.ai-reader-v2-rerun-20260906 \
    AI_READER_FORCE_DB_KEY=1 uv run python scripts/story_5_5_sample_180.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from src.infra.config import DB_PATH  # noqa: E402

OUT_DIR = _BACKEND / "audit_reports"

BOOKS: dict[str, tuple[str, str]] = {
    "sanguo": ("b1287ef6-c215-4bd2-842c-cb04aec5eb70", "三国演义"),
    "shuihu": ("4ac43c73-f67b-427c-8d6d-e766a1423977", "水浒传"),
    "honglou": ("c384901a-8b71-437a-af35-b5ec1c56c696", "红楼梦"),
    "xiyouji": ("3b2ef56c-1a55-466a-a7d1-34272446a198", "西游记"),
    "fengshen": ("53013970-effd-4f50-aef7-728ca13de69a", "封神演义"),
}

CORE = {"world", "continent", "kingdom", "region"}
MICRO = {"site", "building"}
PER_STRATUM = 60


def read_state(novel_id: str) -> tuple[dict, dict]:
    con = sqlite3.connect(str(DB_PATH))
    try:
        row = con.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?",
            (novel_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"world_structures 无此小说: {novel_id}")
        p = json.loads(row[0])
        return p.get("location_parents", {}), p.get("location_tiers", {})
    finally:
        con.close()


def stratified_sample(parents: dict, tiers: dict, n: int = PER_STRATUM) -> list[dict]:
    buckets: dict[str, list[tuple[str, str]]] = {"核心": [], "常规": [], "微观": []}
    for child, parent in sorted(parents.items()):
        t = tiers.get(child, "")
        if t in CORE:
            buckets["核心"].append((child, parent))
        elif t in MICRO:
            buckets["微观"].append((child, parent))
        else:
            buckets["常规"].append((child, parent))

    out: list[dict] = []
    for stratum, items in buckets.items():
        if not items:
            continue
        if len(items) <= n:
            picked = items
        else:
            step = len(items) / n
            picked = [items[int(i * step)] for i in range(n)]
        for child, parent in picked:
            out.append({
                "stratum": stratum,
                "child": child,
                "parent": parent,
                "child_tier": tiers.get(child, ""),
                "judgment": "",      # 人工填: 对 / 错
                "error_type": "",    # 人工填: 反向 / 跨层 / 无关 / 通名
                "note": "",
            })
    return out


def main() -> None:
    stamp = os.environ.get("STAMP", datetime.now(timezone.utc).strftime("%Y%m%d"))
    all_rows: list[dict] = []
    summary = []
    for slug, (nid, title) in BOOKS.items():
        parents, tiers = read_state(nid)
        rows = stratified_sample(parents, tiers)
        for i, r in enumerate(rows, 1):
            r["novel"] = slug
            r["novel_title"] = title
            r["seq"] = i
        all_rows.extend(rows)
        cnt: dict[str, int] = {}
        for r in rows:
            cnt[r["stratum"]] = cnt.get(r["stratum"], 0) + 1
        summary.append((slug, title, len(parents), cnt))
        print(f"{slug:10} 边 {len(parents):>5}  抽样 {len(rows):>3}  {cnt}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jf = OUT_DIR / f"story_5_5_sample_180_{stamp}.json"
    jf.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2),
                  encoding="utf-8")

    lines = [
        "# Story 5.5 人工抽查表（180 条分层抽样）",
        "",
        f"- 生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- 数据库: `{DB_PATH}`",
        "- 分层口径: 核心(world/continent/kingdom/region) / 常规(city) / "
        "微观(site/building)，每层目标 60 条",
        "- 判定标准: `judgment` 填 对/错；错的填 `error_type`"
        "（反向 / 跨层 / 无关 / 通名）",
        "- 验收线: 错边 ≤ 2/180",
        "",
        "| 书 | 总边数 | 抽样 | 核心 | 常规 | 微观 |",
        "|---|---|---|---|---|---|",
    ]
    for _slug, title, total, cnt in summary:
        lines.append(
            f"| {title} | {total} | {sum(cnt.values())} | "
            f"{cnt.get('核心', 0)} | {cnt.get('常规', 0)} | {cnt.get('微观', 0)} |"
        )
    lines += ["", "## 抽样明细", "",
              "| # | 书 | 层级 | 子地点 | 父地点 | 子 tier | 判定 | 错误类型 | 备注 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in all_rows:
        lines.append(
            f"| {r['seq']} | {r['novel_title']} | {r['stratum']} | {r['child']} | "
            f"{r['parent']} | {r['child_tier']} | {r['judgment']} | "
            f"{r['error_type']} | {r['note']} |"
        )
    mf = OUT_DIR / f"story_5_5_sample_180_{stamp}.md"
    mf.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n总抽样 {len(all_rows)} 条")
    print(f"已导出 -> {jf.name}")
    print(f"已导出 -> {mf.name}")


if __name__ == "__main__":
    main()
