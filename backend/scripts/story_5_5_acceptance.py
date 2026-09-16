#!/usr/bin/env python
"""Story 5.5 五本通用验收 — geo 链重建 + A/B 两项硬指标。

背景:``check_sanguo_hierarchy.py`` 只硬编码三国一本。改核心投票逻辑
(vote_builder loc.parent 缩进修复) 后必须验证**五本**都不劣化,故新增本脚本。
判定逻辑与 check_sanguo_hierarchy.py 完全一致(证据四源 + name-containment),
只是把 novel_id / title 参数化。

指标(epics-quality-hardening-v076.md Story 5.5 验收):
  A. 道路/通道类节点作为 parent 的计数 —— 目标 0
  B. 仅 topology 证据支持的 parent 边计数 —— 目标 0
     · 有 hierarchy 证据        → 合规
     · 只有 topology            → 违规
     · 两类都无                 → 零证据遗留边(设计保留,单列一桶)

用法:
    AI_READER_DATA_DIR=~/.ai-reader-v2-rerun-20260906 \
    AI_READER_FORCE_DB_KEY=1 uv run python scripts/story_5_5_acceptance.py

    NOVELS=sanguo,shuihu uv run python scripts/story_5_5_acceptance.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

from src.infra.config import DB_PATH  # noqa: E402
from src.models.chapter_fact import classify_spatial_relation  # noqa: E402
from src.services.geo_skills.orchestrator import (  # noqa: E402
    build_default_orchestrator,
)
from src.services.geo_skills.suffix_normalizer import (  # noqa: E402
    _EXPLICIT_SYNONYMS,
    _MERGE_SUFFIXES,
)
from src.services.world_structure_agent import (  # noqa: E402
    TIER_ORDER,
    _get_suffix_rank,
)
from src.utils.location_names import is_passage_like  # noqa: E402

OUT_DIR = _BACKEND / "audit_reports"

# demo 五本(scratch 库 novel_id;主库同 id,因为 scratch 由主库拷贝)
BOOKS: dict[str, tuple[str, str]] = {
    "sanguo": ("b1287ef6-c215-4bd2-842c-cb04aec5eb70", "三国演义"),
    "shuihu": ("4ac43c73-f67b-427c-8d6d-e766a1423977", "水浒传"),
    "honglou": ("c384901a-8b71-437a-af35-b5ec1c56c696", "红楼梦"),
    "xiyouji": ("3b2ef56c-1a55-466a-a7d1-34272446a198", "西游记"),
    "fengshen": ("53013970-effd-4f50-aef7-728ca13de69a", "封神演义"),
}


MAIN_DB = Path(os.path.expanduser("~/.ai-reader-v2/data.db"))


def reset_base_to_main(novel_id: str) -> None:
    """把 scratch 库的 location_parents/tiers 重置为主库值。

    用于 before/after 对比:同一 base 上分别用修复前/修复后代码重建,
    否则 base 会随每次 rebuild 漂移,两次结果不可比。
    主库**只读**,不做任何写入。
    """
    src = sqlite3.connect(str(MAIN_DB))
    try:
        row = src.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?",
            (novel_id,),
        ).fetchone()
    finally:
        src.close()
    if not row:
        print(f"  ! 主库无 world_structure,跳过重置: {novel_id}")
        return
    main_payload = json.loads(row[0])

    dst = sqlite3.connect(str(DB_PATH))
    try:
        cur = dst.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?",
            (novel_id,),
        ).fetchone()
        if not cur:
            return
        p = json.loads(cur[0])
        p["location_parents"] = main_payload.get("location_parents", {})
        p["location_tiers"] = main_payload.get("location_tiers", {})
        dst.execute(
            "UPDATE world_structures SET structure_json=? WHERE novel_id=?",
            (json.dumps(p, ensure_ascii=False), novel_id),
        )
        dst.commit()
    finally:
        dst.close()


def read_state(novel_id: str) -> dict:
    con = sqlite3.connect(str(DB_PATH))
    try:
        row = con.execute(
            "SELECT structure_json FROM world_structures WHERE novel_id=?",
            (novel_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"world_structures 无此小说: {novel_id}")
        p = json.loads(row[0])
        return {
            "location_parents": p.get("location_parents", {}),
            "location_tiers": p.get("location_tiers", {}),
        }
    finally:
        con.close()


async def rebuild(novel_id: str, title: str) -> None:
    # fresh=True:始终从 world_structures 导入 v0,避免「在上次快照上继续优化」
    # 造成的起点漂移(同 base 两次重建会差约 50 条 parent)。
    orch = build_default_orchestrator(novel_id, novel_title=title)
    async for _event in orch.run(fresh=True):
        pass
    await orch.apply_to_world_structure()


def build_evidence_index(novel_id: str, tiers: dict[str, str]) -> dict:
    """(child,parent) -> 证据类别集合。四源:loc.parent / 空间关系 / 主场景推断。"""
    idx: dict[tuple[str, str], set[str]] = {}
    con = sqlite3.connect(str(DB_PATH))
    try:
        rows = con.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
            (novel_id,),
        ).fetchall()
    finally:
        con.close()

    for (raw,) in rows:
        try:
            fact = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for rel in fact.get("spatial_relationships") or []:
            src, tgt = rel.get("source"), rel.get("target")
            if not src or not tgt:
                continue
            cls = classify_spatial_relation(rel.get("relation_type") or "")
            idx.setdefault((tgt, src), set()).add(cls)
            idx.setdefault((src, tgt), set()).add(cls)

        for loc in fact.get("locations") or []:
            name, parent = loc.get("name"), loc.get("parent")
            if name and parent:
                idx.setdefault((name, parent), set()).add("hierarchy")

        # 主场景推断 —— 复刻 vote_builder 的逻辑
        primary = None
        settings = [
            loc for loc in (fact.get("locations") or [])
            if loc.get("role") == "setting" and loc.get("name")
        ]
        if settings:
            best = 999
            for loc in settings:
                suf = _get_suffix_rank(loc["name"])
                rank = suf if suf is not None else TIER_ORDER.get(
                    tiers.get(loc["name"], "city"), 4)
                if rank < best:
                    best, primary = rank, loc["name"]
        else:
            for loc in fact.get("locations") or []:
                if loc.get("name"):
                    primary = loc.get("name")
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


def build_synonym_map(all_locs: set[str]) -> dict[str, str]:
    """复刻 SuffixNormalizer 的 variant→base 映射。

    这些边是**同义变体归并**(旱路边→旱路 / 十字街头→十字街),不是层级
    包含关系。把它们算进 A/B 会把「道路下辖实体」误报成违规 —— 实测
    A 指标 7 条违规全部出自这里(2026-09-08)。
    """
    v2b: dict[str, str] = {}
    for base, variants in _EXPLICIT_SYNONYMS.items():
        if base not in all_locs:
            continue
        for v in variants:
            if v in all_locs and v != base:
                v2b[v] = base
    for name in all_locs:
        if name in v2b:
            continue
        for suffix, min_len in _MERGE_SUFFIXES:
            if not name.endswith(suffix):
                continue
            base = name[: -len(suffix)]
            if len(base) < min_len:
                continue
            if base in all_locs and base != name:
                v2b[name] = base
                break
    return v2b


def evaluate(slug: str, novel_id: str) -> dict:
    st = read_state(novel_id)
    parents, tiers = st["location_parents"], st["location_tiers"]
    evidence = build_evidence_index(novel_id, tiers)

    all_locs = set(tiers.keys()) | set(parents.values())
    # SuffixNormalizer 的 all_locs 含 parents.keys()(child 本身),不能只取
    # tiers+parent 值 —— 否则 县西巷内 这类 child 不在集合里,归并映射漏建。
    syn_locs = all_locs | set(parents.keys())
    syn = build_synonym_map(syn_locs)

    road_parents = [
        {"child": c, "parent": p}
        for c, p in parents.items()
        if is_passage_like(p) and syn.get(c) != p
    ]

    def name_containment_parent(child: str) -> str | None:
        if is_passage_like(child):
            return None
        for cand in sorted(all_locs, key=len, reverse=True):
            if cand == child or len(cand) < 2 or is_passage_like(cand):
                continue
            if child.startswith(cand):
                return cand
        return None

    topo_only, zero = [], []
    hierarchy_ok = name_containment_ok = 0
    synonym_merged = 0
    for child, parent in parents.items():
        if syn.get(child) == parent:
            synonym_merged += 1
            continue
        classes = set(evidence.get((child, parent), set()))
        if "hierarchy" not in classes and parent == name_containment_parent(child):
            classes.add("hierarchy")
            name_containment_ok += 1
        if "hierarchy" in classes:
            hierarchy_ok += 1
        elif "topology" in classes:
            topo_only.append({"child": child, "parent": parent,
                              "classes": sorted(classes)})
        else:
            zero.append({"child": child, "parent": parent})

    return {
        "slug": slug,
        "novel_id": novel_id,
        "edge_count": len(parents),
        "location_count": len(tiers),
        "evidence_index_entries": len(evidence),
        "road_parent": road_parents,
        "topology_only": topo_only,
        "hierarchy_supported": hierarchy_ok,
        "name_containment": name_containment_ok,
        "synonym_merged_excluded": synonym_merged,
        "zero_evidence_legacy": len(zero),
        "verdict": {
            "road_parent": len(road_parents),
            "topology_only": len(topo_only),
            "pass": not road_parents and not topo_only,
        },
    }


def main() -> None:
    only = os.environ.get("NOVELS")
    slugs = only.split(",") if only else list(BOOKS)
    stamp = os.environ.get("STAMP", "story55")
    do_reset = os.environ.get("RESET_BASE") == "1"

    results = []
    for slug in slugs:
        novel_id, title = BOOKS[slug]
        print(f"\n=== {slug} ({title}) ===", flush=True)
        if do_reset:
            reset_base_to_main(novel_id)
            print("  base 已重置为主库值", flush=True)
        asyncio.run(rebuild(novel_id, title))
        r = evaluate(slug, novel_id)
        # 导出 parents 快照,供「同 base 两次重建」的确定性对比使用
        (OUT_DIR / f"hierarchy_after_{stamp}_{slug}.json").write_text(
            json.dumps(read_state(novel_id)["location_parents"],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(r)
        v = r["verdict"]
        print(f"  边数 {r['edge_count']}  地点 {r['location_count']}  "
              f"证据索引 {r['evidence_index_entries']}")
        print(f"  A 道路类 parent = {v['road_parent']}  "
              f"B topology-only = {v['topology_only']}  "
              f"→ {'PASS' if v['pass'] else 'FAIL'}")
        print(f"  hierarchy 支撑 {r['hierarchy_supported']}"
              f"(含 name-containment {r['name_containment']})  "
              f"零证据遗留 {r['zero_evidence_legacy']}")
        for e in r["topology_only"][:8]:
            print(f"     · {e['child']} → {e['parent']}  {e['classes']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"story_5_5_acceptance_{stamp}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print(f"\n{'书':10}{'边数':>7}{'A 道路':>8}{'B topo':>8}{'层级支撑':>10}"
          f"{'零证据':>8}   判定")
    for r in results:
        v = r["verdict"]
        print(f"{r['slug']:10}{r['edge_count']:>7}{v['road_parent']:>8}"
              f"{v['topology_only']:>8}{r['hierarchy_supported']:>10}"
              f"{r['zero_evidence_legacy']:>8}   "
              f"{'PASS' if v['pass'] else 'FAIL'}")
    allpass = all(r["verdict"]["pass"] for r in results)
    print(f"\n总体: {'PASS' if allpass else 'FAIL'}")
    print(f"已导出 -> {out}")


if __name__ == "__main__":
    main()
