#!/usr/bin/env python
"""诊断 geo 链非确定性:逐 phase 导出 parents,定位第一个分歧的 phase。

背景(2026-09-08):
    同一 base 连续两次 rebuild 出现 50-89 条 parent 差异。已修 set 迭代顺序
    (sorted + 二级键),差异从 89 降到 50,但没归零。本脚本用于定位剩余分歧点。

用法(需在**两个独立进程**跑,否则 set 顺序固定,测不出差异):
    AI_READER_DATA_DIR=... AI_READER_FORCE_DB_KEY=1 \\
        uv run python scripts/trace_phase_determinism.py run1
    ... 再跑一次 run2 ...
    uv run python scripts/trace_phase_determinism.py --compare run1 run2
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from src.services.geo_skills.orchestrator import (  # noqa: E402
    build_default_orchestrator,
    snapshot_from_world_structure,
)

OUT = Path("/tmp/phase_trace")
NOVELS = {
    "sanguo": ("b1287ef6-c215-4bd2-842c-cb04aec5eb70", "三国演义"),
}


async def trace(slug: str, tag: str) -> None:
    nid, title = NOVELS[slug]
    orch = build_default_orchestrator(nid, novel_title=title)

    # 用 load_latest 之外的干净起点:直接从 world_structure 导入,
    # 保证两次运行的初始快照一致
    snap = await snapshot_from_world_structure(nid)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{tag}_{slug}_init.json").write_text(
        json.dumps(dict(sorted(snap.location_parents.items())),
                   ensure_ascii=False, indent=2),
        encoding="utf-8")

    for name, skill in orch._skills:
        result = await skill.run(snap)
        if result.success:
            snap = snap.apply(result)
        (OUT / f"{tag}_{slug}_{name}.json").write_text(
            json.dumps(dict(sorted(snap.location_parents.items())),
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"  [{name}] parents={len(snap.location_parents)}", flush=True)

    # apply 后的最终结果(含 _inject_layer_roots)
    await orch.apply_to_world_structure()
    print("  [apply] done", flush=True)


def compare(t1: str, t2: str, slug: str = "sanguo") -> None:
    stages = ["init", "tier", "votes", "prior", "edmonds", "suffix", "purify"]
    print(f"{'phase':10}{f'{t1} 边数':>12}{f'{t2} 边数':>12}{'差异':>10}")
    first_diff = None
    for st in stages:
        f1, f2 = OUT / f"{t1}_{slug}_{st}.json", OUT / f"{t2}_{slug}_{st}.json"
        if not (f1.exists() and f2.exists()):
            continue
        a, b = json.loads(f1.read_text()), json.loads(f2.read_text())
        d = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
        mark = ""
        if d and first_diff is None:
            first_diff = st
            mark = "  ← 首个分歧"
        print(f"{st:10}{len(a):>12}{len(b):>12}{len(d):>10}{mark}")
    print(f"\n首个分歧 phase: {first_diff}")
    if first_diff:
        f1, f2 = OUT / f"{t1}_{slug}_{first_diff}.json", OUT / f"{t2}_{slug}_{first_diff}.json"
        a, b = json.loads(f1.read_text()), json.loads(f2.read_text())
        d = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
        for k, v in list(d.items())[:10]:
            print(f"   {k}: {v[0]} -> {v[1]}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--compare":
        compare(args[1], args[2])
        return
    tag = args[0] if args else "run1"
    slug = args[1] if len(args) > 1 else "sanguo"
    asyncio.run(trace(slug, tag))


if __name__ == "__main__":
    main()
