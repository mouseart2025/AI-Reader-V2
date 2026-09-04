"""Pass diff service: 章节级一审/二审差异引擎 (multi-pass MVP, issue #70 Epic 3 Story 3.1).

输入一章的主表 fact_json(chapter_facts)与二审 fact_json(pass_chapter_facts,
同构),输出分类差异:
- only_in_main: 仅一审有的记录
- only_in_pass: 仅二审有的记录
- different:    双方都有但字段不一致(字段级,带 category 标签)

匹配规则(spec Story 3.1):
- 实体(人物/地点/概念/物品/组织)按规范化名匹配 —— 两侧名字都先经主表
  alias_map(alias → canonical)归一,避免别名差异造成假分歧(二审独立命名
  「少年」 vs 一审归一后「杨过」经 alias_map 归一后匹配,不产生假 only_in_*);
  一审归一错误造成的真实差异会保留(归一后仍不同名 → only_in_*),这正是
  二审要暴露的。
- 事件按 (participant 集, 章节内序号邻近) 启发式匹配:参与者集合(归一后)
  有交集才可配对,贪心取重合度最高、序号最近的候选。
- 自由文本字段(summary/description/evidence 原文)不参与字段级比较 —— 两次
  独立抽取的措辞必然不同,逐字比较全是噪声;evidence 只比较「有无」
  (unresolved ↔ confirmed 语义)。

字段级 category 标签(spec 五类 + other):
- type:       类型分歧(关系类型/地点类型/事件类型/维度/概念分类/物品类型)
- identity:   指称/归属分歧(别名集、父地点、动作主体/对象)
- boundary:   事件边界分歧(参与者集、发生地点)
- temporal:   时态分歧(is_new/previous_type,关系是否本章新建立)
- resolution: unresolved ↔ confirmed(evidence 有无)
- other:      其余(appearance/importance/role 等)

幂等 + 可缓存:diff_chapter_facts 是纯函数;PassDiffService 按
(pass_id, chapter_pk, 双方内容 hash) 缓存。首次生成某章 diff 时回填
history:air_unlocked_at(diff 生成动作充当 AIR unlock,gate 语义)+
diff_counts(update_chapter_history,Story 1.2 骨架)。

不 diff 的集合:spatial_relationships / world_declarations(地图域产物,
章节级人工审查价值低,留待二期全书聚合)。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from src.db import analysis_pass_store, chapter_fact_store, chapter_store
from src.services.alias_resolver import build_alias_map

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """history 埋点时间戳(UTC, ISO 格式,与 source_pass_service 同口径)。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _content_hash(fact: dict) -> str:
    """fact 内容 hash(键序无关),作为缓存键的一部分。"""
    canonical = json.dumps(fact or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── 字段比较规格 ──────────────────────────────────
# 每项: (字段名, category, 规范化器)。规范化器把字段值转成可比较形式
# (名字经 alias_map 归一;列表按集合比较,忽略顺序与措辞差异)。


def _norm_name(alias_map: dict[str, str]):
    """名字归一器:alias_map 双向归一(两侧名字都映射到 canonical)。"""
    def _norm(v):
        if not v:
            return v
        return alias_map.get(v, v)
    return _norm


def _norm_name_set(alias_map: dict[str, str]):
    """名字列表归一器:归一后按集合比较(忽略顺序)。"""
    norm = _norm_name(alias_map)

    def _norm_set(v):
        return sorted({norm(x) for x in (v or []) if x})
    return _norm_set


def _norm_abilities(alias_map: dict[str, str]):
    """abilities_gained: 按 (dimension, name) 集合比较,忽略描述措辞。"""
    def _norm_abs(v):
        return sorted(
            (a.get("dimension", ""), a.get("name", ""))
            for a in (v or [])
            if isinstance(a, dict)
        )
    return _norm_abs


def _norm_presence(_alias_map: dict[str, str]):
    """evidence 等自由文本:只比较有无(unresolved ↔ confirmed)。"""
    def _norm_bool(v):
        return bool(v and str(v).strip())
    return _norm_bool


def _norm_plain(_alias_map: dict[str, str]):
    """原样比较(类型/枚举/布尔字段)。"""
    def _norm_id(v):
        return v
    return _norm_id


def _field_specs(collection: str, alias_map: dict[str, str]) -> list[tuple]:
    """每个集合参与字段级比较的 (field, category, normalizer) 清单。"""
    name = _norm_name(alias_map)
    name_set = _norm_name_set(alias_map)
    plain = _norm_plain(alias_map)
    presence = _norm_presence(alias_map)
    if collection == "characters":
        return [
            ("new_aliases", "identity", name_set),
            ("appearance", "other", plain),
            ("abilities_gained", "other", _norm_abilities(alias_map)),
            ("locations_in_chapter", "other", name_set),
        ]
    if collection == "relationships":
        return [
            ("relation_type", "type", plain),
            ("polarity", "type", plain),
            ("rel_subtype", "type", plain),
            ("closeness", "type", plain),
            ("is_new", "temporal", plain),
            ("previous_type", "temporal", plain),
            ("evidence", "resolution", presence),
        ]
    if collection == "locations":
        return [
            ("type", "type", plain),
            ("parent", "identity", name),
            ("role", "other", plain),
        ]
    if collection == "events":
        return [
            ("type", "type", plain),
            ("participants", "boundary", name_set),
            ("location", "boundary", name),
            ("importance", "other", plain),
            ("evidence", "resolution", presence),
        ]
    if collection == "item_events":
        return [
            ("item_type", "type", plain),
            ("actor", "identity", name),
            ("recipient", "identity", name),
        ]
    if collection == "org_events":
        return [
            ("org_type", "type", plain),
            ("role", "other", plain),
        ]
    if collection == "new_concepts":
        return [
            ("category", "type", plain),
        ]
    return []


def _field_diffs(
    main_item: dict, pass_item: dict, specs: list[tuple],
) -> list[dict]:
    """对一对已匹配的记录做字段级比较,返回差异清单。"""
    diffs = []
    for field, category, norm in specs:
        mv = norm(main_item.get(field))
        pv = norm(pass_item.get(field))
        if mv != pv:
            diffs.append({
                "field": field,
                "category": category,
                "main": main_item.get(field),
                "pass": pass_item.get(field),
            })
    return diffs


def _diff_keyed(
    collection: str,
    main_items: list[dict],
    pass_items: list[dict],
    key_fn,
    alias_map: dict[str, str],
    only_in_main: list[dict],
    only_in_pass: list[dict],
    different: list[dict],
) -> None:
    """按键匹配的集合 diff(实体类:characters/locations/concepts 等)。

    同键重复(异常数据)取首条匹配,其余按 only_in 处理,保证不漏报。
    """
    specs = _field_specs(collection, alias_map)
    pass_by_key: dict[str, list[int]] = {}
    for j, item in enumerate(pass_items):
        pass_by_key.setdefault(key_fn(item), []).append(j)
    matched_pass: set[int] = set()

    for item in main_items:
        key = key_fn(item)
        candidates = [j for j in pass_by_key.get(key, []) if j not in matched_pass]
        if not candidates:
            only_in_main.append(
                {"collection": collection, "key": key, "item": item},
            )
            continue
        j = candidates[0]
        matched_pass.add(j)
        fields = _field_diffs(item, pass_items[j], specs)
        if fields:
            different.append({
                "collection": collection,
                "key": key,
                "main": item,
                "pass": pass_items[j],
                "fields": fields,
            })

    for j, item in enumerate(pass_items):
        if j not in matched_pass:
            only_in_pass.append(
                {"collection": collection, "key": key_fn(item), "item": item},
            )


def _event_participants(event: dict, alias_map: dict[str, str]) -> frozenset:
    """事件的归一化参与者集合(匹配键的一部分)。"""
    norm = _norm_name(alias_map)
    return frozenset(norm(p) for p in event.get("participants") or [] if p)


def _diff_events(
    main_events: list[dict],
    pass_events: list[dict],
    alias_map: dict[str, str],
    only_in_main: list[dict],
    only_in_pass: list[dict],
    different: list[dict],
) -> None:
    """事件 diff:(participant 集, 章节内序号邻近) 启发式匹配。

    候选配对要求归一化参与者集合有交集(双方均无参与者时退化为序号邻近
    |i-j| <= 1);贪心按 (Jaccard 重合度降序, 序号差升序) 取配。
    """
    specs = _field_specs("events", alias_map)
    psets_m = [_event_participants(e, alias_map) for e in main_events]
    psets_p = [_event_participants(e, alias_map) for e in pass_events]

    candidates: list[tuple[float, int, int, int]] = []  # (-jaccard, |i-j|, i, j)
    for i, ps_m in enumerate(psets_m):
        for j, ps_p in enumerate(psets_p):
            if ps_m and ps_p:
                inter = len(ps_m & ps_p)
                if not inter:
                    continue
                jaccard = inter / len(ps_m | ps_p)
            else:
                # 双方都无参与者:只靠序号邻近,且不允许一方有一方空
                if ps_m != ps_p or abs(i - j) > 1:
                    continue
                jaccard = 1.0
            candidates.append((-jaccard, abs(i - j), i, j))
    candidates.sort()

    matched_m: set[int] = set()
    matched_p: set[int] = set()
    for _neg_j, _dist, i, j in candidates:
        if i in matched_m or j in matched_p:
            continue
        matched_m.add(i)
        matched_p.add(j)
        m_item, p_item = main_events[i], pass_events[j]
        fields = _field_diffs(m_item, p_item, specs)
        if fields:
            different.append({
                "collection": "events",
                "key": f"#{i + 1}↔#{j + 1}",
                "main": m_item,
                "pass": p_item,
                "main_index": i,
                "pass_index": j,
                "fields": fields,
            })

    for i, item in enumerate(main_events):
        if i not in matched_m:
            only_in_main.append({
                "collection": "events", "key": f"#{i + 1}",
                "index": i, "item": item,
            })
    for j, item in enumerate(pass_events):
        if j not in matched_p:
            only_in_pass.append({
                "collection": "events", "key": f"#{j + 1}",
                "index": j, "item": item,
            })


def diff_chapter_facts(
    main_fact: dict,
    pass_fact: dict,
    alias_map: dict[str, str] | None = None,
) -> dict:
    """比较一章的主表 fact 与二审 fact,输出分类差异(纯函数,幂等)。

    返回 {"counts": {...}, "only_in_main": [...], "only_in_pass": [...],
    "different": [...]};每条差异携带 collection + key + 原始记录
    (含 evidence 原文摘录,供前端回原文定位)。
    """
    alias_map = alias_map or {}
    main_fact = main_fact or {}
    pass_fact = pass_fact or {}

    only_in_main: list[dict] = []
    only_in_pass: list[dict] = []
    different: list[dict] = []

    norm = _norm_name(alias_map)

    _diff_keyed(
        "characters",
        main_fact.get("characters") or [],
        pass_fact.get("characters") or [],
        lambda c: norm(c.get("name") or ""),
        alias_map, only_in_main, only_in_pass, different,
    )
    # 关系按 (person_a, person_b) 无序对匹配(归一后),方向差异由字段级体现
    _diff_keyed(
        "relationships",
        main_fact.get("relationships") or [],
        pass_fact.get("relationships") or [],
        lambda r: "|".join(sorted(
            norm(r.get(k) or "") for k in ("person_a", "person_b")
        )),
        alias_map, only_in_main, only_in_pass, different,
    )
    _diff_keyed(
        "locations",
        main_fact.get("locations") or [],
        pass_fact.get("locations") or [],
        lambda loc: norm(loc.get("name") or ""),
        alias_map, only_in_main, only_in_pass, different,
    )
    _diff_events(
        main_fact.get("events") or [],
        pass_fact.get("events") or [],
        alias_map, only_in_main, only_in_pass, different,
    )
    _diff_keyed(
        "item_events",
        main_fact.get("item_events") or [],
        pass_fact.get("item_events") or [],
        lambda it: f"{norm(it.get('item_name') or '')}|{it.get('action') or ''}",
        alias_map, only_in_main, only_in_pass, different,
    )
    _diff_keyed(
        "org_events",
        main_fact.get("org_events") or [],
        pass_fact.get("org_events") or [],
        lambda o: "|".join((
            norm(o.get("org_name") or ""),
            norm(o.get("member") or ""),
            o.get("action") or "",
        )),
        alias_map, only_in_main, only_in_pass, different,
    )
    _diff_keyed(
        "new_concepts",
        main_fact.get("new_concepts") or [],
        pass_fact.get("new_concepts") or [],
        lambda c: norm(c.get("name") or ""),
        alias_map, only_in_main, only_in_pass, different,
    )

    counts = {
        "only_in_main": len(only_in_main),
        "only_in_pass": len(only_in_pass),
        "different": len(different),
    }
    return {
        "counts": counts,
        "only_in_main": only_in_main,
        "only_in_pass": only_in_pass,
        "different": different,
    }


class PassDiffService:
    """章节 diff 的装配层:读库 → 纯函数 diff → 缓存 → history 回填。"""

    def __init__(self):
        # (pass_id, chapter_pk, main_hash, pass_hash) -> diff result
        self._cache: dict[tuple, dict] = {}

    async def get_chapter_diff(self, pass_id: str, chapter_num: int) -> dict:
        """获取某章一审/二审 diff;首次生成时回填 history 埋点。

        Raises ValueError(中文消息):pass 不存在 / 章节不存在 /
        一审结果缺失 / 二审未覆盖该章。
        """
        pass_row = await analysis_pass_store.get_pass(pass_id)
        if not pass_row:
            raise ValueError("二审任务不存在")
        novel_id = pass_row["novel_id"]

        chapter = await chapter_store.get_chapter_content(novel_id, chapter_num)
        if not chapter:
            raise ValueError(f"章节不存在: 第{chapter_num}章")
        chapter_pk = chapter["id"]

        main_row = await chapter_fact_store.get_chapter_fact(novel_id, chapter_pk)
        if not main_row:
            raise ValueError(f"第{chapter_num}章没有一审分析结果,无法对比")

        pass_fact_row = await analysis_pass_store.get_pass_chapter_fact(
            pass_id, chapter_pk,
        )
        if not pass_fact_row:
            raise ValueError(f"二审尚未覆盖第{chapter_num}章")
        if pass_fact_row["status"] != "completed":
            raise ValueError(
                f"二审第{chapter_num}章未完成(状态: {pass_fact_row['status']})"
            )

        main_fact = main_row["fact"]
        pass_fact = pass_fact_row["fact"]

        cache_key = (
            pass_id, chapter_pk,
            _content_hash(main_fact), _content_hash(pass_fact),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        # 实体匹配先经主表 alias_map 双向归一(避免别名差异造成假分歧)
        alias_map = await build_alias_map(novel_id)
        result = diff_chapter_facts(main_fact, pass_fact, alias_map)

        # 首次生成 diff = AIR unlock(gate 语义):回填 air_unlocked_at
        # (仅在未解锁时)+ diff_counts(Story 1.2 history 骨架)
        chapters_hist = (pass_row.get("history_json") or {}).get("chapters") or {}
        existing = chapters_hist.get(str(chapter_num)) or {}
        await analysis_pass_store.update_chapter_history(pass_id, chapter_num, {
            "chapter_id": chapter_pk,
            "air_unlocked_at": existing.get("air_unlocked_at") or _utc_now_iso(),
            "diff_counts": {
                "air_only": result["counts"]["only_in_main"],
                "pass_only": result["counts"]["only_in_pass"],
                "different": result["counts"]["different"],
            },
        })

        payload = {
            "pass_id": pass_id,
            "novel_id": novel_id,
            "chapter": chapter_num,
            "chapter_id": chapter_pk,
            **result,
        }
        self._cache[cache_key] = payload
        return {**payload, "cached": False}


# Module-level singleton(缓存跨请求复用)
_service: PassDiffService | None = None


def get_pass_diff_service() -> PassDiffService:
    """Return module-level singleton PassDiffService."""
    global _service
    if _service is None:
        _service = PassDiffService()
    return _service
