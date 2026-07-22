"""M5 ablation — greedy voting + post-processing, NO Edmonds MWA, NO priors.

Answers the reviewer question (review-sim M5 / devils-advocate M5):
  "The 279→63 fan-out reduction may come entirely from post-processing
   heuristics (phantom-parent lift, degree balancing, cycle repair) rather
   than from Edmonds' algorithm itself."

Configuration (the missing ablation row):
  TierClassifier → VoteBuilder → VoteResolver (greedy voting, the paper's
  voting baseline) → M5PostProcessor. The M5 base is VoteResolver's own
  output map ONLY — the frozen world_structures.location_parents currently
  hold the FULL-pipeline output (apply_to_world_structure ran; xiyouji
  ws max_ch=63 with virtual layer root 主世界), so letting the snapshot
  merge leak ws parents into the M5 base would contaminate the ablation
  with the very pipeline output being ablated. M5NoEdmondsResolver
  therefore takes the captured VoteResolver map as its base and emits
  None-overrides to erase every stale ws parent from the merged snapshot.

  On top of that base it applies, via the deployed EdmondsResolver's own
  static methods (verbatim reuse):
    - orphan attachment to uber_root   (naive substitute for Edmonds Phase 2)
    - cycle repair to a fixpoint       (deployed Phase 3, redirects→uber_root)
    - phantom-parent lift              (deployed Phase 4, unchanged)
    - degree balancing, K=30           (deployed Phase 5, unchanged)
  No maximum_spanning_arborescence call, no KnowledgePrior skill,
  no name-containment override, no SuffixNormalizer.

Sanity anchor: the intermediate voting snapshot (same merged-state口径 as
ablation_hierarchy.py config 3) reproduces the paper's Journey voting
max_children=279 exactly, validating the pipeline setup.

Frozen-data safety:
  The script NEVER touches the real DB. It copies the frozen DB to a
  scratch dir (default /tmp/m5-data) and points AI_READER_DATA_DIR there
  before importing any src module. All snapshot writes land in the copy.

Usage:
    cd backend && uv run python scripts/m5_no_edmonds_ablation.py
    M5_DATA_DIR=/tmp/m5-data uv run python scripts/m5_no_edmonds_ablation.py --refresh

Output:
    ../../ai-reader-internal/paper/evaluation/v071/baselines/m5-no-edmonds.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

# ── Scratch-DB isolation: must happen BEFORE any src.* import ──
_BACKEND_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(_BACKEND_DIR))

_REAL_DATA_DIR = Path(os.environ.get("AI_READER_DATA_DIR", Path.home() / ".ai-reader-v2"))
_SCRATCH_DIR = Path(os.environ.get("M5_DATA_DIR", "/tmp/m5-data"))

if _SCRATCH_DIR.resolve() == _REAL_DATA_DIR.resolve():
    sys.exit("FATAL: M5_DATA_DIR must differ from the real data dir (frozen novels).")

_REAL_DB = _REAL_DATA_DIR / "data.db"
_SCRATCH_DB = _SCRATCH_DIR / "data.db"
if "--refresh" in sys.argv or not _SCRATCH_DB.exists():
    _SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[m5] copying frozen DB → {_SCRATCH_DB} (this may take a moment)...")
    shutil.copy2(_REAL_DB, _SCRATCH_DB)

os.environ["AI_READER_DATA_DIR"] = str(_SCRATCH_DIR)

from src.db.sqlite_db import get_connection  # noqa: E402
from src.services.geo_skills.edmonds_resolver import EdmondsResolver  # noqa: E402
from src.services.geo_skills.orchestrator import GeoOrchestrator  # noqa: E402
from src.services.geo_skills.snapshot import (  # noqa: E402
    HierarchyMetrics,
    HierarchySnapshot,
    SkillResult,
)
from src.services.geo_skills.snapshot_store import SnapshotStore  # noqa: E402
from src.services.geo_skills.tier_classifier import TierClassifier  # noqa: E402
from src.services.geo_skills.vote_builder import VoteBuilder  # noqa: E402
from src.services.geo_skills.vote_resolver import VoteResolver  # noqa: E402

logger = logging.getLogger(__name__)

# Frozen benchmark novels (paper §3, v071 freeze)
NOVELS = [
    ("3b2ef56c-1a55-466a-a7d1-34272446a198", "xiyouji", "西游记"),
    ("c384901a-8b71-437a-af35-b5ec1c56c696", "honglou", "红楼梦"),
    ("4ac43c73-f67b-427c-8d6d-e766a1423977", "shuihu", "水浒传"),
    ("b1287ef6-c215-4bd2-842c-cb04aec5eb70", "sanguo", "三国演义"),
    ("53013970-effd-4f50-aef7-728ca13de69a", "fengshen", "封神演义"),
]

OUT_PATH = (
    _BACKEND_DIR / ".." / ".." / "ai-reader-internal" / "paper" / "evaluation"
    / "v071" / "baselines" / "m5-no-edmonds.json"
).resolve()


def count_cycles(parents: dict[str, str]) -> int:
    """Count distinct cycles in a parent map; each cycle counted once.

    Globally marks nodes whose downstream is fully explored, so nodes
    upstream of a cycle do not re-count it.
    """
    cycles = 0
    done: set[str] = set()
    for start in parents:
        if start in done:
            continue
        path: dict[str, int] = {}
        node = start
        while node in parents and node not in done and node not in path:
            path[node] = len(path)
            node = parents[node]
        if node in path and node not in done:
            cycles += 1  # re-entered own path → one cycle
        done.update(path)
    return cycles


def map_metrics(
    parents: dict[str, str],
    universe: set[str],
    uber_root: str,
) -> dict:
    """Structural metrics on a parent map over a node universe."""
    ch_count = Counter(parents.values())
    top = ch_count.most_common(1)
    roots = [loc for loc in universe if loc not in parents and loc != uber_root]
    cycles = count_cycles(parents)
    return {
        "nodes": len(universe),
        "total_parents": len(parents),
        "max_children": top[0][1] if top else 0,
        "max_children_node": top[0][0] if top else "",
        "roots": len(roots),
        "cycles": cycles,
        "has_cycles": cycles > 0,
    }


class CapturingVoteResolver(VoteResolver):
    """VoteResolver that exposes its own output map for downstream reuse."""

    def __init__(self) -> None:
        self.captured: dict[str, str] = {}

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        result = await super().execute(snapshot)
        self.captured = {
            k: v for k, v in result.parent_overrides.items() if v is not None
        }
        return result


class M5NoEdmondsResolver(EdmondsResolver):
    """Greedy-voting base + deployed post-passes, Edmonds-free.

    Reuses EdmondsResolver's static post-pass implementations verbatim
    (_break_cycles_fixpoint / _lift_phantom_parent_children /
    _balance_degrees) so the only delta vs. the deployed resolver is:
      - Phase 1: base = VoteResolver greedy output (no LLM-base preservation
                 from ws, no name-containment overrides, no prior overrides)
      - Phase 2: orphans attach directly to uber_root instead of being
                 placed by maximum_spanning_arborescence
      - Phase 3: cycle repair redirects to uber_root (edmonds_parents={})
    """

    def __init__(self, voting: CapturingVoteResolver) -> None:
        self._voting = voting
        self.stats: dict = {}
        # Captured artifacts for downstream accuracy evaluation
        self.final_parents: dict[str, str] = {}
        self.orphans: list[str] = []
        self.all_locs: set[str] = set()
        self.uber_root: str = ""

    @property
    def name(self) -> str:
        return "M5投票+后处理(无Edmonds)"

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        votes = snapshot.parent_votes
        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        if not votes:
            return SkillResult.empty(self.name, "No votes to resolve")

        # ── Base: greedy voting output ONLY (no ws-parent contamination) ──
        parents: dict[str, str] = dict(self._voting.captured)
        n_base = len(parents)

        uber_root = self._find_uber_root(parents)
        if not uber_root:
            for loc, tier in tiers.items():
                if tier == "world":
                    uber_root = loc
                    break
        if not uber_root:
            uber_root = "天下"

        all_locs: set[str] = set(tiers.keys())
        all_locs.update(votes.keys())
        all_locs.add(uber_root)

        # ── Phase 2 substitute: orphans → uber_root (no Edmonds) ──
        orphans = [
            loc for loc in all_locs
            if loc not in parents and loc != uber_root
        ]
        for loc in orphans:
            parents[loc] = uber_root

        # ── Phase 3 analog: cycle repair, redirects → uber_root ──
        parents, cycles_broken = self._break_cycles_fixpoint(
            parents, votes, {}, uber_root
        )
        # Re-attach nodes whose edge was deleted by repair (deployed Phase 3
        # re-attaches via Edmonds' choice; here uber_root is the only option).
        reattached = 0
        for loc in all_locs:
            if loc != uber_root and loc not in parents:
                parents[loc] = uber_root
                reattached += 1

        # ── Phase 4: phantom-parent lift (deployed code, unchanged) ──
        parents, phantoms_lifted = self._lift_phantom_parent_children(
            parents, freq, uber_root
        )

        # ── Phase 5: degree balancing (deployed code, unchanged) ──
        parents = self._balance_degrees(parents, tiers, 30)

        # ── Final structural pass (deployed code, unchanged) ──
        parents, final_cycles = self._break_cycles_fixpoint(
            parents, votes, {}, uber_root
        )
        cycles_broken += final_cycles
        for loc in all_locs:
            if loc != uber_root and loc not in parents:
                parents[loc] = uber_root

        m = map_metrics(parents, all_locs, uber_root)
        self.final_parents = dict(parents)
        self.orphans = list(orphans)
        self.all_locs = set(all_locs)
        self.uber_root = uber_root
        self.stats = {
            "uber_root": uber_root,
            "base_parents_from_voting": n_base,
            "orphans_attached_to_uber_root": len(orphans),
            "orphan_share_of_nodes": round(len(orphans) / max(len(all_locs), 1), 4),
            "reattached_after_cycle_delete": reattached,
            "cycles_broken": cycles_broken,
            "phantom_children_lifted": phantoms_lifted,
            **{f"final_{k}": v for k, v in m.items()},
        }
        logger.info("M5 stats: %s", self.stats)

        # Erase stale snapshot parents (ws full-pipeline output + voting
        # leftovers) so the merged snapshot equals the M5 map exactly.
        overrides: dict[str, str | None] = dict(parents)
        for key in snapshot.location_parents:
            if key not in parents:
                overrides[key] = None

        return SkillResult(
            skill_name=self.name,
            parent_overrides=overrides,
        )


async def run_novel(novel_id: str, key: str, title: str) -> dict:
    store = SnapshotStore()

    # Fresh snapshot chain in the SCRATCH db (real DB untouched)
    conn = await get_connection()
    try:
        await conn.execute(
            "DELETE FROM hierarchy_snapshots WHERE novel_id=?", (novel_id,),
        )
        await conn.commit()
    finally:
        await conn.close()

    voting = CapturingVoteResolver()
    m5 = M5NoEdmondsResolver(voting)
    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("voting", voting)
    orch.add_skill("m5", m5)
    async for _ in orch.run():
        pass

    # v0 import, v1 tier, v2 votes, v3 voting, v4 m5
    snap_voting = await store.load_version(novel_id, 3)
    snap_m5 = await store.load_version(novel_id, 4)

    # Voting row — two口径:
    # (a) merged snapshot (same setup as ablation_hierarchy.py config 3):
    #     sanity anchor, must reproduce the paper's Journey max_ch=279
    mv = HierarchyMetrics.compute(snap_voting)
    # (b) VoteResolver's own output map over its universe (clean)
    all_locs = set(snap_voting.location_tiers) | set(snap_voting.parent_votes)
    uber_root = m5.stats.get("uber_root", "天下")
    voting_clean = map_metrics(voting.captured, all_locs | {uber_root}, uber_root)

    m5_metrics = HierarchyMetrics.compute(snap_m5)
    m5_cycles = count_cycles(snap_m5.location_parents)

    return {
        "novel": key,
        "title": title,
        "voting_merged_snapshot": {
            "_note": "口径 sanity anchor = ablation_hierarchy.py config 3 "
                     "(ws parents merged with VoteResolver overrides)",
            "max_children": mv.max_children,
            "max_children_node": mv.max_children_node,
        },
        "voting_clean": voting_clean,
        "m5_no_edmonds": {
            "max_children": m5_metrics.max_children,
            "max_children_node": m5_metrics.max_children_node,
            "roots": m5_metrics.root_count,
            "cycles": m5_cycles,
            "has_cycles": m5_cycles > 0,
            "avg_depth": m5_metrics.avg_depth,
            "total_parents": m5_metrics.total_parents,
        },
        "m5_internal_stats": m5.stats,
    }


async def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    # Sanity guard: prove we are on the scratch DB
    from src.infra.config import DB_PATH
    assert Path(DB_PATH).resolve() == _SCRATCH_DB.resolve(), (
        f"refusing to run: DB_PATH={DB_PATH} is not the scratch copy"
    )
    print(f"[m5] scratch DB: {DB_PATH}")

    results = []
    for novel_id, key, title in NOVELS:
        print(f"[m5] {title} ...", flush=True)
        r = await run_novel(novel_id, key, title)
        vm, vc, m = r["voting_merged_snapshot"], r["voting_clean"], r["m5_no_edmonds"]
        print(
            f"  voting(merged): max_ch={vm['max_children']}({vm['max_children_node']})  "
            f"voting(clean): max_ch={vc['max_children']} roots={vc['roots']} "
            f"cycles={vc['cycles']}"
        )
        print(
            f"  m5: max_ch={m['max_children']}({m['max_children_node']}) "
            f"roots={m['roots']} cycles={m['cycles']} "
            f"orphans={r['m5_internal_stats'].get('orphans_attached_to_uber_root')}"
        )
        results.append(r)

    n = len(results)

    def _avg(rows, k):
        return round(sum(r[k] for r in rows) / n, 2)

    voting_clean_avg = {
        "max_children": _avg([r["voting_clean"] for r in results], "max_children"),
        "roots": _avg([r["voting_clean"] for r in results], "roots"),
        "novels_with_cycles": sum(
            1 for r in results if r["voting_clean"]["has_cycles"]
        ),
    }
    m5_avg = {
        "max_children": _avg([r["m5_no_edmonds"] for r in results], "max_children"),
        "roots": _avg([r["m5_no_edmonds"] for r in results], "roots"),
        "novels_with_cycles": sum(
            1 for r in results if r["m5_no_edmonds"]["has_cycles"]
        ),
        "orphans_attached_to_uber_root_total": sum(
            r["m5_internal_stats"]["orphans_attached_to_uber_root"]
            for r in results
        ),
        "cycles_broken_total": sum(
            r["m5_internal_stats"]["cycles_broken"] for r in results
        ),
        "phantom_children_lifted_total": sum(
            r["m5_internal_stats"]["phantom_children_lifted"] for r in results
        ),
    }

    # ── Frozen anchors (v071 freeze, paper Table 5) ──
    anchors = {
        "voting_only_frozen": {
            "_source": "paper Table 5 (tab:ablation) + evaluation/v071/ablation-voting-structural.json",
            "_note": (
                "Paper Journey voting max_ch=279 (VoteResolver pipeline口径, "
                "reproduced exactly by this script's merged-snapshot anchor). "
                "ablation-voting-structural.json reports a simpler majority-vote "
                "aggregation (xiyouji max_ch=66); its 5-novel avg max_ch=90.8, "
                "roots=25, cycles on 2/5 (honglou, sanguo) are the paper's "
                "reported voting-row averages."
            ),
            "per_novel_structural_json": {
                "xiyouji": {"max_children": 66, "roots": 35, "cycles": 0},
                "honglou": {"max_children": 137, "roots": 27, "cycles": 1},
                "shuihu": {"max_children": 79, "roots": 31, "cycles": 0},
                "sanguo": {"max_children": 58, "roots": 22, "cycles": 1},
                "fengshen": {"max_children": 114, "roots": 10, "cycles": 0},
            },
            "paper_journey_max_children": 279,
            "five_novel_average": {"max_children": 90.8, "roots": 25.0},
            "novels_with_cycles": 2,
        },
        "full_pipeline_frozen": {
            "_source": "paper Table 5 + restored full-pipeline hierarchy_snapshots (tag=suffix)",
            "per_novel": {
                "xiyouji": {"max_children": 63, "roots": 1, "cycles": 0},
                "honglou": {"max_children": 76, "roots": 1, "cycles": 0},
                "shuihu": {"max_children": 47, "roots": 1, "cycles": 0},
                "sanguo": {"max_children": 64, "roots": 1, "cycles": 0},
                "fengshen": {"max_children": 31, "roots": 1, "cycles": 0},
            },
            "_note": (
                "xiyouji=63 is the paper Table 5 value; other novels from the "
                "restored full-pipeline snapshots. Paper 5-novel avg max_ch=62."
            ),
            "five_novel_average": {"max_children": 62},
        },
    }

    # Comparison table: per novel, voting vs m5 vs full
    comparison = []
    for r in results:
        k = r["novel"]
        full = anchors["full_pipeline_frozen"]["per_novel"][k]
        comparison.append({
            "novel": k,
            "title": r["title"],
            "voting_max_children": r["voting_clean"]["max_children"],
            "m5_max_children": r["m5_no_edmonds"]["max_children"],
            "full_max_children": full["max_children"],
            "voting_roots": r["voting_clean"]["roots"],
            "m5_roots": r["m5_no_edmonds"]["roots"],
            "full_roots": full["roots"],
            "voting_cycles": r["voting_clean"]["cycles"],
            "m5_cycles": r["m5_no_edmonds"]["cycles"],
            "full_cycles": full["cycles"],
            "m5_orphans_attached": r["m5_internal_stats"]["orphans_attached_to_uber_root"],
            "m5_cycles_broken": r["m5_internal_stats"]["cycles_broken"],
            "m5_phantom_children_lifted": r["m5_internal_stats"]["phantom_children_lifted"],
        })

    out = {
        "_description": (
            "M5 ablation: greedy voting + post-processing (phantom-parent lift "
            "+ degree balancing K=30 + cycle repair), WITHOUT Edmonds MWA and "
            "WITHOUT knowledge priors. Tests whether the 279→63 fan-out "
            "reduction is attributable to Edmonds or to post-pass heuristics. "
            "Chain: TierClassifier→VoteBuilder→VoteResolver→M5NoEdmondsResolver; "
            "M5 base = VoteResolver output only (ws full-pipeline parents "
            "explicitly erased to avoid circular contamination); orphans→uber_root; "
            "repair redirects→uber_root; deployed Phase 4/5 code reused verbatim. "
            "Run on a scratch copy of the frozen DB (AI_READER_DATA_DIR "
            "override); frozen novels untouched."
        ),
        "_script": "backend/scripts/m5_no_edmonds_ablation.py",
        "_date": "2026-07-22",
        "per_novel": results,
        "five_novel_average": {
            "voting_clean": voting_clean_avg,
            "m5_no_edmonds": m5_avg,
        },
        "frozen_anchors": anchors,
        "comparison_table": comparison,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[m5] saved: {OUT_PATH}")

    print(
        "\n| Novel | voting max_ch | M5 max_ch | full max_ch "
        "| voting roots | M5 roots | M5 cycles |"
    )
    print("|-------|--------------|-----------|-------------|--------------|----------|-----------|")
    for c in comparison:
        print(
            f"| {c['title']} | {c['voting_max_children']} | {c['m5_max_children']} "
            f"| {c['full_max_children']} | {c['voting_roots']} "
            f"| {c['m5_roots']} | {c['m5_cycles']} |"
        )


if __name__ == "__main__":
    asyncio.run(main())
