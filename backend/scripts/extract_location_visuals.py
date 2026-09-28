"""从地点描述抽取 terrain/landmark，并对照现有的「名字后缀 + type/icon」判定。

    python scripts/extract_location_visuals.py                          # 全库地点画像（不跑 LLM）
    python scripts/extract_location_visuals.py --novel 西游记 --limit 120 --save
    python scripts/extract_location_visuals.py --novel 西游记 --all --save --model-label qwen2.5:7b

不做 `--dry` 之类的开关：默认就**不写库**，只有 `--save` 才落盘。

## 为什么这个对照脚本不会说谎

它用的分类器是 `services/location_influence.influence_classes` —— 与
`generate_terrain` 烘焙时**同一个函数**（`map_layout_service` 直接 import 它）。
脚本若自己重写一遍判定，就会犯「量具复刻被测逻辑」：改了烘焙那边，这边照旧
报旧答案，且没有任何报错。这一条是本项目踩过的坑，写在脚本里当护栏。

## 四象限的含义

只看 山/水/林 三类（地形场**目前**只有这三条影响力通道）：

| | 含义 |
|---|---|
| both | 两边一致 —— 描述没带来新信息 |
| only-rule | 规则说是、描述没说。**不是错误率**：实测样本里多数是规则判对而描述不含地貌信息 |
| only-desc | 描述说是、规则没说 → **描述带来的召回增量**（决定值不值得接线） |
| neither | 有描述但不含地貌信息（实测占多数 —— 描述中位只有 11–22 字） |

新增类别（coastal/plain/urban/celestial/underworld/underground）单独统计：
它们现有规则**完全没有通道**，所以每一个都是纯增量，但接不接是设计问题
（celestial/underworld 属于别的图层，喂进地理地形场是错的）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.location_influence import influence_classes

# terrain 值 → 现有的三条影响力通道。只有这三类能直接进 `generate_terrain`。
TERRAIN_TO_CHANNEL = {"mountain": "mountain", "water": "water", "forest": "forest"}
NEW_CHANNELS = ("coastal", "plain", "urban", "celestial", "underworld", "underground")


def _con() -> sqlite3.Connection:
    from src.infra.config import DB_PATH

    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _resolve(con: sqlite3.Connection, title: str) -> tuple[str, str]:
    rows = list(con.execute("SELECT id, title FROM novels"))
    for nid, t in rows:
        if t == title:
            return nid, t
    for nid, t in rows:
        if title in t:
            return nid, t
    raise SystemExit(f"找不到小说：{title}")


def _icons(con: sqlite3.Connection, novel_id: str) -> dict[str, str]:
    row = con.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0]).get("location_icons") or {}
    except (TypeError, ValueError):
        return {}


def _describe(con: sqlite3.Connection, novel_id: str) -> None:
    """不跑 LLM 的画像：有多少地点带描述、描述多长。"""
    rows = con.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ).fetchall()
    best: dict[str, int] = {}
    for (fj,) in rows:
        try:
            d = json.loads(fj)
        except (TypeError, ValueError):
            continue
        for loc in d.get("locations") or []:
            name = (loc.get("name") or "").strip()
            desc = (loc.get("description") or "").strip()
            if name and len(desc) > best.get(name, -1):
                best[name] = len(desc)
    with_desc = sum(1 for n in best.values() if n > 0)
    lens = sorted(n for n in best.values() if n > 0)
    if lens:
        p50 = lens[len(lens) // 2]
        p90 = lens[min(len(lens) - 1, int(len(lens) * 0.9))]
    else:
        p50 = p90 = 0
    print(f"地点总数 {len(best)}，带描述 {with_desc}（{with_desc / max(len(best), 1) * 100:.0f}%）")
    print(f"描述长度 中位 {p50} 字 / p90 {p90} 字，总字数 {sum(lens)}")
    print(f"估算：batch=10、本地 2–4s/批 ⇒ 全量约 {with_desc / 10 * 3 / 60:.0f} 分钟")


def compare(visuals, entries: dict[str, dict], icons: dict[str, str]) -> None:
    """规则 vs 描述 的四分类（另加"冲突"这一类）。

    ⚠️ 曾经把"规则与描述**冲突**"（规则=mountain、描述=water）混进 only-rule，
    于是"两边都说了但说法相反"和"只有规则说"被报成同一个数。修好了：
    冲突单独一列 —— 它是**唯一**能说明两边判断口径不同的证据，
    而 only-rule 大多只是描述不含地貌信息（规则判对）。
    """
    agree = rule_only = desc_only = conflict = neither = 0
    new_count: Counter = Counter()
    ex_desc: list[str] = []
    ex_conflict: list[str] = []
    ex_rule: list[str] = []

    for v in visuals:
        entry = entries.get(v.name) or {}
        if not entry.get("description"):
            continue
        old = influence_classes(v.name, entry.get("type", ""), icons.get(v.name, ""))
        desc = (
            {TERRAIN_TO_CHANNEL[v.terrain.value]}
            if v.terrain and v.terrain.value in TERRAIN_TO_CHANNEL
            else set()
        )
        label = f"{v.name}（规则={sorted(old)}，描述 terrain={v.terrain.value if v.terrain else None}）"
        if old and desc:
            if old == desc:
                agree += 1
            else:
                conflict += 1
                if len(ex_conflict) < 12:
                    ex_conflict.append(label)
        elif old:
            rule_only += 1
            if len(ex_rule) < 12:
                ex_rule.append(label.replace("，描述 terrain=None", "，描述无地貌"))
        elif desc:
            desc_only += 1
            if len(ex_desc) < 12:
                ex_desc.append(
                    f"{v.name}（描述={sorted(desc)}，证据：{v.evidence[:24]}）"
                )
        else:
            neither += 1
        if v.terrain and v.terrain.value in NEW_CHANNELS:
            new_count[v.terrain.value] += 1

    total = agree + rule_only + desc_only + conflict + neither
    n = max(total, 1)
    print(f"\n[对照] 山/水/林 三通道（共 {total} 个有描述的地点）")
    print(f"  agree          {agree:>5}  {agree / n * 100:>5.1f}%   两边同判同类")
    print(f"  only-rule      {rule_only:>5}  {rule_only / n * 100:>5.1f}%   规则说有、描述没说")
    print(f"  only-desc      {desc_only:>5}  {desc_only / n * 100:>5.1f}%   描述说有、规则没说 ← 增量")
    print(f"  conflict       {conflict:>5}  {conflict / n * 100:>5.1f}%   两边都说了但**不是同一类**")
    print(f"  neither        {neither:>5}  {neither / n * 100:>5.1f}%   双方都没说")
    print("  注：only-rule **不等于**规则的假阳性 —— 实测里多数是（濯垢泉/东洋海/子母河）"
          "规则本就判对、只是描述不含地貌信息。要判假阳性必须人看那几条，"
          "不能把这个数当错误率。")

    if ex_desc:
        print("\n  描述带来的召回（前 12）:")
        for line in ex_desc:
            print(f"    {line}")
    if ex_conflict:
        print("\n  ⚠️ 冲突（前 12）—— 这两处口径不一致，接线前必须逐条看:")
        for line in ex_conflict:
            print(f"    {line}")
    if ex_rule:
        print("\n  规则说有、描述没说（前 12）:")
        for line in ex_rule:
            print(f"    {line}")

    if new_count:
        print("\n[新增类别] 现有规则没有通道，纯增量：")
        for k, v in new_count.most_common():
            print(f"  {k:<14}{v:>5}")
        print("  注意：celestial / underworld 属**其它图层**（西游记已有独立天界/冥界布局），"
              "不该再喂进地理地形场。")


def report(s, visuals, entries, icons) -> None:
    print(f"\n[抽取] {s['locations']} 个地点")
    print(f"  terrain 非空     {s['terrain_hit']:>5}  {s['terrain_hit'] / s['locations'] * 100:>5.1f}%")
    print(f"  landmark 非空    {s['landmark_hit']:>5}  {s['landmark_hit'] / s['locations'] * 100:>5.1f}%")
    print(f"  evidence 通过    {s['evidence_ok']:>5}  {s['evidence_ok'] / s['locations'] * 100:>5.1f}%")
    print(f"  两项皆空         {s['both_empty']:>5}")
    print(f"  有结论的地点     {s['values_total']:>5}"
          f"  其中**无合法证据** {s['value_without_evidence']}"
          f"  ← 校验层没覆盖到的部分")
    print(f"  被枚举校验拦下   terrain {s['terrain_rejected']} / landmark {s['landmark_rejected']}")
    print(f"\n  terrain 分布: {s['terrain_dist']}")
    print(f"  landmark 分布: {s['landmark_dist']}")
    compare(visuals, entries, icons)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--novel", help="书名（可模糊匹配）；不给就对每本书只打印画像")
    ap.add_argument("--limit", type=int, default=120, help="抽样地点数（固定种子可复现）")
    ap.add_argument("--all", action="store_true", help="不抽样，跑全部有描述的地点")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--model", default="qwen2.5:7b", help="Ollama 模型名")
    ap.add_argument("--model-label", default="", help="写库时记录的模型名（默认同 --model）")
    ap.add_argument("--save", action="store_true", help="写入 location_visuals 表")
    ap.add_argument(
        "--from-store",
        action="store_true",
        help="不跑 LLM：直接读库里的 location_visuals 出报表（改报表口径时不重跑抽取）",
    )
    args = ap.parse_args()

    con = _con()
    if not args.novel:
        titles = [t for (t,) in con.execute("SELECT title FROM novels ORDER BY title")]
        for t in titles:
            row = con.execute(
                "SELECT COUNT(*) FROM chapter_facts f JOIN novels n ON n.id=f.novel_id "
                "WHERE n.title=?", (t,)
            ).fetchone()
            if not row or not row[0]:
                continue
            nid, _ = _resolve(con, t)
            print(f"\n=== {t} ===")
            _describe(con, nid)
        return

    novel_id, title = _resolve(con, args.novel)
    print(f"=== {title} ({novel_id}) ===")
    _describe(con, novel_id)

    from src.infra.llm_client import LLMClient
    from src.services.location_visual_extractor import (
        _load_entries,
        extract_location_visuals,
        summarize,
    )

    entries = {e["name"]: e for e in _load_entries(novel_id)}
    icons = _icons(con, novel_id)

    if args.from_store:
        import src.db.location_visual_store as store
        from src.db.sqlite_db import init_db

        asyncio.run(init_db())
        loaded = asyncio.run(store.load(novel_id))
        if not loaded:
            raise SystemExit("库里没有 location_visuals；先跑一次带 --save 的抽取")
        visuals = list(loaded.values())
        print(f"（读库：{len(visuals)} 行，不跑 LLM）")
        report(summarize(visuals), visuals, entries, icons)
        return

    limit = None if args.all else args.limit

    def progress(n, total, acc):
        if n % 5 == 0 or n == total:
            hit = sum(1 for v in acc if v.terrain is not None or v.landmark is not None)
            print(f"  batch {n}/{total}  已标注 {hit}/{len(acc)}", flush=True)

    visuals = asyncio.run(
        extract_location_visuals(
            novel_id,
            client=LLMClient(model=args.model),
            batch_size=args.batch,
            limit=limit,
            seed=args.seed,
            on_batch=progress,
        )
    )
    if not visuals:
        print("没有可标注的地点（都缺描述）")
        return

    report(summarize(visuals), visuals, entries, icons)

    if args.save:
        import src.db.location_visual_store as store
        from src.db.sqlite_db import init_db

        # The table is created by the app's schema init; a bare script run may be
        # the first thing to touch this DB, so make it exist before inserting.
        # `init_db` is all `IF NOT EXISTS`, so this stays idempotent.
        asyncio.run(init_db())
        written = asyncio.run(
            store.save(novel_id, visuals, model=args.model_label or args.model)
        )
        print(f"\n已写入 location_visuals：{written} 行")
    else:
        print("\n（未写库；加 --save 才落盘）")


if __name__ == "__main__":
    main()
