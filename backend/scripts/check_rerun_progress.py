#!/usr/bin/env python3
"""Check demo 五本重跑进度(直连 SQLite)。

分析任务会阻塞 uvicorn 事件循环 → HTTP 轮询会超时返回空,必须直连库。

Usage:
    python scripts/check_rerun_progress.py
    python scripts/check_rerun_progress.py --data-dir ~/.ai-reader-v2-rerun-20260906
    python scripts/check_rerun_progress.py --model qwen-plus --watch
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

NOVELS = {
    "sanguo": "b1287ef6-c215-4bd2-842c-cb04aec5eb70",
    "shuihu": "4ac43c73-f67b-427c-8d6d-e766a1423977",
    "honglou": "c384901a-8b71-437a-af35-b5ec1c56c696",
    "xiyouji": "3b2ef56c-1a55-466a-a7d1-34272446a198",
    "fengshen": "53013970-effd-4f50-aef7-728ca13de69a",
}


def snapshot(conn: sqlite3.Connection, model: str, slug: str, nid: str) -> dict:
    t = conn.execute(
        "SELECT status, current_chapter, chapter_end, updated_at FROM analysis_tasks "
        "WHERE novel_id=? ORDER BY created_at DESC LIMIT 1", (nid,)
    ).fetchone()
    done = conn.execute(
        "SELECT COUNT(*) FROM chapter_facts WHERE novel_id=? AND llm_model=?",
        (nid, model),
    ).fetchone()[0]
    out_trunc = conn.execute(
        "SELECT COUNT(*) FROM chapter_facts WHERE novel_id=? AND llm_model=? "
        "AND output_truncated=1", (nid, model),
    ).fetchone()[0]
    # 抽取质量:已完成章的平均地点数 / 带 parent 比例
    rows = conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? AND llm_model=?",
        (nid, model),
    ).fetchall()
    n_loc = n_parent = 0
    for (fj,) in rows:
        try:
            locs = json.loads(fj).get("locations") or []
        except (ValueError, TypeError):
            continue
        n_loc += len(locs)
        n_parent += sum(1 for l in locs if isinstance(l, dict) and l.get("parent"))
    return {
        "slug": slug,
        "status": t[0] if t else "-",
        "progress": f"{t[1]}/{t[2]}" if t else "-",
        "done": done,
        "out_trunc": out_trunc,
        "avg_loc": round(n_loc / len(rows), 1) if rows else 0.0,
        "parent_pct": round(100 * n_parent / n_loc) if n_loc else 0,
        "updated": t[3] if t else "-",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir",
                    default=os.environ.get("AI_READER_DATA_DIR",
                                           "~/.ai-reader-v2-rerun-20260906"))
    ap.add_argument("--model", default="qwen-plus")
    ap.add_argument("--watch", action="store_true", help="每 60s 刷新")
    args = ap.parse_args()

    db = os.path.join(os.path.expanduser(args.data_dir), "data.db")
    if not os.path.exists(db):
        print(f"❌ 找不到数据库: {db}", file=sys.stderr)
        sys.exit(1)

    while True:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = [snapshot(conn, args.model, s, n) for s, n in NOVELS.items()]
        conn.close()

        # ⚠️ 库里 datetime('now') 存的是 UTC,不能用本地时间比,否则凭空多 8h
        print(f"\n[{time.strftime('%H:%M:%S')} 本地 / "
              f"{time.strftime('%H:%M:%S', time.gmtime())} UTC] "
              f"model={args.model}")
        print(f"{'本':<10}{'状态':<11}{'进度':<10}{'已完成':<8}{'截断':<7}{'均地点':<8}{'带parent%':<9}")
        print("-" * 66)
        for r in rows:
            flag = " ⚠️" if r["out_trunc"] else ""
            print(f"{r['slug']:<10}{r['status']:<11}{r['progress']:<10}{r['done']:<8}"
                  f"{r['out_trunc']:<7}{r['avg_loc']:<8}{r['parent_pct']:<9}{flag}")
        total = sum(r["done"] for r in rows)
        print(f"\n合计已完成 {total} 章,截断 {sum(r['out_trunc'] for r in rows)} 章")

        if all(r["status"] in ("completed", "completed_with_errors") for r in rows):
            print("\n🎉 五本全部完成")
            break
        if not args.watch:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
