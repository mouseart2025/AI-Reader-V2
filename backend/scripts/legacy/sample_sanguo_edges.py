#!/usr/bin/env python
"""Story 5.5 — 三国层级边 180 条分层抽样抽查表。

分层(Spec 原写「各 1/3」,实测数据不支持,已按下述调整并说明理由):
  核心 core   : world / continent / kingdom —— 大尺度容器,错了影响整棵子树
  常规 regular: region / city              —— 中间层
  微观 micro  : site / building / 未分级     —— 细节层

实测三国重建后各层边数:core=18, regular=191, micro=1111。
core 只有 18 条,取不到 60,故改为 **core 全查(census)**——它数量少但
风险最高(一条错边污染整棵子树);剩余 162 条在 regular 与 micro 间
平分各 81。合计仍为 180,且对最高风险层达到 100% 覆盖。

抽样用**确定性**等距取样(按 child 名排序后取等距索引),保证可复现,
同一份数据每次跑出的 180 条完全一致。

输出:
  backend/audit_reports/story_5_5_sample_180_<stamp>.md   人工核对表
  backend/audit_reports/story_5_5_sample_180_<stamp>.json  机读

人工核对口径(每条判 正确 / 错误):
  「child 是否确实位于 parent 之内」—— 按《三国演义》地理常识判断。
  存疑(如势力范围类 parent)标「存疑」并在备注说明,不计入错边。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("AI_READER_DATA_DIR", "/tmp/sanguo-rebuild")

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

OUT_DIR = _BACKEND / "audit_reports"
STAMP = "20260906"
SRC = OUT_DIR / f"hierarchy_after_{STAMP}_sanguo_rebuild.json"

CORE = {"world", "continent", "kingdom"}
REGULAR = {"region", "city"}
# 其余(site / building / street …)归入微观

N_TOTAL = 180
# core 全查;剩余额度在 regular / micro 间平分
N_REGULAR = N_MICRO = 81


def stratum_of(tier: str | None) -> str:
    if tier in CORE:
        return "core"
    if tier in REGULAR:
        return "regular"
    return "micro"


def even_sample(items: list, n: int) -> list:
    """确定性等距抽样(保持原顺序)。"""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    parents: dict[str, str] = data["location_parents"]
    tiers: dict[str, str] = data["location_tiers"]

    buckets: dict[str, list[dict]] = {"core": [], "regular": [], "micro": []}
    for child in sorted(parents):
        parent = parents[child]
        st = stratum_of(tiers.get(child))
        buckets[st].append({
            "child": child,
            "parent": parent,
            "child_tier": tiers.get(child),
            "parent_tier": tiers.get(parent),
            "stratum": st,
        })

    # core 全查(census);regular 取 81;micro 补足到 N_TOTAL
    core_all = buckets["core"]
    picked: list[dict] = list(core_all)
    picked += even_sample(buckets["regular"], N_REGULAR)
    picked += even_sample(buckets["micro"], N_TOTAL - len(picked))

    # ── Markdown 抽查表 ────────────────────────────────────────────
    lines = [
        "# Story 5.5 — 三国层级边 180 条人工抽查表",
        "",
        f"- 来源: `/tmp/sanguo-rebuild`(三国 120 章,Epic 5 当前代码重建后)",
        f"- 边总数: {len(parents)} · 本次抽样: {len(picked)}",
        "- 抽样:核心层 18 条**全查**;常规层/微观层各等距取 81 条;合计 180",
        "- 层内按名称排序后等距取样,确定性可复现(同数据每次结果一致)",
        "- 判定栏填写 `正确` / `错误` / `存疑`;**错边目标 ≤2/180**",
        "",
        "| # | 分层 | child | parent | child_tier | parent_tier | 判定 | 备注 |",
        "|---|------|-------|--------|-----------|-------------|------|------|",
    ]
    for i, e in enumerate(picked, 1):
        lines.append(
            f"| {i} | {e['stratum']} | {e['child']} | {e['parent']} | "
            f"{e['child_tier'] or '-'} | {e['parent_tier'] or '-'} |  |  |"
        )
    lines += [
        "",
        "## 汇总(核对后填写)",
        "",
        "| 分层 | 抽样数 | 正确 | 错误 | 存疑 |",
        "|------|-------|------|------|------|",
    ]
    for st, label in (("core", "核心"), ("regular", "常规"), ("micro", "微观")):
        cnt = sum(1 for e in picked if e["stratum"] == st)
        lines.append(f"| {label} | {cnt} |  |  |  |")
    lines.append(f"| **合计** | **{len(picked)}** |  |  |  |")

    md_path = OUT_DIR / f"story_5_5_sample_180_{STAMP}.md"
    json_path = OUT_DIR / f"story_5_5_sample_180_{STAMP}.json"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(
        json.dumps({
            "source": str(SRC),
            "total_edges": len(parents),
            "sampled": len(picked),
            "stratum_sizes": {k: len(v) for k, v in buckets.items()},
            "edges": picked,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"边总数 {len(parents)};分层规模: "
          + ", ".join(f"{k}={len(v)}" for k, v in buckets.items()))
    print(f"抽样 {len(picked)} 条 -> {md_path.name}")
    print(f"机读 -> {json_path.name}")


if __name__ == "__main__":
    main()
