"""LLM 增量实体消解 (Epic 2, FR-2.1–FR-2.4)。

聚合期的两阶段实体消解:

1. FR-2.1 embedding blocking — 对人物名生成 embedding,余弦阈值 + top-k
   近邻产出候选簇;只有簇内名字才会进入 LLM 判定,LLM 调用量与人物数
   近似线性(非平方,NFR-2)。
2. FR-2.2 LLM 批量聚类判定 — 对每个候选簇做一次批量 in-context 聚类
   prompt,输出合并分组 + 理由;决策落审计日志(JSONL,含输入簇/输出
   分组/理由/prompt 版本,NFR-5),并写入与 v072 entity_overrides 兼容的
   存储(override_type="llm_merge"),与手动合并走同一条 override 通道,
   可被 DELETE 撤销。

安全约束 (FR-2.3):
- alias_safety_level()==0 的名字永不参与合并(例外:entity_dictionary 中
  freq≥10 的 person 主实体 — 沿用 alias_resolver 的 dict-primary 晋升规则,
  例如「观音菩萨」虽命中称谓后缀但确实是独立人物)。
- level 1(存疑)名字仅作为提示出现在 prompt 中,不会被并入任何组。
- 防桥接约束(阮小二≠阮小七类,name_authority.similar_name_conflict)在
  LLM 决策校验与 override 应用两层都生效。
- canonical 原文锚定(canonical-name 污染防线):chapter_facts 的名字是
  LLM 产物,拼接名/幻觉名可能混入候选。LLM 给出的 canonical 必须在全书
  原文中可定位(词典型名字是原文子串,走快速路径);不 grounded 时改选
  组内 grounded 成员,全组不 grounded 则拒绝该组并记决策日志。

优先级 (FR-2.4): 手动 merge/split/rename 优先级高于 LLM 决策
(alias_resolver._apply_user_overrides 先应用 llm_merge 再应用手动条目);
被手动 override 锁定的名字不参与 LLM 判定;已决策的名字(skips rebuild
重跑)不会重复调用 LLM — 决策随 entity_overrides 持久化,survives-rebuild。

开关: config.ENTITY_RESOLUTION_ENABLED(默认开)。关闭时 resolve_novel
为 no-op,行为与 v0.73 一致 (NFR-3)。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.infra import config
from src.services import name_authority

logger = logging.getLogger(__name__)

PROMPT_VERSION = "er-cluster-v1"

# 决策审计日志(JSONL,每行一条簇决策)— 与 audit_reports 其他产物同目录。
AUDIT_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "audit_reports" / "entity_resolution_log.jsonl"
)

# dict-primary 晋升阈值(与 alias_resolver._build_merged 的 person 晋升规则一致):
# level-0 但 pre-scan 判定为高频 person 的名字是具体人物,可参与合并。
_DICT_PRIMARY_MIN_FREQ = 10

EmbedFn = Callable[[list[str]], list[list[float]]]


# ── Embedding blocking (FR-2.1) ────────────────────────────────


def default_embed_fn(texts: list[str]) -> list[list[float]]:
    """默认 embedding:复用 embedding_service 的 ChromaDB embedding function。"""
    from src.services import embedding_service

    fn = embedding_service._get_embed_fn()
    return [list(map(float, vec)) for vec in fn(texts)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class _ClusterUF:
    """最小 Union-Find,用于把 top-k 近邻边聚成候选簇。"""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_candidate_clusters(
    names: list[str],
    embed_fn: EmbedFn,
    threshold: float | None = None,
    top_k: int | None = None,
    max_cluster_size: int | None = None,
) -> list[list[str]]:
    """余弦阈值 + top-k 近邻产候选簇(FR-2.1)。

    每个名字只与 top-k 个相似度 ≥ threshold 的近邻连边,传递闭包成簇;
    只返回 size ≥ 2 的簇(簇外不做 LLM 判定)。簇大小超过
    max_cluster_size 时按成员的平均近邻相似度截断,保证 prompt 有界。
    """
    threshold = config.ER_SIMILARITY_THRESHOLD if threshold is None else threshold
    top_k = config.ER_TOP_K if top_k is None else top_k
    max_cluster_size = (
        config.ER_MAX_CLUSTER_SIZE if max_cluster_size is None else max_cluster_size
    )

    names = sorted(set(names))
    if len(names) < 2:
        return []

    vectors = embed_fn(names)
    if len(vectors) != len(names):
        raise ValueError("embed_fn must return one vector per name")

    uf = _ClusterUF()
    strength: dict[str, float] = {n: 0.0 for n in names}
    edges = 0
    for i, name in enumerate(names):
        sims: list[tuple[float, int]] = []
        for j, other in enumerate(names):
            if i == j:
                continue
            sims.append((_cosine(vectors[i], vectors[j]), j))
        sims.sort(key=lambda x: (-x[0], names[x[1]]))
        for sim, j in sims[:top_k]:
            if sim < threshold:
                break
            uf.union(name, names[j])
            strength[name] += sim
            edges += 1

    if edges == 0:
        return []

    clusters: dict[str, list[str]] = {}
    for name in names:
        clusters.setdefault(uf.find(name), []).append(name)

    result: list[list[str]] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        # 截断超大簇:保留平均近邻相似度最高的成员(prompt 有界,NFR-2)。
        if len(members) > max_cluster_size:
            members.sort(key=lambda n: (-strength.get(n, 0.0), n))
            members = members[:max_cluster_size]
        result.append(sorted(members))
    result.sort(key=lambda c: (c[0], len(c)))
    return result


# ── 候选收集与过滤 (FR-2.3) ─────────────────────────────────────


def name_merge_eligibility(
    name: str,
    *,
    dict_person_freq: int = 0,
) -> tuple[bool, str]:
    """判定名字是否可参与 LLM 合并。

    返回 (eligible, role):
    - ("merge", ...)   level 2 安全名,或可晋升的 dict 高频 person 主实体
    - ("hint", ...)    level 1 存疑名 — 仅作提示出现在 prompt,不参与合并
    - (False, "block") level 0 且非晋升主实体 — 永不参与合并 (FR-2.3)
    """
    level = name_authority.alias_safety_level(name)
    if level >= 2:
        return True, "merge"
    if level == 1:
        return True, "hint"
    if dict_person_freq >= _DICT_PRIMARY_MIN_FREQ:
        # dict-primary 晋升:pre-scan 判定的高频 person 实体(如 观音菩萨),
        # 不是泛称桥接词,允许参与合并。
        return True, "merge"
    return False, "block"


async def collect_person_names(novel_id: str) -> dict[str, dict[str, Any]]:
    """聚合期收集人物名:entity_dictionary (person) ∪ chapter_facts 人物。

    返回 {name: {"freq": int, "dict_person_freq": int,
                 "in_dict": bool, "grounded": bool}}。

    grounded 是原文锚定标志(canonical-name 污染防线,B2):词典型名字
    是原文子串,天然 grounded(快速路径,不扫全文);仅出现在
    chapter_facts 的名字是 LLM 产物,需在全书 corpus 中可定位才算
    grounded。corpus 复用 hallucination_filter._get_corpus(构建一次
    并缓存),不逐名/逐组重建。
    """
    from src.db.sqlite_db import get_connection
    from src.extraction.fact_validator import _normalize_char_variants

    meta: dict[str, dict[str, Any]] = {}
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            """
            SELECT name, frequency, entity_type
            FROM entity_dictionary
            WHERE novel_id = ?
            """,
            (novel_id,),
        )
        dict_rows = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id = ?",
            (novel_id,),
        )
        fact_rows = await cursor.fetchall()
    finally:
        await conn.close()

    for row in dict_rows:
        name = _normalize_char_variants(row["name"] or "")
        if not name:
            continue
        entry = meta.setdefault(
            name,
            {"freq": 0, "dict_person_freq": 0, "in_dict": False,
             "grounded": True},
        )
        entry["in_dict"] = True
        freq = row["frequency"] or 0
        entry["freq"] = max(entry["freq"], freq)
        if (row["entity_type"] or "") == "person":
            entry["dict_person_freq"] = max(entry["dict_person_freq"], freq)

    for row in fact_rows:
        try:
            data = json.loads(row["fact_json"])
        except Exception:
            continue
        for char in data.get("characters", []):
            name = _normalize_char_variants(char.get("name", ""))
            if not name:
                continue
            entry = meta.setdefault(
                name,
                {"freq": 0, "dict_person_freq": 0, "in_dict": False,
                 "grounded": True},
            )
            entry["freq"] += 1

    # B2: 仅 chapter_facts 来源的名字做全书原文锚定;与
    # hallucination_filter 一致 — corpus 为空(无法校验)或名字太短
    # (子串匹配不可靠)时保留。
    unverified = [n for n, m in meta.items() if not m["in_dict"]]
    if unverified:
        from src.services import hallucination_filter

        _count, corpus = await hallucination_filter._get_corpus(novel_id)
        if corpus:
            for n in unverified:
                meta[n]["grounded"] = (
                    len(n) < hallucination_filter.MIN_VERIFIABLE_LEN
                    or n in corpus
                )

    return meta


def _name_grounded(name: str, name_meta: dict[str, dict[str, Any]]) -> bool:
    """名字是否有原文锚定(collect_person_names 已预算;词典型快速路径)。"""
    meta = name_meta.get(name) or {}
    if meta.get("in_dict"):
        return True
    return bool(meta.get("grounded", True))


def partition_candidates(
    name_meta: dict[str, dict[str, Any]],
    locked_names: set[str] | None = None,
    decided_names: set[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """把收集到的名字分为 (mergeable, hints, blocked)。

    locked_names(手动 override 锁定,FR-2.4)与 decided_names(已有 LLM
    决策,survives-rebuild 增量)被排除在候选之外。
    """
    locked = locked_names or set()
    decided = decided_names or set()
    mergeable: list[str] = []
    hints: list[str] = []
    blocked: list[str] = []
    for name in sorted(name_meta):
        if name in locked or name in decided:
            continue
        eligible, role = name_merge_eligibility(
            name, dict_person_freq=name_meta[name].get("dict_person_freq", 0)
        )
        if not eligible:
            blocked.append(name)
        elif role == "hint":
            hints.append(name)
        else:
            mergeable.append(name)
    return mergeable, hints, blocked


# ── LLM 批量聚类判定 (FR-2.2) ──────────────────────────────────

_CLUSTER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "members": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["canonical", "members", "reason"],
            },
        }
    },
    "required": ["groups"],
}

_SYSTEM_PROMPT = (
    "你是小说人物实体消解专家。给定一组候选人物名字(可能指向同一人物的不同称呼,"
    "也可能是不同人物),判断哪些名字指向同一人物。\n"
    "规则:\n"
    "1. 只有确信指向同一人物的名字才能放进同一组;拿不准就分开。\n"
    "2. 名字结构相似但排行不同(如 阮小二/阮小五/阮小七)是不同人物,绝不能合并。\n"
    "3. 每组给出 canonical(最规范的称呼)与简短理由。\n"
    "4. 标注「(存疑)」的名字仅供参考,不要把它放进任何组。\n"
    "5. 每个名字最多出现在一个组;只输出包含 2 个及以上名字的组。"
)


def build_cluster_prompt(cluster: list[str], hints: list[str] | None = None) -> str:
    """构造单个候选簇的批量 in-context 聚类 prompt。"""
    lines = ["请对以下候选人物名字做实体消解聚类:\n"]
    for name in cluster:
        lines.append(f"- {name}")
    for hint in hints or []:
        lines.append(f"- {hint} (存疑)")
    lines.append(
        '\n按 JSON 输出: {"groups": [{"canonical": "...", "members": [...], "reason": "..."}]}'
    )
    return "\n".join(lines)


def validate_groups(
    cluster: list[str],
    groups: list[dict],
    name_meta: dict[str, dict[str, Any]],
) -> tuple[list[dict], list[dict]]:
    """校验 LLM 输出的分组 (FR-2.3)。

    拒绝:
    - 成员不在候选簇内 / 重复出现 / canonical 不在成员中
    - 含 level-0 非晋升名字(hard-block,即使 LLM 判定应并也不并)
    - 组内存在防桥接冲突对(阮小二≠阮小七类)
    - 含 level-1 存疑名(仅提示,不合并)
    返回 (accepted_groups, rejected_groups_with_reason)。
    """
    cluster_set = set(cluster)
    seen: set[str] = set()
    accepted: list[dict] = []
    rejected: list[dict] = []

    for group in groups or []:
        members = [m for m in (group.get("members") or []) if isinstance(m, str)]
        canonical = group.get("canonical") or ""
        reason = group.get("reason") or ""

        def _reject(why: str) -> None:
            rejected.append({**group, "rejected_reason": why})

        if len(members) < 2:
            continue  # 单名组无需决策,忽略
        if any(m not in cluster_set for m in members):
            _reject("member outside candidate cluster")
            continue
        if canonical not in members:
            _reject("canonical not in members")
            continue
        if any(m in seen for m in members):
            _reject("member appears in multiple groups")
            continue
        blocked = [
            m for m in members
            if name_merge_eligibility(
                m, dict_person_freq=name_meta.get(m, {}).get("dict_person_freq", 0)
            ) != (True, "merge")
        ]
        if blocked:
            _reject(f"hard-block/soft-hint member(s) not mergeable: {blocked}")
            continue
        conflict_pair = next(
            (
                (a, b)
                for i, a in enumerate(members)
                for b in members[i + 1:]
                if name_authority.similar_name_conflict(a, b)
            ),
            None,
        )
        if conflict_pair:
            _reject(
                f"similar-name conflict (anti-bridging): "
                f"{conflict_pair[0]} vs {conflict_pair[1]}"
            )
            continue

        seen.update(members)
        accepted.append(
            {"canonical": canonical, "members": sorted(members), "reason": reason}
        )
    return accepted, rejected


# ── 审计日志 (NFR-5) ───────────────────────────────────────────


def write_decision_log(entry: dict, log_path: Path | None = None) -> Path:
    """追加一条簇决策到 JSONL 审计日志。"""
    path = log_path or AUDIT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        **entry,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


# ── 手动锁定 / 已决策名字 ───────────────────────────────────────


def locked_names_from_overrides(overrides: list[dict]) -> set[str]:
    """手动 override 锁定的名字 (FR-2.4) — LLM 不得再对它们做决策。"""
    locked: set[str] = set()
    for ov in overrides:
        t = ov.get("override_type")
        j = ov.get("override_json") or {}
        if t == "llm_merge":
            continue
        if t == "alias_merge":
            locked.update(j.get("members", []))
            if j.get("canonical"):
                locked.add(j["canonical"])
        elif t == "alias_split":
            locked.update(j.get("aliases", []))
            if j.get("source"):
                locked.add(j["source"])
            if j.get("to"):
                locked.add(j["to"])
        elif t == "entity_rename":
            locked.add(ov.get("override_key") or "")
            if j.get("to"):
                locked.add(j["to"])
    locked.discard("")
    return locked


def decided_names_from_overrides(overrides: list[dict]) -> set[str]:
    """已被 LLM 决策覆盖的名字 — 重跑时跳过(增量,survives-rebuild)。"""
    decided: set[str] = set()
    for ov in overrides:
        if ov.get("override_type") != "llm_merge":
            continue
        j = ov.get("override_json") or {}
        decided.update(j.get("members", []))
        if j.get("canonical"):
            decided.add(j["canonical"])
    decided.discard("")
    return decided


# ── 主流程 ────────────────────────────────────────────────────


async def _record_llm_cost(usage: Any) -> None:
    """把 LLM 调用计入 cost_service 月度账本 (NFR-2)。

    仅云端 provider 记账(与 analysis_service 一致,本地 Ollama 无费用);
    失败不阻塞主流程。
    """
    try:
        if config.LLM_PROVIDER == "ollama":
            return
        from src.infra.config import get_model_name
        from src.services.cost_service import add_monthly_usage, get_pricing

        if usage is None:
            return
        input_price, output_price = get_pricing(get_model_name())
        spent_usd = (
            (usage.prompt_tokens / 1_000_000) * input_price
            + (usage.completion_tokens / 1_000_000) * output_price
        )
        await add_monthly_usage(
            round(spent_usd, 4),
            round(spent_usd * 7.2, 2),
            usage.prompt_tokens,
            usage.completion_tokens,
        )
    except Exception as exc:  # pragma: no cover - 记账失败不影响消解
        logger.debug("entity_resolver cost recording skipped: %s", exc)


async def resolve_cluster(
    novel_id: str,
    cluster: list[str],
    name_meta: dict[str, dict[str, Any]],
    llm: Any,
    hints: list[str] | None = None,
    log_path: Path | None = None,
    record_cost: bool = True,
) -> dict:
    """对单个候选簇调用 LLM 聚类,返回校验后的决策并落审计日志。"""
    prompt = build_cluster_prompt(cluster, hints)
    content, usage = await llm.generate(
        _SYSTEM_PROMPT, prompt, format=_CLUSTER_SCHEMA
    )
    if record_cost:
        await _record_llm_cost(usage)

    raw_groups = content.get("groups", []) if isinstance(content, dict) else []
    accepted, rejected = validate_groups(cluster, raw_groups, name_meta)
    # B3: 决策日志带原文锚定标志,便于事后复核 canonical 污染。
    for g in accepted:
        g["grounded"] = {m: _name_grounded(m, name_meta) for m in g["members"]}

    decision = {
        "novel_id": novel_id,
        "input_cluster": cluster,
        "hints": hints or [],
        "output_groups": accepted,
        "rejected_groups": rejected,
        "llm_raw_groups": raw_groups,
    }
    write_decision_log(decision, log_path)
    return decision


async def resolve_novel(
    novel_id: str,
    llm: Any | None = None,
    embed_fn: EmbedFn | None = None,
    log_path: Path | None = None,
) -> dict:
    """聚合期实体消解主流程 (FR-2.1–2.4)。

    返回运行报告 {enabled, candidates, clusters, llm_calls, merges, skipped}。
    LLM 决策写入 entity_overrides (override_type="llm_merge"),与手动合并
    同一通道;随后使聚合/alias 缓存失效,下一次 build_alias_map 即生效。
    """
    if not config.ENTITY_RESOLUTION_ENABLED:
        logger.info("Entity resolution disabled (ENTITY_RESOLUTION_ENABLED=false)")
        return {"enabled": False, "candidates": 0, "clusters": 0,
                "llm_calls": 0, "merges": 0}

    from src.db import entity_override_store
    from src.services import alias_resolver
    from src.infra.llm_client import get_llm_client

    llm = llm or get_llm_client()
    embed_fn = embed_fn or default_embed_fn

    name_meta = await collect_person_names(novel_id)
    overrides = await entity_override_store.load_overrides(novel_id)
    locked = locked_names_from_overrides(overrides)
    decided = decided_names_from_overrides(overrides)
    mergeable, hints, _blocked = partition_candidates(name_meta, locked, decided)

    clusters = build_candidate_clusters(mergeable, embed_fn)

    report = {
        "enabled": True,
        "candidates": len(mergeable),
        "clusters": len(clusters),
        "llm_calls": 0,
        "merges": 0,
        "locked": sorted(locked),
        "skipped_decided": sorted(decided),
    }
    if not clusters:
        return report

    # 当前 auto map 快照,供 override 冲突检测 (FR7 沿用)。
    auto_map = await alias_resolver.build_alias_map(novel_id)
    freq = {n: m.get("freq", 0) for n, m in name_meta.items()}

    merges: list[dict] = []
    # level-1 存疑名仅作提示附带(FR-2.3),带上限避免 prompt 膨胀;不参与合并。
    hint_sample = hints[:10]
    for cluster in clusters:
        decision = await resolve_cluster(
            novel_id, cluster, name_meta, llm, hints=hint_sample, log_path=log_path
        )
        report["llm_calls"] += 1
        for group in decision["output_groups"]:
            members = group["members"]
            # canonical 选择:优先 LLM 建议;否则按频率/昵称降权规则挑选。
            canonical = group["canonical"]
            if canonical not in members:
                canonical = name_authority.pick_canonical(members, freq)

            # canonical-name 污染防线 (B1):canonical 必须在全书原文中
            # 可定位 — 拼接名/幻觉名即使进了 chapter_facts,也不能成为
            # ER canonical 写进 llm_merge override 污染全书别名映射。
            canonical_grounded = _name_grounded(canonical, name_meta)
            grounded_reselected = False
            if not canonical_grounded:
                grounded_members = [
                    m for m in members if _name_grounded(m, name_meta)
                ]
                if not grounded_members:
                    logger.info(
                        "Entity resolution %s: rejected group %s — "
                        "no member is grounded in source text",
                        novel_id, members,
                    )
                    write_decision_log(
                        {
                            "novel_id": novel_id,
                            "input_cluster": cluster,
                            "output_groups": [],
                            "rejected_groups": [
                                {
                                    **group,
                                    "rejected_reason": (
                                        "no grounded member in source text "
                                        "(canonical-name pollution defense)"
                                    ),
                                }
                            ],
                        },
                        log_path,
                    )
                    continue
                # 改选:组内 grounded 成员中按 pick_canonical 规则最优者。
                canonical = name_authority.pick_canonical(grounded_members, freq)
                canonical_grounded = True
                grounded_reselected = True

            await entity_override_store.save_override(
                novel_id,
                "llm_merge",
                canonical,
                {
                    "members": members,
                    "canonical": canonical,
                    "reason": group["reason"],
                    "prompt_version": PROMPT_VERSION,
                    "input_cluster": cluster,
                    "auto_snapshot": {m: auto_map.get(m, m) for m in members},
                    # B3: 原文锚定标志,供「我的修正」/审计复核。
                    "canonical_grounded": canonical_grounded,
                    "grounded_reselected": grounded_reselected,
                },
            )
            merges.append(group)

    report["merges"] = len(merges)
    if merges:
        from src.services import entity_aggregator

        entity_aggregator.invalidate_cache(novel_id)
    logger.info(
        "Entity resolution for %s: %d candidates → %d clusters → %d merges",
        novel_id, report["candidates"], report["clusters"], report["merges"],
    )
    return report
