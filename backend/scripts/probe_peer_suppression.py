"""`peers` → 父子票抑制机制，到底还有没有作用面？（可复算探针）

## 机制是什么

层级投票有两条链路，各自都会做同一件事：若某章 fact 称 `parent(child) = P`，
而 peer 集合里存在对 `{child, P}`，则给这张票打 **×0.33**：

  - `src/services/world_structure_agent.py:1455-1477`（逐章累积，`self._peer_pairs`）
  - `src/services/world_structure_agent.py:2325-2338`（重建后的票，同上）
  - `src/services/geo_skills/vote_builder.py:124-167`（`peer_discount=0.33`）

peer 对**唯一**的作用就是这个命中。所以「这个机制还有没有用」= 「peer 对与
真实投票对有没有交集」，这是可以离线量出来的，不需要跑管线。

## 2026-09-28 的结论（全库 31 本有数据的书；红楼梦 c384901a 做 LLM 实验）

| 量 | 值 |
|---|---|
| 全库 peer 字段条目 / 去重对 | 337 条 → **120 对**（分布 9 本；红楼梦 0 条） |
| 全库投票对 | 17866 |
| 全库 peer 对命中投票对 | **1**（诡秘之主；其余 30 本全 0，命中率 0.83%） |
| 红楼梦：模型新挖 raw 对 | 126 对（同章候选表 + qwen2.5:7b） |
| 其中命中投票对 | **1**（仁清巷↔葫芦庙） |
| 随机对基线命中率 | 0.18%（n=4000）→ 期望命中 0.23 对 |

命中 1/126 与随机不可区分 ⇒ 不是"数据太稀"，而是**peer 关系与"被错写成父子的
并列关系"这两批对几乎不相交**。所以「把 `peers` 喂饱就能让抑制生效」这个假设
不成立：喂饱了也没有靶子。

## 两个附加发现

1. **祖先过滤会顺带删掉唯一的靶子**。为防「并列被误标为父子」的票被误伤而加的
   `_lineal`（过滤互为祖先的对）删掉了那 1 个命中 —— 因为要抑制的错例正是
   「facts 声明为父子」的对，而过滤规则也专删「facts 声明为父子」的对。
   两者在定义上就重叠：过滤越保险，机制越无用。
2. **`peers` 提示词本身是活的**（`extraction/prompts/extraction_system.txt:120`），
   模型在不带上下文的独立提问里 15/20 会答，但进管线后几乎不填 ⇒ 该机制是
   **提示词要求 + 代码消费 + 数据长期为空**。

## 用法

    python scripts/probe_peer_suppression.py                  # 全库扫描（0 LLM 成本）
    python scripts/probe_peer_suppression.py --mine 红楼梦      # 跑 LLM 挖掘实验
    python scripts/probe_peer_suppression.py --mine 红楼梦 --model qwen2.5:7b --limit 32
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.extraction.fact_validator import _is_generic_location

GENERIC_GENRE = None


def _connect() -> sqlite3.Connection:
    from src.infra.config import DB_PATH

    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _novels(con: sqlite3.Connection) -> dict[str, str]:
    return dict(con.execute("SELECT id, title FROM novels"))


def _pairs_and_parents(
    con: sqlite3.Connection, novel_id: str
) -> tuple[set[frozenset], set[frozenset], dict[str, str], dict[str, Counter]]:
    """→ (投票对, peer 对, name→首个 parent, name→同章共现计数)"""
    rows = con.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ).fetchall()

    votes: set[frozenset] = set()
    peers: set[frozenset] = set()
    parents: dict[str, str] = {}
    cooc: dict[str, Counter] = {}
    for (fj,) in rows:
        try:
            d = json.loads(fj)
        except (TypeError, ValueError):
            continue
        names: list[str] = []
        for loc in d.get("locations") or []:
            name = (loc.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            parent = (loc.get("parent") or "").strip()
            if parent.lower() in ("none", "null"):
                parent = ""
            if parent and parent != name:
                if not (
                    _is_generic_location(name, GENERIC_GENRE)
                    or _is_generic_location(parent, GENERIC_GENRE)
                ):
                    votes.add(frozenset({name, parent}))
                parents.setdefault(name, parent)
            for peer in loc.get("peers") or []:
                peer = (peer or "").strip()
                if peer and peer != name:
                    peers.add(frozenset({name, peer}))
        uniq = set(names)
        for a in uniq:
            counter = cooc.setdefault(a, Counter())
            for b in uniq:
                if a != b:
                    counter[b] += 1
    return votes, peers, parents, cooc


def scan_all(con: sqlite3.Connection) -> None:
    titles = _novels(con)
    rows = []
    for nid in titles:
        vp, pp, _, _ = _pairs_and_parents(con, nid)
        if not vp and not pp:
            continue
        rows.append((nid, len(vp), len(pp), len(pp & vp)))

    rows.sort(key=lambda r: -r[2])
    print(f"{'小说':<30}{'投票对':>7}{'peers对':>9}{'命中':>6}{'命中率':>8}")
    for nid, nv, np_, hit in rows:
        print(
            f"{titles[nid][:28]:<30}{nv:>7}{np_:>9}{hit:>6}"
            f"{hit / max(np_, 1) * 100:>7.1f}%"
        )
    tv = sum(r[1] for r in rows)
    tp = sum(r[2] for r in rows)
    th = sum(r[3] for r in rows)
    print(
        f"\n合计：投票对 {tv}，peers 对 {tp}，命中 {th} "
        f"({th / max(tp, 1) * 100:.2f}%)，涉及小说 {len(rows)} 本"
    )


# ── LLM 挖掘实验 ────────────────────────────────────────────────────────────

PEER_SYSTEM = """你为一本中文小说标注地点的「并列关系」。

peers = 与给定地点**同级别、空间相邻或左右对称**的地点。
例：宁国府 ↔ 荣国府（两府并列、对面而建）；东市 ↔ 西市（同城内对称的两个市场）。

铁律：
1. 只依据给出的【描述】与【候选并列】判断，**禁止引入这两者之外的地名**；
2. 每个 peer **必须逐字出现在该地点的【候选并列】里**；
3. 判断不了就给空数组 []；**宁可空，也不要猜**；
4. 上下级关系（父子、包含）不是并列，不要写进来。

只输出 JSON 对象，键为地点名，值为 peer 数组：
{"地点A": ["地点B"], "地点C": []}"""


def _ancestors(name: str, parents: dict[str, str]) -> set[str]:
    chain: set[str] = set()
    cur = parents.get(name)
    while cur and cur not in chain:
        chain.add(cur)
        cur = parents.get(cur)
    return chain


def _lineal(a: str, b: str, parents: dict[str, str]) -> bool:
    """互为祖先 ⇒ 不是并列（这是防误伤的守卫，也是让机制失效的原因）。"""
    return b in _ancestors(a, parents) or a in _ancestors(b, parents)


def _candidates(
    name: str, cooc: dict[str, Counter], parents: dict[str, str], cap: int = 24
) -> list[str]:
    out: list[str] = []
    for n, _ in cooc.get(name, Counter()).most_common():
        if n == name or _lineal(name, n, parents):
            continue
        out.append(n)
        if len(out) >= cap:
            break
    return out


def _build_prompt(entries: list[dict], cands: dict[str, list[str]]) -> str:
    lines = []
    for e in entries:
        pool = cands.get(e["name"]) or []
        lines.append(
            f"- {e['name']}（{e['type']}）描述：{(e['description'] or '（无描述）')[:220]}"
            f"\n  候选并列：{'、'.join(pool) if pool else '（无）'}"
        )
    return (
        "【待标注地点】\n" + "\n".join(lines) + "\n\n"
        "请为上面每一个待标注地点给出 peers（JSON 对象，键为地点名）。"
    )


def mine_experiment(
    con: sqlite3.Connection, title: str, model: str, limit: int, batch: int, cap: int
) -> None:
    from src.infra.llm_client import LLMClient, _extract_json

    titles = _novels(con)
    match = [nid for nid, t in titles.items() if t == title]
    if not match:
        match = [nid for nid, t in titles.items() if title in t]
    if not match:
        print(f"找不到小说：{title}")
        return
    nid = match[0]

    vp, pp_existing, parents, cooc = _pairs_and_parents(con, nid)
    entries_all = sorted(
        (
            {
                "name": loc_name,
                "type": loc_type,
                "description": loc_desc,
            }
            for loc_name, loc_type, loc_desc in _loc_info(con, nid)
        ),
        key=lambda e: e["name"],
    )
    known = {e["name"] for e in entries_all}
    entries = entries_all[:limit]
    cands = {e["name"]: _candidates(e["name"], cooc, parents, cap) for e in entries}

    print(f"\n=== {titles[nid]} ===")
    print(
        f"地点 {len(entries_all)}（本次取前 {len(entries)}），投票对 {len(vp)}，"
        f"库内已有 peers 对 {len(pp_existing)}"
    )

    client = LLMClient(model=model)
    raw: set[frozenset] = set()
    for i in range(0, len(entries), batch):
        chunk = entries[i : i + batch]
        content, _ = asyncio.run(
            client.generate(
                system=PEER_SYSTEM,
                prompt=_build_prompt(chunk, cands),
                format={"type": "object"},
                temperature=0.0,
                max_tokens=max(2000, 500 * len(chunk)),
            )
        )
        data = content if isinstance(content, dict) else _extract_json(str(content))
        for name, peers in (data or {}).items():
            if not isinstance(peers, list):
                continue
            for p in peers:
                p = (p or "").strip()
                if p and p != name:
                    raw.add(frozenset({name, p}))
        print(
            f"  batch {i // batch + 1}/{(len(entries) + batch - 1) // batch}: "
            f"累计 {len(raw)} 对"
        )

    known_only = {p for p in raw if all(n in known for n in p)}
    no_guard = known_only
    with_lin = {p for p in no_guard if not _lineal(*sorted(p), parents)}
    with_cand = {
        p
        for p in with_lin
        if sorted(p)[1] in cands.get(sorted(p)[0], [])
        or sorted(p)[0] in cands.get(sorted(p)[1], [])
    }

    print(f"\n{'守卫':<16}{'存活对':>8}{'命中投票对':>12}")
    for label, s in (
        ("无守卫", no_guard),
        ("+祖先过滤", with_lin),
        ("+候选白名单", with_cand),
    ):
        print(f"{label:<16}{len(s):>8}{len(s & vp):>12}")

    print(
        "\n命中明细（机制真正的作用面）：",
        sorted(map(sorted, no_guard & vp))[:20] or "无",
    )
    print(
        "被祖先过滤删掉的命中：",
        sorted(map(sorted, (no_guard & vp) - (with_lin & vp)))[:20] or "无",
    )

    all_pairs = {frozenset({a, b}) for a in known for b in known if a != b}
    rnd = random.Random(0)
    sample = rnd.sample(sorted(all_pairs), min(4000, len(all_pairs)))
    null = sum(1 for p in sample if p in vp) / max(len(sample), 1)
    print(
        f"\n随机对基线命中率 {null * 100:.2f}%（n={len(sample)}）"
        f" → 期望命中 {null * len(no_guard):.2f} 对"
    )


def _loc_info(con: sqlite3.Connection, novel_id: str):
    """(name, type, description)，同名取描述最长的一条。"""
    best: dict[str, tuple[str, str, str]] = {}
    for (fj,) in con.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ):
        for loc in json.loads(fj).get("locations") or []:
            name = (loc.get("name") or "").strip()
            if not name:
                continue
            desc = (loc.get("description") or "").strip()
            prev = best.get(name)
            if prev is None or len(desc) > len(prev[2]):
                best[name] = (name, loc.get("type") or "", desc)
    return list(best.values())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mine", metavar="TITLE", help="额外跑一次 LLM 挖掘实验")
    ap.add_argument("--model", default="qwen2.5:7b", help="Ollama 模型名")
    ap.add_argument("--limit", type=int, default=32, help="挖掘实验取前 N 个地点")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--candidates", type=int, default=24, help="每个地点的候选上限")
    args = ap.parse_args()

    con = _connect()
    scan_all(con)
    if args.mine:
        mine_experiment(
            con, args.mine, args.model, args.limit, args.batch, args.candidates
        )


if __name__ == "__main__":
    main()
