#!/usr/bin/env python
"""Story 5.5 自动检查 — 三国重建后层级边的两项硬指标。

指标(取自 epics-quality-hardening-v076.md Story 5.5 验收):
  A. 道路/通道类节点作为 parent 的计数 —— 目标 0
     (Story 5.2 AC1:passage-like 节点永不为 parent)
  B. 仅 topology 证据支持的 parent 边计数 —— 目标 0

B 的口径(易错,务必按此实现):
  在 chapter_facts 里找支持 (child,parent) 这一对关系的**证据类别**:
    - 有 hierarchy 类(contains / located_in)证据      → 合规
    - 只有 topology 类(connects/adjacent/direction…)   → topology-only(违规)
    - 两类证据都没有                                    → 零证据遗留边
  第三类是**设计保留**而非违规:edmonds_resolver :183-199 明确写着
  "child 无任何票 → 保留旧 parent",因为很多零证据遗留边是正确的
  (注释举例 涿郡→幽州,而该边确实成立)。把它们算作违规会把 1051 条
  正确边误判成错误,所以单列一桶。

用法:
    AI_READER_DATA_DIR=/tmp/sanguo-rebuild uv run python scripts/check_sanguo_hierarchy.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("AI_READER_DATA_DIR", "/tmp/sanguo-rebuild")

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from src.infra.config import DB_PATH  # noqa: E402
from src.models.chapter_fact import classify_spatial_relation  # noqa: E402
from src.services.geo_skills.orchestrator import (  # noqa: E402
    snapshot_from_world_structure,
)
from src.services.geo_skills.vote_builder import VoteBuilder  # noqa: E402
from src.services.world_structure_agent import (  # noqa: E402
    TIER_ORDER,
    _get_suffix_rank,
)
from src.utils.location_names import is_passage_like  # noqa: E402

NOVEL_ID = "b1287ef6-c215-4bd2-842c-cb04aec5eb70"
OUT_DIR = _BACKEND / "audit_reports"
STAMP = os.environ.get("STAMP", "20260908_sanguo_full120")


def build_evidence_index(tiers: dict[str, str]) -> dict[tuple[str, str], set[str]]:
    """(child, parent) -> 证据类别集合 {"hierarchy","topology","other"}。

    来源两处:
      1. spatial_relationships 的 source/target + relation_type
      2. locations[].parent —— LLM 直判的层级证据,算 hierarchy
    """
    idx: dict[tuple[str, str], set[str]] = {}
    con = sqlite3.connect(str(DB_PATH))
    try:
        rows = con.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
            (NOVEL_ID,),
        ).fetchall()
    finally:
        con.close()

    for (raw,) in rows:
        try:
            fact = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for rel in fact.get("spatial_relationships") or []:
            src = rel.get("source")
            tgt = rel.get("target")
            if not src or not tgt:
                continue
            cls = classify_spatial_relation(rel.get("relation_type") or "")
            # 边方向 (child→parent): parent 可能是 source(contains) 或 target(located_in)
            idx.setdefault((tgt, src), set()).add(cls)
            idx.setdefault((src, tgt), set()).add(cls)

        for loc in fact.get("locations") or []:
            name = loc.get("name")
            parent = loc.get("parent")
            if name and parent:
                idx.setdefault((name, parent), set()).add("hierarchy")

        # ③ 主场景推断(primary setting inference)—— 第三种票源。
        # 漏掉它就会把由「本章主场景」支撑的合法边误判成 topology-only。
        # 逻辑复刻 vote_builder.execute 的 Primary setting inference 段。
        primary = None
        settings = [
            l for l in (fact.get("locations") or [])
            if l.get("role") == "setting" and l.get("name")
        ]
        if settings:
            best_rank = 999
            for loc in settings:
                suf = _get_suffix_rank(loc["name"])
                rank = suf if suf is not None else TIER_ORDER.get(
                    tiers.get(loc["name"], "city"), 4)
                if rank < best_rank:
                    best_rank, primary = rank, loc["name"]
        else:
            for loc in fact.get("locations") or []:
                if loc.get("name"):
                    primary = loc["name"]
                    break
        if primary and not is_passage_like(primary):
            p_suf = _get_suffix_rank(primary)
            p_rank = p_suf if p_suf is not None else TIER_ORDER.get(
                tiers.get(primary, "city"), 4)
            for loc in fact.get("locations") or []:
                ln = loc.get("name", "")
                if not ln or ln == primary or loc.get("parent"):
                    continue
                if loc.get("role") in ("referenced", "boundary"):
                    continue
                c_suf = _get_suffix_rank(ln)
                c_rank = c_suf if c_suf is not None else TIER_ORDER.get(
                    tiers.get(ln, "city"), 4)
                if c_rank <= p_rank:
                    continue
                idx.setdefault((ln, primary), set()).add("hierarchy")
    return idx


async def load_votes() -> dict:
    snap = await snapshot_from_world_structure(NOVEL_ID)
    result = await VoteBuilder(NOVEL_ID).run(snap)
    return result.new_votes


def main() -> None:
    votes = asyncio.run(load_votes())

    after = json.loads(
        (OUT_DIR / f"hierarchy_after_{STAMP}.json").read_text(encoding="utf-8")
    )
    parents: dict[str, str] = after["location_parents"]
    tiers: dict[str, str] = after["location_tiers"]
    evidence = build_evidence_index(tiers)

    print(f"最终边数: {len(parents)}  地点数: {len(tiers)}")
    print(f"hierarchy 票覆盖 child 数: {len(votes)}")
    print(f"证据索引条目数: {len(evidence)}")

    # ── A. 道路/通道类 parent ────────────────────────────────────────
    road_parents = [
        {"child": c, "parent": p}
        for c, p in parents.items()
        if is_passage_like(p)
    ]

    # ── B. 仅 topology 证据支持的边 ──────────────────────────────────
    # name-containment 是管线显式注入的**合法层级依据**
    # (edmonds_resolver 以 weight=25 注入,注释称覆盖 306+ 例),不是拓扑证据。
    # 只看 chapter_facts 会把这类边误判成 topology-only
    # (如 许昌城外→许昌:文本里只有 distance 关系,但 许昌城外.startswith(许昌))。
    all_locs = set(tiers.keys()) | set(parents.values())

    def name_containment_parent(child: str) -> str | None:
        """复刻 edmonds_resolver 的 name-containment:最长前缀匹配。"""
        if is_passage_like(child):
            return None
        for cand in sorted(all_locs, key=len, reverse=True):
            if cand == child or len(cand) < 2:
                continue
            if is_passage_like(cand):
                continue
            if child.startswith(cand):
                return cand
        return None

    topo_only: list[dict] = []
    zero_evidence: list[dict] = []
    hierarchy_ok = 0
    name_containment_ok = 0
    for child, parent in parents.items():
        classes = set(evidence.get((child, parent), set()))
        if "hierarchy" not in classes and parent == name_containment_parent(child):
            classes.add("hierarchy")
            name_containment_ok += 1
        if "hierarchy" in classes:
            hierarchy_ok += 1
        elif "topology" in classes:
            topo_only.append({
                "child": child, "parent": parent,
                "classes": sorted(classes),
            })
        else:
            zero_evidence.append({"child": child, "parent": parent})

    print("\n=== Story 5.5 自动检查 ===")
    print(f"A. 道路/通道类 parent 边数 : {len(road_parents):>5}  (目标 0)")
    print(f"B. topology-only parent 边 : {len(topo_only):>5}  (目标 0)")
    print(f"   旁证: hierarchy 证据支撑 : {hierarchy_ok:>5}"
          f"(含 name-containment {name_containment_ok})")
    print(f"   旁证: 零证据遗留边(设计保留): {len(zero_evidence):>5}")
    print(f"   判定: {'PASS' if not road_parents and not topo_only else 'FAIL'}")

    if road_parents:
        print("\n-- A 样例(前 20) --")
        for e in road_parents[:20]:
            print(f"   {e['child']} → {e['parent']}")
    if topo_only:
        print("\n-- B 样例(前 20) --")
        for e in topo_only[:20]:
            print(f"   {e['child']} → {e['parent']}  classes={e['classes']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"story_5_5_autocheck_{STAMP}.json").write_text(
        json.dumps({
            "novel_id": NOVEL_ID,
            "edge_count": len(parents),
            "road_parent_edges": road_parents,
            "topology_only_edges": topo_only,
            "hierarchy_supported": hierarchy_ok,
            "zero_evidence_legacy": len(zero_evidence),
            "verdict": {
                "road_parent": len(road_parents),
                "topology_only": len(topo_only),
                "pass": not road_parents and not topo_only,
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n已导出 -> {OUT_DIR}/story_5_5_autocheck_{STAMP}.json")


if __name__ == "__main__":
    main()
