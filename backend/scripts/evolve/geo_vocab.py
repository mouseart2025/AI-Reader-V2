"""GeoEvolve 阶段 1 —— 词表/字典级 ACE 算子（geo supplement 字典增量进化）。

设计见 backend/scripts/evolve/README.md。要点：
  - 变异面：geo_resolver._SUPPLEMENT_GEO（经文件末尾定界块 update，不碰手 curated 字面量）
  - 单一事实源：vocab_delta.json（已提交 delta 集合）；源文件是其确定性渲染产物
  - 原料：仅运行时产物（冻结 DB location_parents + _find_resolved_ancestor 祖先坐标
    + chapter_facts 频次）；anti-hack 黄金集原文检测（§6.3）
  - Curator：重复/冲突/语义冲突（泛称不该有坐标）/形态 逐条剔除
  - APPLY 隔离：渲染-评估-finally 还原；启动时按 delta 重渲染自愈脏状态
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_EVOLVE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _EVOLVE_DIR.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_BACKEND_DIR / "scripts"), str(_EVOLVE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GEO_RESOLVER_PATH = _BACKEND_DIR / "src" / "services" / "geo_resolver.py"
DELTA_PATH = _EVOLVE_DIR / "vocab_delta.json"
REAL_DB = Path.home() / ".ai-reader-v2" / "data.db"

# 五本小说（与 quality_dashboard.NOVELS 一致；西游/红楼 id 冻结）
NOVELS: list[tuple[str, str, str | None]] = [
    ("xiyouji", "西游记", "3b2ef56c-1a55-466a-a7d1-34272446a198"),
    ("honglou", "红楼梦", "c384901a-8b71-437a-af35-b5ec1c56c696"),
    ("shuihu", "水浒传", None),
    ("sanguo", "三国演义", None),
    ("fengshen", "封神演义", None),
]
INNER_SLUGS = ("xiyouji", "honglou", "shuihu")
HOLDOUT_SLUGS = ("sanguo", "fengshen")

# 源文件定界块标记（渲染/剥离的唯一依据）
BLOCK_BEGIN = "# ── GeoEvolve delta begin（自动生成，勿手改）──"
BLOCK_END = "# ── GeoEvolve delta end ──"

_GOLDEN_GLOB = "backend/tests/fixtures/golden_standard_*.json"


# ── delta 存储（单一事实源）────────────────────────────────────────

def load_delta(path: Path = DELTA_PATH) -> dict:
    """加载已提交 delta。返回 {version, updated_at, entries, rejected}。"""
    if not path.exists():
        return {"version": 1, "updated_at": None, "entries": {}, "rejected": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("entries", {})
    data.setdefault("rejected", {})
    return data


def mark_rejected(store: dict, names: list[str], reason: str,
                  path: Path = DELTA_PATH) -> None:
    """把被 anti-hack / 人工剔除的名字记入 rejected（后续轮不再提议）。"""
    for name in names:
        store.setdefault("rejected", {})[name] = {
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    save_delta(store, path)


def save_delta(store: dict, path: Path = DELTA_PATH) -> None:
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def committed_coords(store: dict) -> dict[str, tuple[float, float]]:
    """已提交 delta 的 name → (lat, lng) 视图。"""
    return {name: tuple(e["coords"]) for name, e in store.get("entries", {}).items()}


# ── 源文件渲染 / 剥离（APPLY 隔离的核心）────────────────────────────

def render_block(entries: dict[str, tuple[float, float]]) -> str:
    """把 delta 集合渲染为定界块（确定性：按名字排序）。"""
    lines = [BLOCK_BEGIN, "_SUPPLEMENT_GEO.update({"]
    for name in sorted(entries):
        lat, lng = entries[name]
        lines.append(f'    {json.dumps(name, ensure_ascii=False)}: ({lat}, {lng}),')
    lines += ["})", BLOCK_END, ""]
    return "\n".join(lines)


def strip_evolve_block(source: str) -> str:
    """剥离定界块，还原 pristine 源（无块时原样返回）。

    只做定界块删除，不做任何其它规范化 —— 保证 strip(render(x)) 与 x 逐字节一致。
    """
    pattern = re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?"
    return re.sub(pattern, "", source, flags=re.DOTALL)


def render_source(pristine: str, entries: dict[str, tuple[float, float]]) -> str:
    """pristine 源 + delta 块（空集合 = pristine 本身）。"""
    pristine = strip_evolve_block(pristine)
    if not entries:
        return pristine
    if not pristine.endswith("\n"):
        pristine += "\n"
    return pristine + render_block(entries)


def write_source_state(entries: dict[str, tuple[float, float]],
                       path: Path = GEO_RESOLVER_PATH) -> None:
    """把源文件重渲染为指定 delta 状态（原子写：先临时文件再替换）。"""
    pristine = strip_evolve_block(path.read_text(encoding="utf-8"))
    content = render_source(pristine, entries)
    tmp = path.with_suffix(".py.evolve-tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def heal_source(store: dict, path: Path = GEO_RESOLVER_PATH) -> bool:
    """启动自愈：源文件偏离已提交状态时重渲染。返回是否发生了修复。"""
    current = path.read_text(encoding="utf-8")
    expected = render_source(strip_evolve_block(current), committed_coords(store))
    if current != expected:
        write_source_state(committed_coords(store), path)
        return True
    return False


# ── 运行时原料：层级名 / 频次 / 未解析池 ────────────────────────────

def open_db_readonly(db_path: Path = REAL_DB) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def resolve_novel_ids(conn: sqlite3.Connection) -> dict[str, str]:
    """slug → novel_id（西游/红楼冻结 id，其余按 title 唯一解析）。"""
    out = {}
    for slug, title, fixed_id in NOVELS:
        if fixed_id:
            out[slug] = fixed_id
            continue
        rows = conn.execute("SELECT id FROM novels WHERE title=?", (title,)).fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"title={title!r} 匹配 {len(rows)} 行，无法唯一解析")
        out[slug] = rows[0][0]
    return out


def load_location_parents(conn: sqlite3.Connection, novel_id: str) -> dict:
    """world_structures.location_parents（生产层级，冻结 DB 只读）。"""
    row = conn.execute(
        "SELECT structure_json FROM world_structures WHERE novel_id=?", (novel_id,)
    ).fetchone()
    if not row:
        raise RuntimeError(f"world_structures 无 novel_id={novel_id} 行")
    ws = json.loads(row[0])
    return ws.get("location_parents") or {}


def load_name_frequency(conn: sqlite3.Connection, novel_id: str) -> dict[str, int]:
    """chapter_facts.locations[] 里每个地名的出现章数（提议排序用）。"""
    freq: dict[str, int] = {}
    for (fact_json_text,) in conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
    ):
        try:
            fact = json.loads(fact_json_text)
        except Exception:
            continue
        seen_in_chapter = set()
        for loc in fact.get("locations") or []:
            name = (loc.get("name") or "").strip()
            if name:
                seen_in_chapter.add(name)
        for name in seen_in_chapter:
            freq[name] = freq.get(name, 0) + 1
    return freq


def compute_novel_geo(conn: sqlite3.Connection, novel_id: str, resolver) -> dict:
    """单本 geo 解析快照：names/resolved/unresolved/rate/pool(有已解祖先的未解析名)。

    resolver 为 GeoResolver 实例（调用方保证索引已加载；索引解析耗时一次性 ~5s）。
    """
    from src.services.geo_resolver import _find_resolved_ancestor

    lp = load_location_parents(conn, novel_id)
    names = sorted(set(lp) | {p for p in lp.values() if p})
    if not names:
        return {"names": 0, "resolved": 0, "unresolved_rate": None, "pool": [],
                "resolved_map": {}, "parent_map": lp}
    resolved = resolver.resolve_names(names, lp)
    unresolved = [n for n in names if n not in resolved]
    pool = [n for n in unresolved
            if _find_resolved_ancestor(n, lp, resolved) is not None]
    return {
        "names": len(names),
        "resolved": len(resolved),
        "unresolved_rate": len(unresolved) / len(names),
        "pool": pool,
        "resolved_map": resolved,
        "parent_map": lp,
    }


# ── Curator（ACE 去重/冲突检查，逐条）────────────────────────────────

def curator_check(name: str, coords: tuple[float, float], *,
                  existing: dict, committed: dict[str, tuple[float, float]],
                  genre: str | None = None) -> str | None:
    """检查一条候选 delta；返回剔除原因（None = 通过）。

    existing：geo_resolver 现有字典（_SUPPLEMENT_GEO/_SUPPLEMENT_CN 的并集视图）。
    """
    if len(name) < 2:
        return "形态：单字名"
    if name in existing:
        return "重复：已在现有 supplement 字典"
    if name in committed:
        if tuple(committed[name]) == tuple(coords):
            return "重复：已在已提交 delta"
        return "冲突：已提交 delta 坐标不同，拒绝覆盖（需人工）"
    from src.extraction.fact_validator import _is_generic_location

    if _is_generic_location(name, genre) is not None:
        return "语义冲突：泛称/非地点不应入坐标字典"
    return None


def load_golden_texts(repo_root: Path = _REPO_ROOT) -> str:
    """全部 golden fixture 原文拼接（anti-hack 包含检测用）。"""
    import glob as glob_mod

    texts = []
    for p in sorted(glob_mod.glob(str(repo_root / _GOLDEN_GLOB))):
        texts.append(Path(p).read_text(encoding="utf-8"))
    return "\n".join(texts)


def anti_hack_filter(entries: dict[str, tuple[float, float]],
                     golden_text: str) -> tuple[dict, list[str]]:
    """§6.3 黄金集硬编码检测：名字出现在 golden fixture 原文的条目剔除。

    返回 (通过的 entries, 被剔除的名字列表)。
    """
    kept, rejected = {}, []
    for name, coords in entries.items():
        if name in golden_text:
            rejected.append(name)
        else:
            kept[name] = coords
    return kept, rejected


# ── 规则提议算子（ACE：增量 delta，禁止整表重写）─────────────────────

def find_ancestor_name(name: str, parent_map: dict,
                       resolved: dict) -> str | None:
    """沿 parent 链找第一个已解祖先的名字（provenance 用；与生产函数同语义）。"""
    current = parent_map.get(name)
    visited = {name}
    depth = 0
    while current and depth < 20:
        if current in resolved:
            return current
        if current in visited:
            break
        visited.add(current)
        current = parent_map.get(current)
        depth += 1
    return None


class GeoSupplementDeltaOperator:
    """每轮为目标小说提议 top-K 条 supplement 增量（名字 → 已解祖先坐标）。

    用法：每代 analyze 阶段构建一次（候选池随已提交 delta 收缩）。
    """

    name = "geo_supplement_delta"

    def __init__(self, *, batch_size: int = 10, db_path: Path = REAL_DB):
        self.batch_size = batch_size
        self.db_path = db_path

    def build_pools(self, store: dict, resolver=None,
                    conn: sqlite3.Connection | None = None) -> dict[str, dict]:
        """各小说剩余候选池（未解析 − 已提交 delta − Curator 剔除），按频次降序。"""
        from src.services.geo_resolver import (
            _SUPPLEMENT_CN,
            _SUPPLEMENT_GEO,
            _find_resolved_ancestor,
        )

        existing = {**_SUPPLEMENT_GEO, **_SUPPLEMENT_CN}
        committed = committed_coords(store)
        rejected_names = set(store.get("rejected", {}))
        own_conn = conn is None
        if own_conn:
            conn = open_db_readonly(self.db_path)
        if resolver is None:
            from src.services.geo_resolver import GeoResolver

            resolver = GeoResolver(dataset_key="cn")
            resolver._load_index()
        try:
            ids = resolve_novel_ids(conn)
            pools: dict[str, dict] = {}
            for slug, _title, _ in NOVELS:
                snap = compute_novel_geo(conn, ids[slug], resolver)
                freq = load_name_frequency(conn, ids[slug])
                # 已提交 delta 视为已解（坐标已知）
                resolved_eff = dict(snap["resolved_map"])
                resolved_eff.update(committed)
                genre = None  # location_parents 不含 genre；generic 检查用 None 保守口径
                candidates = []
                curato_rejected: dict[str, str] = {}
                for n in snap["pool"]:
                    if n in committed or n in rejected_names:
                        continue
                    coords = _find_resolved_ancestor(n, snap["parent_map"], resolved_eff)
                    if coords is None:
                        continue
                    reason = curator_check(n, coords, existing=existing,
                                           committed=committed, genre=genre)
                    if reason:
                        curato_rejected[n] = reason
                        continue
                    candidates.append({"name": n, "coords": coords,
                                       "ancestor": find_ancestor_name(
                                           n, snap["parent_map"], resolved_eff),
                                       "frequency": freq.get(n, 0)})
                candidates.sort(key=lambda c: (-c["frequency"], c["name"]))
                pools[slug] = {
                    "unresolved_rate": snap["unresolved_rate"],
                    "names": snap["names"],
                    "pool_size": len(candidates),
                    "candidates": candidates,
                    "curator_rejected": curato_rejected,
                }
            return pools
        finally:
            if own_conn:
                conn.close()

    def __call__(self, genome: dict, context: dict) -> dict:
        """OPERATORS 协议：(genome, context) → candidate。context 需带 pools。"""
        pools = context.get("pools") or {}
        # 目标小说：剩余池最大者（确定顺序按 NOVELS 次序破平）
        best_slug, best = None, None
        for slug, _t, _ in NOVELS:
            p = pools.get(slug)
            if p and p["pool_size"] > 0 and (best is None or p["pool_size"] > best["pool_size"]):
                best_slug, best = slug, p
        if best is None:
            return {
                "operator": self.name,
                "hypothesis": "候选池已穷尽（所有有已解祖先的未解析名均已提议）",
                "genome_diff": {},
                "exhausted": True,
            }
        batch = sorted(best["candidates"],
                       key=lambda c: (-c["frequency"], c["name"]))[: self.batch_size]
        add = {c["name"]: list(c["coords"]) for c in batch}
        return {
            "operator": self.name,
            "hypothesis": (
                f"为《{best_slug}》补充 {len(add)} 条 supplement 坐标"
                f"（池余 {best['pool_size']}），预计 {best_slug}.geo.unresolved_rate "
                f"下降约 {len(add) / max(best['names'], 1):.4f}；其余小说不变。"
            ),
            "genome_diff": {"vocab_delta.add": add, "target_novel": best_slug},
            "target_novel": best_slug,
            "frequencies": {c["name"]: c["frequency"] for c in batch},
            "ancestors": {c["name"]: c["ancestor"] for c in batch},
        }
