"""VoteBuilder — GeoSkill that builds parent votes from chapter facts.

Extracted from WorldStructureAgent._rebuild_parent_votes().
Reads chapter_facts from DB and produces vote counts for each location.
Also builds frequency map, chapter settings, and location-chapter mapping.

This skill always succeeds (no LLM dependency).
"""

from __future__ import annotations

import json
import logging
from collections import Counter

from src.extraction.fact_validator import _is_generic_location
from src.models.chapter_fact import classify_spatial_relation
from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.evolve_params import evolve_param
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult
from src.services.world_structure_agent import TIER_ORDER, _get_suffix_rank
from src.utils.location_names import (
    is_passage_like,
    location_alias_map_for_title,
)

logger = logging.getLogger(__name__)


class VoteBuilder(GeoSkill):
    """Build parent votes from chapter facts."""

    def __init__(self, novel_id: str, novel_title: str = ""):
        self.novel_id = novel_id
        self.novel_title = novel_title

    @property
    def name(self) -> str:
        return "投票构建"

    def _alias_map(self) -> dict[str, str]:
        """地名别名映射(geo_alias.enabled 默认开;表为空时零行为)。"""
        if not evolve_param("geo_alias.enabled", True):
            return {}
        return location_alias_map_for_title(self.novel_title)

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
                (self.novel_id,),
            )
            rows = await cursor.fetchall()
        finally:
            await conn.close()

        if not rows:
            return SkillResult.empty(self.name, "No chapter facts")

        tiers = snapshot.location_tiers
        uber_root = self._find_uber_root(snapshot.location_parents)

        # 地名别名归一:所有取名处先过 canonical(票仓合流/freq 归并/
        # evidence_pairs 与 baseline 注入匹配统一)。表为空时 _canon 是恒等。
        alias_map = self._alias_map()

        def _canon(name: str) -> str:
            return alias_map.get(name, name)

        # ── Phase 1: Build frequency, chapter settings, location chapters ──
        loc_freq: Counter = Counter()
        chapter_settings: dict[int, str] = {}
        location_chapters: dict[str, list[int]] = {}

        for row in rows:
            data = json.loads(row["fact_json"])
            ch_id = data.get("chapter_id", 0)
            locations = data.get("locations", [])
            for loc in locations:
                name = _canon(loc.get("name", ""))
                if name:
                    loc_freq[name] += 1
                    location_chapters.setdefault(name, []).append(ch_id)
            # Primary setting
            settings = [
                loc for loc in locations
                if loc.get("role") == "setting" and loc.get("name")
                and (not _is_generic_location(_canon(loc["name"]))
                     or _canon(loc["name"]) == uber_root)
            ]
            if settings:
                best_rank, best_name = 999, ""
                for loc in settings:
                    cname = _canon(loc["name"])
                    suf = _get_suffix_rank(cname)
                    rank = suf if suf is not None else TIER_ORDER.get(
                        tiers.get(cname, "city"), 4)
                    if rank < best_rank:
                        best_rank = rank
                        best_name = cname
                if best_name:
                    chapter_settings[ch_id] = best_name
            elif locations:
                for loc in locations:
                    ln = _canon(loc.get("name", ""))
                    if ln and (not _is_generic_location(ln) or ln == uber_root):
                        chapter_settings[ch_id] = ln
                        break

        # ── Phase 2: Build votes from chapter facts ──
        votes: dict[str, Counter] = {}

        # Evidence pairs actually cast as votes from current chapter facts
        # (loc.parent 直接证据 / contains 空间关系 / 主场景推断).
        # Baseline injection below is gated on this set (Epic D3 follow-up):
        # old parents whose pair has NO current evidence must not be
        # re-injected — otherwise pre-D3 propagation pollution (adjacent/
        # direction→parent) is immortal via the baseline channel.
        evidence_pairs: set[tuple[str, str]] = set()

        # Peer pairs
        peer_pairs: set[frozenset[str]] = set()
        for row in rows:
            data = json.loads(row["fact_json"])
            for loc in data.get("locations", []):
                peers = loc.get("peers")
                name = _canon(loc.get("name", ""))
                if peers and name:
                    for peer in peers:
                        peer = _canon(peer)
                        if peer and peer != name:
                            peer_pairs.add(frozenset({name, peer}))

        # Chapter fact votes
        total_chapters = max(len(rows), 1)
        # P1-B: (child, parent) → 投过该有机票的章节集合(仅第①②类有机票;
        # prior/baseline 票不进),供聚合后 TSDF 式冲突降权判定证据强度。
        pair_chapters: dict[str, dict[str, set[int]]] = {}
        # (Removed, issue #70 D3) spatial_neighbors 收集与传播已删除:
        # adjacent/direction/in_between 不再产生 parent 票(邻近≠包含)。

        for chapter_idx, row in enumerate(rows):
            data = json.loads(row["fact_json"])
            ch_id = data.get("chapter_id", 0)
            chapter_weight = 1.0 + evolve_param(
                "vote_builder.chapter_slope", 0.5
            ) * (chapter_idx / total_chapters)

            for loc in data.get("locations", []):
                parent = _canon(loc.get("parent") or "")
                name = _canon(loc.get("name", ""))
                if parent and name and name != parent:
                    if (_is_generic_location(name) and name != uber_root) or \
                       (_is_generic_location(parent) and parent != uber_root):
                        continue
                    # Story 5.2: passage-like nodes never act as a parent
                    # (a road/corridor does not contain anything). Drop the vote
                    # so "道路下辖学校" edges cannot form.
                    if is_passage_like(parent):
                        continue
                    pair_key = frozenset({name, parent})
                    w = evolve_param("vote_builder.peer_discount", 0.33) \
                        if pair_key in peer_pairs else 1.0
                    votes.setdefault(name, Counter())[parent] += w * chapter_weight
                    evidence_pairs.add((name, parent))
                    pair_chapters.setdefault(name, {}).setdefault(parent, set()).add(ch_id)

            for sr in data.get("spatial_relationships", []):
                rel = sr.get("relation_type", "")
                src, tgt = _canon(sr.get("source", "")), _canon(sr.get("target", ""))
                if not src or not tgt or src == tgt:
                    continue
                if (_is_generic_location(src) and src != uber_root) or \
                   (_is_generic_location(tgt) and tgt != uber_root):
                    continue
                if classify_spatial_relation(rel) != "hierarchy":
                    continue
                # Story 5.2: passage-like node cannot be the container (parent).
                # Only the parent (src) is gated — a passage-like child that
                # carries explicit real-parent votes is still attached to that
                # parent; only the spurious uber_root fallback is suppressed
                # (see edmonds_resolver).
                if is_passage_like(src):
                    continue
                weight = {"high": evolve_param("vote_builder.spatial_high_weight", 2),
                          "medium": 1, "low": 1}.get(
                    sr.get("confidence", "low"), 1)
                # P1-A1 (默认关): 关系带数值 confidence_score(0-1)时按
                # (0.5 + score) 连续调权(区间 0.5–1.5),替代高中低三档的
                # 粗粒度。开关关时完全不启用。
                if evolve_param("votes.confidence_score_blend", False):
                    cs = sr.get("confidence_score")
                    if cs is not None:
                        weight = weight * (0.5 + float(cs))
                # Direction validation
                s_suf = _get_suffix_rank(src)
                t_suf = _get_suffix_rank(tgt)
                s_rank = s_suf if s_suf is not None else TIER_ORDER.get(tiers.get(src, "city"), 4)
                t_rank = t_suf if t_suf is not None else TIER_ORDER.get(tiers.get(tgt, "city"), 4)
                if s_rank > t_rank:
                    src, tgt = tgt, src
                votes.setdefault(tgt, Counter())[src] += weight * chapter_weight
                evidence_pairs.add((tgt, src))
                pair_chapters.setdefault(tgt, {}).setdefault(src, set()).add(ch_id)

            # Primary setting inference
            locations = data.get("locations", [])
            setting_candidates = [
                loc for loc in locations
                if loc.get("role") == "setting" and loc.get("name")
                and (not _is_generic_location(_canon(loc["name"]))
                     or _canon(loc["name"]) == uber_root)
            ]
            primary = None
            if setting_candidates:
                best_rank = 999
                for loc in setting_candidates:
                    cname = _canon(loc["name"])
                    suf = _get_suffix_rank(cname)
                    rank = suf if suf is not None else TIER_ORDER.get(
                        tiers.get(cname, "city"), 4)
                    if rank < best_rank:
                        best_rank = rank
                        primary = cname
            elif locations:
                for loc in locations:
                    ln = _canon(loc.get("name", ""))
                    if ln and (not _is_generic_location(ln) or ln == uber_root):
                        primary = ln
                        break

            if primary and not self._is_realm(primary):
                # Story 5.2: a passage-like primary setting must not become a
                # parent of the chapter's other locations.
                if is_passage_like(primary):
                    continue
                p_suf = _get_suffix_rank(primary)
                p_rank = p_suf if p_suf is not None else TIER_ORDER.get(
                    tiers.get(primary, "city"), 4)
                for loc in locations:
                    ln = _canon(loc.get("name", ""))
                    if ln == primary or loc.get("parent"):
                        continue
                    if not ln or (_is_generic_location(ln) and ln != uber_root):
                        continue
                    if loc.get("role") in ("referenced", "boundary"):
                        continue
                    c_suf = _get_suffix_rank(ln)
                    c_rank = c_suf if c_suf is not None else TIER_ORDER.get(
                        tiers.get(ln, "city"), 4)
                    if c_rank <= p_rank:
                        continue
                    # P1-A2 (默认 1.0 = 零行为变化): 主场景推断票的乘性折扣。
                    # 推断票是间接证据,冲突场景下应弱于直接证据。
                    votes.setdefault(ln, Counter())[primary] += evolve_param(
                        "vote_builder.primary_setting_weight", 2
                    ) * evolve_param("votes.primary_setting_discount", 1.0)
                    evidence_pairs.add((ln, primary))

        # 注(Story 5.5 试过并已回退):曾对「只有 topology 证据、无 hierarchy
        # 证据」的地点对显式发 0 票,想借 EdmondsResolver 的
        #   「child 有票 且 该 parent 得票 ≤0 → 丢弃遗留边」
        # 门控清掉 广宗→青州 / 蜀→荆州 这类拓扑传播污染。
        # **实测是负优化,已回退**:B 指标 3→4,max_children 35→76,边数
        # 1320→1333。原因:丢边并未提供更好的父节点,Edmonds 只能拿兜底边
        # 重挂,全部堆到少数枢纽上 —— 正是 edmonds_resolver :196-199 警告的
        # 「重新挂到 uber_root 导致度均衡发散并在多轮间震荡」。设计上保留
        # 零证据遗留边是有意的,不要与之对着干。

        # (Removed, issue #70 D3) Spatial neighbor propagation deleted.

        # ── Baseline injection (existing parents, weight=1) ──
        # Epic D3 follow-up: only re-inject an old parent edge when current
        # chapter-fact evidence still supports this exact pair (loc.parent /
        # contains / 主场景推断 — all recorded in evidence_pairs above).
        # Bare legacy edges (e.g. pre-D3 adjacent/direction propagation) get
        # no baseline vote, so EdmondsResolver can re-resolve them from
        # evidence instead of preserving them forever.
        known_locs = set(tiers.keys())
        baseline_pairs: set[tuple[str, str]] = set()
        if snapshot.location_parents:
            baseline_injected = 0
            baseline_dropped = 0
            for child, parent in sorted(snapshot.location_parents.items()):
                # 别名归一:旧 ws 里的别名边按 canonical 对齐 evidence_pairs
                # (修复别名导致的旧边静默丢失);归一并成 self-loop 的
                # (如 汴梁城→东京)跳过—— canonical 节点的边由本名旧边或
                # 有机票承担。
                child, parent = _canon(child), _canon(parent)
                if child == parent:
                    baseline_dropped += 1
                    continue
                if parent not in known_locs and parent != uber_root:
                    continue
                # Story 5.2: never re-inject legacy edges that involve a
                # passage-like node (e.g. 走廊→天下, or 学校→长街). Such edges
                # are topology/orphan, not hierarchy.
                if is_passage_like(child) or is_passage_like(parent):
                    baseline_dropped += 1
                    continue
                if (child, parent) not in evidence_pairs:
                    baseline_dropped += 1
                    continue
                votes.setdefault(child, Counter())[parent] += evolve_param(
                    "vote_builder.baseline_weight", 1
                )
                baseline_pairs.add((child, parent))
                baseline_injected += 1
            logger.info(
                "Baseline: %d parents injected, %d bare edges not re-injected",
                baseline_injected, baseline_dropped,
            )

        # ── E: 单章孤证降权(默认关,discount=1.0 时零行为变化)──
        # 只有单章证据的有机票(第①②类,pair_chapters 恰 1 章)是重抽取
        # 试点结论中的少数派噪声主源;对其降权、多章 corroboration 票保持
        # 全价,抑制噪声捕获(gen 280 幻影捕获点多为单章票)。
        # 豁免:baseline 注入票所在的 (child,parent) 票整条不降(沿用 P1-B
        # 的 baseline_pairs 追踪;有机与 baseline 份额在同一计数格内,
        # 整条豁免以免误伤 baseline 权威语义);prior 票在本 skill 之后
        # 注入,天然不在作用域;第③类主场景推断票无 pair_chapters 记录,
        # 不受影响。与 P1-B 的次序:先做单票级孤证降权,再做 child 级
        # 冲突降权(后者 gating 看到的是降权后的票值)——两 pass 默认
        # 都关,叠加时按此次序。
        single_discount = evolve_param("votes.single_source_discount", 1.0)
        if single_discount != 1.0:
            discounted = 0
            for child in sorted(votes):
                chapters = pair_chapters.get(child, {})
                if not chapters:
                    continue
                counter = votes[child]
                new_counter = Counter()
                for p, w in counter.items():
                    if ((child, p) not in baseline_pairs
                            and len(chapters.get(p, ())) == 1):
                        new_counter[p] = w * single_discount
                        discounted += 1
                    else:
                        new_counter[p] = w
                votes[child] = new_counter
            if discounted:
                logger.info(
                    "E single-source discount: %d tickets discounted", discounted)

        # ── P1-B: TSDF 式冲突降权(默认关,decay=1.0 时零行为变化)──
        # 长期被冲突证据反复拉扯的 child(top1/top2 票比接近且双方都跨
        # ≥2 章有独立证据)整组降置信,降低其对 Edmonds 全局竞争的影响
        # (TSDF 滤除不稳定观测)。只降权不删票(显式 0 票清污染边实测是
        # 负优化,见上方 Story 5.5 注释);baseline 注入票不参与降权
        # (其权威语义不可被新因子动摇);prior 票由 KnowledgePrior 在
        # 本 skill 之后注入,天然不在此作用域。
        conflict_decay = evolve_param("votes.conflict_decay", 1.0)
        if conflict_decay != 1.0:
            conflict_ratio = evolve_param("votes.conflict_ratio", 2.0)
            decayed = 0
            for child in sorted(votes):
                counter = votes[child]
                if len(counter) < 2:
                    continue
                # (-weight, name) 排序:同权重 tie-break 确定
                ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
                (p1, w1), (p2, w2) = ranked[0], ranked[1]
                if w2 <= 0 or w1 / w2 >= conflict_ratio:
                    continue
                chapters = pair_chapters.get(child, {})
                if len(chapters.get(p1, ())) < 2 or len(chapters.get(p2, ())) < 2:
                    continue
                votes[child] = Counter({
                    p: (w if (child, p) in baseline_pairs else w * conflict_decay)
                    for p, w in counter.items()
                })
                decayed += 1
            if decayed:
                logger.info("P1-B conflict decay: %d children decayed", decayed)

        # Uber-root vote capping
        if uber_root:
            uber_cap = evolve_param("vote_builder.uber_root_cap", 2)
            for _loc_name, counter in votes.items():
                if uber_root in counter and len(counter) > 1:
                    if counter[uber_root] > uber_cap:
                        counter[uber_root] = uber_cap

        n_core = sum(1 for c in loc_freq.values() if c >= 10)
        n_reg = sum(1 for c in loc_freq.values() if 3 <= c <= 9)
        n_micro = sum(1 for c in loc_freq.values() if c <= 2)
        logger.info(
            "VoteBuilder: %d votes, freq=%d core + %d regular + %d micro",
            len(votes), n_core, n_reg, n_micro,
        )

        # Return result — votes go into snapshot, freq/settings as metadata
        result = SkillResult(skill_name=self.name, new_votes=votes)
        # Store frequency data in a way the snapshot can use
        # We return a special snapshot instead of applying to existing
        result._extra = {
            "location_frequencies": loc_freq,
            "chapter_settings": chapter_settings,
            "location_chapters": location_chapters,
            "peer_pairs": peer_pairs,
        }
        return result

    @staticmethod
    def _find_uber_root(parents: dict[str, str]) -> str | None:
        if not parents:
            return None
        children = set(parents.keys())
        counts: Counter = Counter()
        for p in parents.values():
            if p not in children:
                counts[p] += 1
        return counts.most_common(1)[0][0] if counts else None

    @staticmethod
    def _is_realm(name: str) -> bool:
        return any(kw in name for kw in "幻梦仙灵冥虚魔")
