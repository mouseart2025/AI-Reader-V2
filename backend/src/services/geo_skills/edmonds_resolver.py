"""EdmondsResolver — GeoSkill that finds globally optimal parent tree.

Uses Chu-Liu/Edmonds' algorithm (networkx.maximum_spanning_arborescence)
to find the maximum weight directed spanning tree from accumulated votes.

Mathematical formulation:
    Given directed graph G=(V, E, w) where w(parent→child) = vote weight,
    find arborescence T* rooted at uber_root that maximizes ∑w(e) for e∈T*.

Key advantages over voting method:
- Global optimality: guaranteed best tree under vote weights (not greedy)
- Structural guarantee: result is always a valid tree (no cycles, connected)
- Deterministic: no LLM dependency, millisecond execution

Based on: McDonald et al. (2005) "Non-Projective Dependency Parsing
using Spanning Tree Algorithms" — same mathematical structure applied
to NLP dependency parsing.
"""

from __future__ import annotations

import logging
from collections import Counter

import networkx as nx

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.evolve_params import evolve_param
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult
from src.utils.location_names import is_passage_like

logger = logging.getLogger(__name__)


class EdmondsResolver(GeoSkill):
    """Resolve votes into optimal parent tree via Edmonds' algorithm."""

    @property
    def name(self) -> str:
        return "层级优化"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        votes = snapshot.parent_votes
        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        if not votes:
            return SkillResult.empty(self.name, "No votes to resolve")

        # Find uber_root
        uber_root = self._find_uber_root(snapshot.location_parents)
        if not uber_root:
            # Fallback: find the "world" tier location
            for loc, tier in tiers.items():
                if tier == "world":
                    uber_root = loc
                    break
        if not uber_root:
            uber_root = "天下"  # last resort

        # ── Build directed graph ──
        # Edge direction: parent → child (Edmonds convention for arborescence)
        # Weight: accumulated votes for this parent-child pair
        G = nx.DiGraph()
        all_locs: set[str] = set(tiers.keys())
        all_locs.update(votes.keys())
        all_locs.add(uber_root)

        from src.services.world_structure_agent import _get_suffix_rank

        for child, vote_counter in votes.items():
            for parent, weight in vote_counter.items():
                if not parent or parent == child:
                    continue
                if parent not in all_locs:
                    continue
                # Story 5.2: passage-like node cannot be a parent — drop the edge
                # so "道路下辖学校" cannot enter the arborescence (input-edge gating).
                if is_passage_like(parent):
                    continue
                # Story 5.2 (AC3): a passage-like child must not be attached to
                # world root — its only candidate being uber_root means it is a
                # topology node, not a hierarchy child. Drop that edge so it
                # stays an orphan instead of dangling under 天下.
                if is_passage_like(child) and (
                    parent == uber_root or tiers.get(parent) == "world"
                ):
                    continue
                w = float(weight)
                if w <= 0:
                    continue

                # ── Tier soft constraint ──
                # When BOTH have recognizable suffixes and parent is clearly
                # smaller than child, halve the weight (discourage but don't block).
                # Blocking too aggressively reduces depth by removing valid deep edges.
                p_suf = _get_suffix_rank(parent)
                c_suf = _get_suffix_rank(child)
                if p_suf is not None and c_suf is not None and p_suf > c_suf:
                    w *= evolve_param("edmonds.tier_soft_penalty", 0.1)  # heavy penalty but not blocked

                # Edge: parent → child
                if G.has_edge(parent, child):
                    G[parent][child]["weight"] = max(
                        G[parent][child]["weight"], w
                    )
                else:
                    G.add_edge(parent, child, weight=w)

        # Ensure all locations are nodes
        # 遍历顺序必须确定:set 迭代顺序随 PYTHONHASHSEED 变化,会让
        # networkx 的节点/边插入顺序不同,进而使同权重边的 arborescence
        # 结果不同(实测同 base 两次重建差 89 条 parent)。
        for loc in sorted(all_locs):
            if loc not in G:
                G.add_node(loc)

        # ── Name-containment rule ──
        # When a child's name starts with a known location name
        # (e.g., "花果山辕门" starts with "花果山"), inject a high-weight
        # edge making that location the parent. This fixes 276 cases where
        # Edmonds' global optimization overrides obvious naming patterns.
        _NAME_CONTAIN_WEIGHT = evolve_param("edmonds.name_contain_weight", 25.0)  # higher than typical chapter votes (~1-15)
        name_contain_injected = 0
        # 二级键 = 字符串本身,保证同长度项的 tie-break 确定
        # (仅按 len 排序时,stable sort 会沿用 set 迭代顺序 → 非确定)
        sorted_locs = sorted(all_locs, key=lambda x: (-len(x), x))  # longest first
        for child in list(all_locs):
            for candidate in sorted_locs:
                if candidate == child or len(candidate) < 2:
                    continue
                # Story 5.2: a passage-like child is a topology node, not a
                # hierarchy child — do not inject a name-containment parent edge.
                if is_passage_like(child):
                    break
                # Story 5.2: a passage-like candidate cannot be a parent either.
                if is_passage_like(candidate):
                    continue
                if child.startswith(candidate) and candidate in all_locs:
                    # Don't inject if candidate is a generic prefix
                    # (e.g., "东" in "东土大唐" — too short/generic)
                    if len(candidate) < 2:
                        continue
                    # Inject or boost edge: candidate → child
                    if G.has_edge(candidate, child):
                        G[candidate][child]["weight"] = max(
                            G[candidate][child]["weight"],
                            _NAME_CONTAIN_WEIGHT,
                        )
                    else:
                        G.add_edge(candidate, child, weight=_NAME_CONTAIN_WEIGHT)
                    name_contain_injected += 1
                    break  # use longest match only
        if name_contain_injected:
            logger.info(
                "EdmondsResolver: injected %d name-containment edges (w=%.0f)",
                name_contain_injected, _NAME_CONTAIN_WEIGHT,
            )

        # Ensure uber_root can reach all nodes: add tiny-weight fallback edges
        # These are "last resort" connections — Edmonds will prefer real votes
        _FALLBACK_WEIGHT = 0.001
        for node in G.nodes():
            if node != uber_root and not G.has_edge(uber_root, node):
                # Story 5.2: a passage-like node is a topology node, not a
                # hierarchy child — do not force-attach it to uber_root.
                if is_passage_like(node):
                    continue
                G.add_edge(uber_root, node, weight=_FALLBACK_WEIGHT)

        logger.info(
            "EdmondsResolver: graph %d nodes, %d edges, root=%s",
            G.number_of_nodes(), G.number_of_edges(), uber_root,
        )

        # ── Phase 1: Start from LLM-extracted parents (respect extraction) ──
        # The key insight: per-chapter LLM extraction produces high-quality
        # local parent judgments (68-81% accuracy). Full Edmonds optimization
        # paradoxically degrades these by overriding correct local judgments
        # with noisy global co-occurrence weights.
        #
        # New strategy: preserve LLM parents as base, use Edmonds only to:
        # 1. Fix structural violations (cycles, multiple roots)
        # 2. Assign parents to orphan locations
        # 3. Override LLM parents only when name-containment or priors disagree

        base_parents = dict(snapshot.location_parents)
        prior_edge_set: frozenset[tuple[str, str]] = snapshot.prior_edges
        prior_edge_children = {c for c, _ in prior_edge_set}

        # ── Evidence gating for base parents (Epic D3 follow-up) ──
        # Old parents enter the snapshot from world_structures or previous
        # hierarchy_snapshots. Preserving them unconditionally makes stale
        # edges immortal: pre-D3 adjacent/direction propagation pollution
        # survived every rebuild through this "base parents preserved"
        # channel. VoteBuilder only casts votes (including baseline ones)
        # for pairs supported by current evidence (loc.parent / contains /
        # 主场景推断) and KnowledgePrior adds its own votes, so:
        #   - child HAS votes but none for this old pair → evidence exists
        #     and points elsewhere → drop the bare edge; Edmonds re-resolves
        #     the child from real votes (this kills the D3 pollution class,
        #     whose children virtually always carry primary-setting votes);
        #   - child has NO votes at all → no evidence either way → keep the
        #     old parent. Re-attaching zero-evidence children to uber_root
        #     fallbacks would feed degree-balancing scatter and oscillate
        #     between runs, and many such edges are correct (e.g. LLM
        #     SET_PARENT ops or older legitimate resolutions like 涿郡→幽州).
        bare_dropped = 0
        for child, parent in list(base_parents.items()):
            # Story 5.2: a passage-like child must not be force-attached via a
            # legacy edge (e.g. 走廊→天下). Orphan it so it stays a topology node.
            if is_passage_like(child):
                del base_parents[child]
                bare_dropped += 1
                continue
            # Story 5.2 (AC1): a passage-like node can never be a PARENT —
            # including via a legacy/base edge (e.g. 战船→华容道, 公安→华容道).
            # The vote path already drops passage-like parents (:78) and the
            # name-containment path too (:130), but base parents carried over
            # from world_structures bypassed both, leaking AC1 into the final
            # tree. Story 5.5 real-data check caught 10 such edges on 三国.
            if is_passage_like(parent):
                del base_parents[child]
                bare_dropped += 1
                continue
            child_votes = votes.get(child)
            if child_votes and child_votes.get(parent, 0) <= 0:
                if (child, parent) in prior_edge_set:
                    continue  # 确定性先验边不受裸边清除
                del base_parents[child]
                bare_dropped += 1
        if bare_dropped:
            logger.info(
                "EdmondsResolver: dropped %d base parents contradicted by "
                "current evidence (bare legacy edges)",
                bare_dropped,
            )

        # Apply name-containment overrides (high-confidence, covers 306+ cases)
        name_contain_applied = 0
        # 高置信边(名包含/知识先验)集合:Phase 4 幻父上提与 Phase 5 度均衡
        # 不得改动这些子节点的归属——度均衡曾把 文德殿→东京(先验 w=35)
        # 重排为 文德殿→内苑,把确定性知识错挂成跳层(2026-09-19 水浒实测)。
        protected: set[str] = set()
        for child in list(all_locs):
            for candidate in sorted_locs:
                if candidate == child or len(candidate) < 2:
                    continue
                # Story 5.2: a passage-like child is a topology node, not a
                # hierarchy child — do not inject a name-containment parent edge.
                if is_passage_like(child):
                    break
                # Story 5.2: a passage-like candidate cannot be a parent either.
                if is_passage_like(candidate):
                    continue
                if child.startswith(candidate) and candidate in all_locs:
                    if base_parents.get(child) != candidate:
                        base_parents[child] = candidate
                        name_contain_applied += 1
                    protected.add(child)
                    break

        # Apply prior overrides from votes (KnowledgePrior injected w=20+ edges)
        # These represent domain knowledge that should override LLM extraction errors
        _PRIOR_THRESHOLD = evolve_param("edmonds.prior_threshold", 15.0)  # only override if prior weight is high
        prior_applied = 0
        # 权威先验边(硬编码知识,经 prior_edges 通道):直接落定并保护,
        # 不参与下方的"高票即覆盖"——有机票可以超过先验票(三国 荆州→益州
        # 压过 荆州→天下 的 sibling 倒挂即由此而来,2026-09-19)
        for child, parent in sorted(prior_edge_set):
            if child == parent or child not in all_locs or parent not in all_locs:
                continue
            # 权威先验边豁免 passage 门:通行形态拦截是启发式(防「战船→华容道」
            # 类幻觉父),而 prior_edges 是人工策展知识——gold/原文确认道路节点
            # 可作容器(水浒 十字坡→孟州道、快活林→孟州道;红楼 荣国府→宁荣街)。
            # 2026-09-19 实测 17 条策展边被此门静默拦截(十字坡最终错挂天下)。
            if base_parents.get(child) != parent:
                base_parents[child] = parent
                prior_applied += 1
            protected.add(child)
        for child, vote_counter in votes.items():
            if child in prior_edge_children:
                continue  # 权威先验边已落定,高票不再覆盖
            if not vote_counter:
                continue
            for parent, weight in vote_counter.most_common():
                if weight >= _PRIOR_THRESHOLD and parent != child and parent in all_locs:
                    current = base_parents.get(child)
                    if current != parent:
                        base_parents[child] = parent
                        prior_applied += 1
                    protected.add(child)
                    break  # use highest-weight vote if it's a prior

        if name_contain_applied or prior_applied:
            logger.info(
                "EdmondsResolver: %d name-containment + %d prior overrides applied to base",
                name_contain_applied, prior_applied,
            )

        # Find locations without parents (orphans needing Edmonds)
        orphans = [loc for loc in sorted(all_locs)
                   if loc not in base_parents and loc != uber_root]

        # ── Phase 2: Edmonds for orphans only ──
        # Run Edmonds on the graph and use its assignments for orphans.
        #
        # Story 5.2 deliberately orphans passage-like nodes (they are *topology*
        # nodes, not hierarchy children) by refusing to attach them to uber_root
        # (:161) and by refusing to inject name-containment parents for them
        # (:127). nx.maximum_spanning_arborescence requires EVERY node to be
        # reachable from the root, so a single such node makes the whole
        # arborescence raise — which silently disabled phases 2-5 (orphan
        # filling, cycle repair, phantom lift, degree balancing) for the entire
        # novel. Found on 三国 during Story 5.5 real-data acceptance.
        #
        # Fix: run the arborescence on the root-reachable subgraph. Intentionally
        # orphaned nodes stay parentless, which is exactly the 5.2 semantics.
        reachable = nx.descendants(G, uber_root) | {uber_root}
        unreachable = set(G.nodes()) - reachable
        G_arbo = G
        if unreachable:
            logger.info(
                "EdmondsResolver: %d node(s) unreachable from root(%s) — "
                "excluded from arborescence (Story 5.2 topology nodes): %s%s",
                len(unreachable), uber_root,
                sorted(unreachable)[:20],
                " ..." if len(unreachable) > 20 else "",
            )
            G_arbo = G.subgraph(reachable).copy()
        try:
            T = nx.maximum_spanning_arborescence(G_arbo, attr="weight")
        except nx.NetworkXException as e:
            logger.error("Edmonds algorithm failed: %s", e)
            return SkillResult.empty(self.name, f"Edmonds failed: {e}")

        edmonds_parents: dict[str, str] = {}
        for u, v in T.edges():
            edmonds_parents[v] = u

        # Merge: base (LLM) + Edmonds for orphans
        parents: dict[str, str] = dict(base_parents)
        orphans_filled = 0
        for orphan in orphans:
            if orphan in edmonds_parents:
                parents[orphan] = edmonds_parents[orphan]
                orphans_filled += 1

        # ── Phase 3: Structural repair ──
        # Fix cycles to a fixpoint. A single pass is not enough: replacing the
        # weakest edge with Edmonds' choice can be a no-op when Edmonds picked
        # the other edge of the same cycle (e.g. a name-containment edge with
        # no vote weight), leaving the cycle intact (found via cross-LLM
        # replication: 黑水河 ↔ 黑水河水府 on DeepSeek extraction).
        parents, cycles_broken = self._break_cycles_fixpoint(
            parents, votes, edmonds_parents, uber_root
        )

        # Ensure single root
        roots = [loc for loc in sorted(all_locs)
                 if loc not in parents and loc != uber_root]
        for root in roots:
            if root in edmonds_parents:
                parents[root] = edmonds_parents[root]

        logger.info(
            "EdmondsResolver (incremental): %d base parents preserved, "
            "%d orphans filled by Edmonds, %d cycles repaired",
            len(base_parents), orphans_filled, cycles_broken,
        )

        # ── Phase 4: Phantom parent lift (Phase 1b from errata analysis) ──
        # 当父节点mention count极低但子节点爆炸, LLM倾向于幻觉地把附近地点
        # 都归给这个弱证据锚点. 将零证据子节点上提到grandparent.
        # 出自西游记 errata: 紫云山(mc=1)→27 kids, 黑风山(mc=2)→26 kids 等案例.
        parents, phantoms_lifted = self._lift_phantom_parent_children(
            parents, freq, uber_root, protected
        )
        if phantoms_lifted:
            logger.info(
                "EdmondsResolver: lifted %d weak children from phantom parents",
                phantoms_lifted,
            )

        # ── Phase 5: Degree balancing ──
        _MAX_CHILDREN = evolve_param("edmonds.max_children", 30)
        parents = self._balance_degrees(
            parents, tiers, _MAX_CHILDREN, protected, votes)

        # ── Final structural pass ──
        # Phases 4-5 reassign parents and can reintroduce cycles; guarantee
        # the output is acyclic before returning.
        parents, final_cycles_broken = self._break_cycles_fixpoint(
            parents, votes, edmonds_parents, uber_root
        )
        cycles_broken += final_cycles_broken

        # ── Final AC1 gate (Story 5.5) ──
        # Every earlier path rejects a passage-like parent — votes (:78),
        # name-containment (:130 / :228), base parents — but Phases 3-5
        # reassign parents and can reintroduce one (on 三国 this left
        # 斜谷道口→斜谷道). Enforce the invariant on the OUTPUT so AC1 holds
        # unconditionally: a passage-like node is a topology node and never
        # owns children. The child is left parentless rather than re-attached.
        # Two ways a passage-like parent can reach the output:
        #   1. it survives in `parents` (Phases 3-5 reassigned it), or
        #   2. the child was dropped earlier (orphan) so it is absent from
        #      `parents` — and HierarchySnapshot.apply() MERGES, meaning the
        #      legacy edge from the incoming snapshot is preserved untouched.
        # Case 2 is the subtle one (on 三国: 斜谷道口→斜谷道) and needs an
        # explicit `None` override, since only `parent is None` deletes (:54-55);
        # `del parents[child]` leaves the legacy edge in place.
        # 权威先验边豁免(2026-09-19):prior_edges 是人工策展知识,gold/原文
        # 确认特定道路节点可作容器(十字坡→孟州道、荣国府→宁荣街);无豁免时
        # 本门把这类子节点清成 root(水浒 root_count 2→4 实测)。
        ac1_violations = [
            c for c, p in parents.items()
            if is_passage_like(p) and (c, p) not in prior_edge_set
        ]
        for child, parent in list(snapshot.location_parents.items()):
            if (is_passage_like(parent) and parents.get(child) is None
                    and (child, parent) not in prior_edge_set):
                ac1_violations.append(child)
        if ac1_violations:
            for c in set(ac1_violations):
                parents[c] = None
            logger.info(
                "EdmondsResolver: dropped %d edge(s) with a passage-like parent "
                "(Story 5.2 AC1 final gate): %s",
                len(set(ac1_violations)), sorted(set(ac1_violations))[:10],
            )

        result = SkillResult(
            skill_name=self.name,
            parent_overrides=parents,
        )

        # Stats
        ch_count = Counter(parents.values())
        top = ch_count.most_common(1)
        max_ch = top[0][1] if top else 0
        logger.info(
            "EdmondsResolver: %d parents, max_children=%d(%s)",
            len(parents), max_ch, top[0][0] if top else "?",
        )
        return result

    @staticmethod
    def _break_cycles_fixpoint(
        parents: dict[str, str],
        votes: dict[str, Counter],
        edmonds_parents: dict[str, str],
        uber_root: str,
    ) -> tuple[dict[str, str], int]:
        """Break parent-pointer cycles until none remain.

        Each round finds one cycle and rewires its weakest edge (by vote
        weight). Redirect candidates in preference order:
          1. Edmonds' choice for that child — only if it is outside the
             cycle and its ancestor chain does not pass through the child
             (otherwise the redirect would be a no-op or create a new cycle);
          2. uber_root — under the same ancestor safety check;
          3. delete the edge (child becomes a root, re-attached later by the
             single-root step if this runs before it).

        Every round provably breaks one cycle and creates none (only the
        chosen child's outgoing edge changes, and the ancestor check rules
        out a new cycle through it), so the loop terminates.
        """
        parents = dict(parents)
        cycles_broken = 0

        def _reaches(candidate: str, target: str) -> bool:
            """Does following parent pointers from candidate hit target?"""
            node = candidate
            seen: set[str] = set()
            while node in parents and node not in seen:
                if node == target:
                    return True
                seen.add(node)
                node = parents[node]
            return node == target

        for _ in range(len(parents) + 1):
            # Find one cycle (as a set of member nodes)
            cycle_nodes: set[str] | None = None
            for start in parents:
                visited: dict[str, int] = {}
                path: list[str] = []
                node = start
                while node in parents and node not in visited:
                    visited[node] = len(path)
                    path.append(node)
                    node = parents[node]
                if node in visited:
                    cycle_nodes = set(path[visited[node]:])
                    break
            if cycle_nodes is None:
                return parents, cycles_broken

            # Weakest edge in the cycle by vote weight
            weakest_child = min(
                cycle_nodes,
                key=lambda c: votes.get(c, Counter()).get(parents[c], 0),
            )
            candidate = edmonds_parents.get(weakest_child)
            if (
                candidate
                and candidate not in cycle_nodes
                and candidate != weakest_child
                and not _reaches(candidate, weakest_child)
            ):
                parents[weakest_child] = candidate
            elif uber_root not in cycle_nodes and not _reaches(
                uber_root, weakest_child
            ):
                parents[weakest_child] = uber_root
            else:
                del parents[weakest_child]
            cycles_broken += 1

        return parents, cycles_broken

    @staticmethod
    def _lift_phantom_parent_children(
        parents: dict[str, str],
        freq: Counter,
        uber_root: str,
        protected: set[str] | None = None,
        phantom_mc_threshold: int = 3,
        phantom_children_threshold: int = 5,
        target_children: int = 3,
    ) -> tuple[dict[str, str], int]:
        """Lift zero-evidence children from low-mc high-child parents.

        v0.71.1 tightened thresholds — original settings (mc<=2, kids>=10)
        missed the majority of phantom catch-alls found in cross-novel audit:
          - 陷空山(mc=3,kids=29)  馒头庵(mc=3,kids=20)
          - 柴扉(mc=0,kids=9)      西行路上(mc=1,kids=7)
          - 哈咇国(mc=0,kids=8)    本省(mc=1,kids=14)

        New defaults(mc<=3, kids>=5, target=3)加上 ratio 检测捕获它们.
        Also lift children with mc<=1 (not just mc=0) since single-chapter
        evidence under a phantom parent is unreliable.

        Algorithm:
          1. Count children per parent
          2. For each phantom parent:
             a) phantom_mc <= phantom_mc_threshold  AND
             b) len(children) >= phantom_children_threshold  AND
             c) (ratio check) children / max(mc, 1) >= 3
          3. Lift children with mc <= 1 to grandparent until remaining <= target
        """
        if not parents:
            return parents, 0
        children_by_parent: dict[str, list[str]] = {}
        for child, parent in parents.items():
            children_by_parent.setdefault(parent, []).append(child)

        lifted = 0
        new_parents = dict(parents)
        for phantom, children in children_by_parent.items():
            if phantom == uber_root:
                continue
            phantom_mc = freq.get(phantom, 0)
            if phantom_mc > phantom_mc_threshold:
                continue
            if len(children) < phantom_children_threshold:
                continue
            # Ratio guard: only lift if the imbalance is severe
            ratio = len(children) / max(phantom_mc, 1)
            if ratio < 3:
                continue
            grandparent = new_parents.get(phantom, uber_root) or uber_root
            # Sort children by mc ascending (lift weakest first)
            children_sorted = sorted(children, key=lambda c: (freq.get(c, 0), c))
            remaining = len(children)
            for c in children_sorted:
                if remaining <= target_children:
                    break
                # v0.71.1: also lift mc=1 children (not just mc=0). Single-
                # chapter evidence under a phantom is unreliable.
                if freq.get(c, 0) > 1:
                    continue
                # 高置信边(先验/名包含)不上提:确定性知识优先于均衡启发式
                if protected and c in protected:
                    continue
                new_parents[c] = grandparent
                lifted += 1
                remaining -= 1
        return new_parents, lifted

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
    def _balance_degrees(
        parents: dict[str, str],
        tiers: dict[str, str],
        max_children: int,
        protected: set[str] | None = None,
        votes: dict[str, Counter] | None = None,
    ) -> dict[str, str]:
        """Redistribute children when a node exceeds max_children.

        Two-phase strategy:
        Phase 1: Redistribute leaf children to existing intermediate nodes
        Phase 2: For remaining overflows, redistribute to ANY smaller-tier
                 child (not just intermediates) — creating new intermediate layers

        零票改挂禁止(evolve_param "edmonds.zero_vote_reassign",默认
        "forbid"):叶节点对**当前** parent 有正票、而候选 absorber 零票
        时,度均衡不得覆盖票仓多数决——本 phase 是纯结构启发式(只读
        tier/度),此前票盲改挂曾把 红楼 省亲别墅({大观园: 4.5 全票})
        重排到 0 票的 紫菱洲,造成两轮 rebuild 间金标边交替(2026-09-22
        确诊)。"allow" 恢复旧行为(A/B 对照通道)。
        """
        from src.services.world_structure_agent import TIER_ORDER

        def _rebuild_children_map():
            cm: dict[str, list[str]] = {}
            for child, parent in parents.items():
                cm.setdefault(parent, []).append(child)
            return cm

        for _iteration in range(10):
            children_map = _rebuild_children_map()
            any_change = False

            for node in list(children_map.keys()):
                kids = children_map.get(node, [])
                if len(kids) <= max_children:
                    continue


                # Sort kids: non-leaf first (intermediates), then by tier rank desc
                kid_has_children = {
                    k: len(children_map.get(k, [])) for k in kids
                }
                # Candidates to absorb: kids with lower tier rank than leaves
                absorbers = sorted(
                    [k for k in kids if kid_has_children.get(k, 0) > 0],
                    key=lambda k: kid_has_children.get(k, 0),
                    reverse=True,
                )
                # If no absorbers, use any kid that has a bigger tier than others
                if not absorbers:
                    absorbers = sorted(
                        kids,
                        key=lambda k: TIER_ORDER.get(tiers.get(k, "site"), 5),
                    )
                    # Only use kids that are at least one tier bigger than the smallest
                    if absorbers:
                        min_rank = TIER_ORDER.get(
                            tiers.get(absorbers[-1], "site"), 5
                        )
                        absorbers = [
                            k for k in absorbers
                            if TIER_ORDER.get(tiers.get(k, "site"), 5) < min_rank
                        ]

                if not absorbers:
                    continue

                # Leaves to redistribute (smallest tier first)
                # 高置信边(先验/名包含)不参与重排:度均衡是结构启发式,
                # 不得覆盖确定性知识(2026-09-19 水浒 文德殿/聚义厅案例)
                leaves = sorted(
                    [k for k in kids if k not in absorbers
                     and not (protected and k in protected)],
                    key=lambda k: TIER_ORDER.get(tiers.get(k, "site"), 5),
                    reverse=True,
                )

                redistributed = 0
                for leaf in leaves:
                    if len(kids) <= max_children:
                        break
                    leaf_rank = TIER_ORDER.get(tiers.get(leaf, "site"), 5)

                    # Find best absorber: bigger tier + fewest current children
                    best = None
                    best_score = -1
                    for ab in absorbers:
                        ab_rank = TIER_ORDER.get(tiers.get(ab, "city"), 4)
                        if ab_rank >= leaf_rank:
                            continue  # absorber must be bigger tier
                        ab_children = len(children_map.get(ab, []))
                        if ab_children >= max_children:
                            continue  # don't overflow absorber
                        score = max_children - ab_children
                        if score > best_score:
                            best = ab
                            best_score = score

                    if best:
                        # 零票改挂禁止:当前 parent 有正票而 absorber 零票 →
                        # 跳过该叶(结构均衡让位票仓多数决)
                        if votes and evolve_param(
                                "edmonds.zero_vote_reassign", "forbid"
                        ) == "forbid":
                            leaf_votes = votes.get(leaf) or {}
                            if (leaf_votes.get(parents[leaf], 0) > 0
                                    and leaf_votes.get(best, 0) <= 0):
                                continue
                        parents[leaf] = best
                        kids.remove(leaf)
                        children_map.setdefault(best, []).append(leaf)
                        redistributed += 1
                        any_change = True

                if redistributed:
                    logger.debug(
                        "Degree balance: %s %d→%d children",
                        node, len(kids) + redistributed, len(kids),
                    )

            if not any_change:
                break

        return parents
