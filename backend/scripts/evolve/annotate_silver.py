"""GeoEvolve R2 —— 非古典 golden 标注扩充（双模型共识，无人工干预）。

对象：魔戒全集（82 章全量）/ 凡人修仙传（前 100 章），各约 50 个地点。

质量保证（无人干预版）：双模型独立标注取交集——
  通道 A: DeepSeek deepseek-chat（中文视角 prompt,annotate_a_s4.txt,冻结）
  通道 B: DashScope qwen-max（英文视角 prompt,annotate_b_s4.txt,冻结）
  （注:Anthropic key 实测 403 不可用,2026-09-18 改用 qwen-max——仍为真双模型）
  仅保留 correct_parent 与 tier 双字段一致的条目；不一致/任一 uncertain 丢弃不仲裁。
反循环污染：标注 prompt 新写并冻结入清单，与评估扫描 prompt（M2,冻结于
  quality_dashboard.py）完全分离。

产出（silver 层级，provenance 注明"双模型共识，未经人工"）：
  tests/fixtures/golden_standard_lotr.json / golden_standard_fanren.json
  —— 参与 OOD 进化评估;不进 quality_loop 的 golden 硬门禁（其口径不变）。

Usage:
    cd backend && .venv/bin/python scripts/evolve/annotate_silver.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import quality_dashboard as qd  # noqa: E402

PROMPT_A = (_EVOLVE_DIR / "prompts" / "annotate_a_s4.txt").read_text(encoding="utf-8")
PROMPT_B = (_EVOLVE_DIR / "prompts" / "annotate_b_s4.txt").read_text(encoding="utf-8")
BATCH = 10

TARGETS = {
    "lotr": {"title_like": "魔戒全集", "novel": "魔戒全集", "author": "J.R.R. 托尔金",
             "max_chapters": None, "target_n": 50,
             "fixture": "golden_standard_lotr.json"},
    "fanren": {"title_like": "凡人修仙传", "novel": "凡人修仙传", "author": "忘语",
               "max_chapters": 100, "target_n": 50,
               "fixture": "golden_standard_fanren.json"},
}
TIERS = ("world", "continent", "kingdom", "region", "city", "site", "building")


def load_candidates(conn, novel_id: str, target_n: int) -> list[dict]:
    """候选地点：层级节点中频次≥2、非泛称,按频次降序取 ~1.4×target 个。"""
    from src.extraction.fact_validator import _is_generic_location

    ws = json.loads(conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?",
        (novel_id,)).fetchone()[0])
    lp = ws.get("location_parents") or {}
    freq: Counter = Counter()
    for (fj,) in conn.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)):
        try:
            fact = json.loads(fj)
        except Exception:
            continue
        for loc in fact.get("locations") or []:
            name = (loc.get("name") or "").strip()
            if name:
                freq[name] += 1
    names = sorted(set(lp) | {p for p in lp.values() if p})
    cands = []
    for n in names:
        if freq.get(n, 0) < 2 or _is_generic_location(n):
            continue
        cands.append({"name": n, "current_parent": lp.get(n),
                      "frequency": freq[n]})
    cands.sort(key=lambda c: (-c["frequency"], c["name"]))
    return cands[: int(target_n * 1.4)]


def candidate_parents(conn, novel_id: str) -> list[str]:
    """候选父级清单：频次 top40 的节点（供模型选择,防自由编造父名）。"""
    ws = json.loads(conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?",
        (novel_id,)).fetchone()[0])
    lp = ws.get("location_parents") or {}
    freq: Counter = Counter()
    for (fj,) in conn.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)):
        try:
            fact = json.loads(fj)
        except Exception:
            continue
        for loc in fact.get("locations") or []:
            name = (loc.get("name") or "").strip()
            if name:
                freq[name] += 1
    top = [n for n, _ in freq.most_common(40) if n in lp or n in set(lp.values())]
    return sorted(top)


async def annotate_channel_a(title: str, batch: list[dict], parents: list[str],
                             cost_acc: dict) -> list[dict]:
    """通道 A:DeepSeek,中文视角。"""
    user = json.dumps({"novel": title, "candidate_parents": parents,
                       "locations": batch}, ensure_ascii=False)
    raw = await qd.deepseek_chat(PROMPT_A, user, "annotate-A", cost_acc)
    return qd.parse_llm_json(raw).get("annotations", [])


async def annotate_channel_b(title: str, batch: list[dict], parents: list[str],
                             cost_acc: dict) -> list[dict]:
    """通道 B:DashScope qwen-max(OpenAI 兼容端点,英文视角 prompt B)。"""
    import httpx

    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY 未设置")
    payload = {
        "model": "qwen-max",
        "messages": [{"role": "system", "content": PROMPT_B},
                     {"role": "user", "content":
                      json.dumps({"novel": title, "candidate_parents": parents,
                                  "locations": batch}, ensure_ascii=False)}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=180.0) as client:
        resp = await client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
    usage = data.get("usage", {})
    # qwen-max 刊例价 ≈ $0.0004/1k in, $0.0012/1k out(2026 估算,量级记录用)
    cost = (usage.get("prompt_tokens", 0) * 0.0004
            + usage.get("completion_tokens", 0) * 0.0012) / 1000
    cost_acc["cost_usd"] += cost
    print(f"[annotate-B] in={usage.get('prompt_tokens', 0):,} "
          f"out={usage.get('completion_tokens', 0):,} cost≈${cost:.4f}")
    return qd.parse_llm_json(
        data["choices"][0]["message"]["content"]).get("annotations", [])


def consensus(a: list[dict], b: list[dict]) -> tuple[list[dict], dict]:
    """双通道交集:parent+tier 双字段一致且双方非 uncertain 才保留。"""
    by_b = {x.get("name"): x for x in b}
    kept, stats = [], {"agree": 0, "disagree": 0, "uncertain": 0, "missing": 0}
    for xa in a:
        name = xa.get("name")
        xb = by_b.get(name)
        if xb is None:
            stats["missing"] += 1
            continue
        if xa.get("uncertain") or xb.get("uncertain"):
            stats["uncertain"] += 1
            continue
        pa, pb = xa.get("correct_parent") or None, xb.get("correct_parent") or None
        ta, tb = xa.get("tier"), xb.get("tier")
        if ta not in TIERS or tb not in TIERS:
            stats["disagree"] += 1
            continue
        if pa == pb and ta == tb:
            kept.append({"name": name, "correct_parent": pa, "tier": ta})
            stats["agree"] += 1
        else:
            stats["disagree"] += 1
    return kept, stats


async def annotate_novel(conn, slug: str, cfg: dict, cost_acc: dict) -> dict:
    rows = conn.execute("SELECT id, title FROM novels WHERE title LIKE ?",
                        (f"%{cfg['title_like']}%",)).fetchall()
    assert len(rows) == 1, f"{cfg['title_like']} 匹配 {len(rows)} 行"
    nid, title = rows[0]
    cands = load_candidates(conn, nid, cfg["target_n"])
    parents = candidate_parents(conn, nid)
    print(f"[{slug}] 候选 {len(cands)} 个,候选父级 {len(parents)} 个")
    batches = [cands[i: i + BATCH] for i in range(0, len(cands), BATCH)]
    all_a: list[dict] = []
    all_b: list[dict] = []
    for i, batch in enumerate(batches):
        ra, rb = await asyncio.gather(
            annotate_channel_a(title, batch, parents, cost_acc),
            annotate_channel_b(title, batch, parents, cost_acc),
            return_exceptions=True)
        if isinstance(ra, Exception):
            print(f"[{slug}] batch{i} A 通道失败: {ra}")
            ra = []
        if isinstance(rb, Exception):
            print(f"[{slug}] batch{i} B 通道失败: {rb}")
            rb = []
        all_a.extend(ra)
        all_b.extend(rb)
    kept, stats = consensus(all_a, all_b)
    total = stats["agree"] + stats["disagree"] + stats["uncertain"] + stats["missing"]
    stats["agreement_rate"] = round(stats["agree"] / max(total, 1), 4)
    print(f"[{slug}] 共识保留 {len(kept)} 条,一致率 {stats['agreement_rate']:.1%} "
          f"(分歧 {stats['disagree']} 不确定 {stats['uncertain']} 缺失 {stats['missing']})")
    return {
        "_meta": {
            "novel": cfg["novel"], "author": cfg["author"],
            "version": "silver-1.0",
            "tier": "silver",
            "provenance": "双模型共识(DeepSeek×Qwen-Max,prompt 视角 A/B),未经人工;"
                          "仅保留 parent+tier 双字段一致条目",
            "annotation_rules": ["只标注直接父级,不跳级",
                                 "双模型共识,不一致/不确定即丢弃"],
            "total_locations": len(kept),
            "consensus_stats": stats,
        },
        "locations": kept,
    }


async def amain() -> int:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_DIR / ".env", override=True)
    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        sys.exit("DASHSCOPE_API_KEY 未设置")
    import sqlite3

    conn = sqlite3.connect(
        f"file:{Path.home() / '.ai-reader-v2' / 'data.db'}?mode=ro", uri=True)
    cost_acc = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    try:
        for slug, cfg in TARGETS.items():
            doc = await annotate_novel(conn, slug, cfg, cost_acc)
            out = _REPO_ROOT / "backend" / "tests" / "fixtures" / cfg["fixture"]
            out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            print(f"[{slug}] 已写 {out.name}: {doc['_meta']['total_locations']} 条")
    finally:
        conn.close()
    print(f"[annotate] done, 成本 ≈ ${cost_acc['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
