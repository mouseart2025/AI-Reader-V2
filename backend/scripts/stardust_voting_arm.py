"""Rebuttal baseline arm — greedy voting (no Edmonds/priors/post-processing)
on the synthetic contamination-free novel 《星尘劫》 (Star-Dust Calamity).

Answers the review-sim request: Table A.1 currently only has the full
pipeline row; this adds the paper's voting baseline on the synthetic corpus
to show structural collapse (fan-out explosion / root fragmentation) also
happens without pretraining contamination, so the full pipeline's
"guarantees hold" row is a non-trivial result.

Chain: TierClassifier -> VoteBuilder -> VoteResolver (greedy voting, the
paper's voting baseline). NO Edmonds MWA, NO KnowledgePrior, NO
post-processing (no phantom-parent lift / degree balancing / cycle repair).
The evaluated parent map is VoteResolver's own output ONLY — the frozen
world_structures.location_parents hold the FULL-pipeline output, so letting
merged-snapshot parents leak in would contaminate the baseline with the very
pipeline output being compared against (same trap as m5_no_edmonds_ablation).

Accuracy scoring reuses eval_contamination_free.py's metric functions
verbatim (same 5-dimension weighting as Table A.1), against
paper/evaluation/contamination-free/novel/gold_standard.json et al.

Frozen-data safety:
  The script NEVER touches the real DB. It copies the frozen DB to a
  scratch dir (default /tmp/stardust-voting) and points AI_READER_DATA_DIR
  there before importing any src module. All snapshot writes land in the
  copy.

Usage:
    cd backend && uv run python scripts/stardust_voting_arm.py
    STARDUST_SCRATCH_DIR=/tmp/stardust-voting uv run python scripts/stardust_voting_arm.py --refresh

Output:
    ../../ai-reader-internal/paper/evaluation/contamination-free/novel/voting-baseline.json
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# ── Scratch-DB isolation: must happen BEFORE any src.* import ──
_BACKEND_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(_BACKEND_DIR))

_REAL_DATA_DIR = Path(os.environ.get("AI_READER_DATA_DIR", Path.home() / ".ai-reader-v2"))
_SCRATCH_DIR = Path(os.environ.get("STARDUST_SCRATCH_DIR", "/tmp/stardust-voting"))

if _SCRATCH_DIR.resolve() == _REAL_DATA_DIR.resolve():
    sys.exit("FATAL: STARDUST_SCRATCH_DIR must differ from the real data dir (frozen novels).")

_REAL_DB = _REAL_DATA_DIR / "data.db"
_SCRATCH_DB = _SCRATCH_DIR / "data.db"
if "--refresh" in sys.argv or not _SCRATCH_DB.exists():
    _SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[stardust-voting] copying frozen DB → {_SCRATCH_DB} ...")
    shutil.copy2(_REAL_DB, _SCRATCH_DB)

os.environ["AI_READER_DATA_DIR"] = str(_SCRATCH_DIR)

from src.db.sqlite_db import get_connection  # noqa: E402
from src.services.geo_skills.orchestrator import GeoOrchestrator  # noqa: E402
from src.services.geo_skills.snapshot import SkillResult  # noqa: E402
from src.services.geo_skills.snapshot_store import SnapshotStore  # noqa: E402
from src.services.geo_skills.tier_classifier import TierClassifier  # noqa: E402
from src.services.geo_skills.vote_builder import VoteBuilder  # noqa: E402
from src.services.geo_skills.vote_resolver import VoteResolver  # noqa: E402

NOVEL_TITLE = "星尘劫"
NOVEL_ID = "336fa0fa-cc30-43c0-8843-2d28a82e755a"

OUT_DIR = Path(
    "/Users/leonfeng/Baiduyun/AISoul/ai-reader-internal/paper/evaluation/contamination-free/novel"
)
OUT_PATH = OUT_DIR / "voting-baseline.json"


def _load_eval_module():
    """Import eval_contamination_free.py as a module to reuse its metrics."""
    spec = importlib.util.spec_from_file_location(
        "eval_contamination_free",
        _BACKEND_DIR / "scripts" / "eval_contamination_free.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def count_cycles(parents: dict[str, str]) -> int:
    """Count distinct cycles in a parent map (same口径 as m5 ablation)."""
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
            cycles += 1
        done.update(path)
    return cycles


class CapturingVoteResolver(VoteResolver):
    """VoteResolver that exposes its own output map (copied from m5 ablation)."""

    def __init__(self) -> None:
        self.captured: dict[str, str] = {}

    async def execute(self, snapshot) -> SkillResult:
        result = await super().execute(snapshot)
        self.captured = {
            k: v for k, v in result.parent_overrides.items() if v is not None
        }
        return result


def load_chapter_fact_aggregates(novel_id: str) -> dict:
    """Characters / aliases / relations from chapter_facts in the SCRATCH DB.

    Identical content to the real DB (it's a byte copy); read here so the
    script is self-contained against the scratch dir.
    """
    conn = sqlite3.connect(_SCRATCH_DB)
    rows = conn.execute(
        "SELECT fact_json FROM chapter_facts WHERE novel_id=? ORDER BY chapter_id",
        (novel_id,),
    ).fetchall()
    conn.close()

    char_counter: Counter[str] = Counter()
    alias_map: dict[str, str] = {}
    rel_set: set[tuple[str, str, str]] = set()
    for (fact_json_text,) in rows:
        try:
            f = json.loads(fact_json_text)
        except Exception:
            continue
        for ch in f.get("characters") or []:
            name = (ch.get("name") or "").strip()
            if name:
                char_counter[name] += 1
            for a in ch.get("new_aliases") or []:
                if isinstance(a, str) and a.strip():
                    alias_map[a.strip()] = name
        for rel in f.get("relationships") or []:
            a = (rel.get("person_a") or "").strip()
            b = (rel.get("person_b") or "").strip()
            t = (rel.get("relation_type") or "").strip()
            if a and b:
                rel_set.add((a, b, t))
    return {
        "characters_counter": char_counter,
        "alias_map": alias_map,
        "relations": rel_set,
        "chapter_facts_loaded": len(rows),
    }


async def run_voting_arm(novel_id: str) -> dict:
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
    orch = GeoOrchestrator(novel_id)
    orch.add_skill("tier", TierClassifier(novel_id))
    orch.add_skill("votes", VoteBuilder(novel_id))
    orch.add_skill("voting", voting)
    async for _ in orch.run():
        pass

    # v0 import, v1 tier, v2 votes, v3 voting
    snap = await store.load_version(novel_id, 3)
    return {
        "voting_parents": dict(voting.captured),
        "location_tiers": dict(snap.location_tiers or {}),
        "parent_votes": {k: dict(v) for k, v in (snap.parent_votes or {}).items()},
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
    print(f"[stardust-voting] scratch DB: {DB_PATH}")

    # Resolve novel_id (fall back to title lookup in the scratch copy)
    novel_id = NOVEL_ID
    conn = sqlite3.connect(_SCRATCH_DB)
    row = conn.execute("SELECT id FROM novels WHERE id=?", (novel_id,)).fetchone()
    if not row:
        row = conn.execute(
            "SELECT id FROM novels WHERE title=?", (NOVEL_TITLE,)
        ).fetchone()
        if not row:
            conn.close()
            sys.exit(f"Novel '{NOVEL_TITLE}' not found in scratch DB.")
        novel_id = row[0]
    conn.close()
    print(f"[stardust-voting] novel_id = {novel_id}")

    arm = await run_voting_arm(novel_id)
    voting_parents: dict[str, str] = arm["voting_parents"]
    tiers: dict[str, str] = arm["location_tiers"]
    votes: dict[str, dict[str, int]] = arm["parent_votes"]
    print(f"[stardust-voting] VoteResolver output: {len(voting_parents)} parent assignments, "
          f"{len(tiers)} tiered locations")

    # ── Structural metrics on the pure voting map ──
    universe = set(tiers) | set(votes) | set(voting_parents) | set(voting_parents.values())
    ch_count = Counter(voting_parents.values())
    top = ch_count.most_common(1)
    roots = sorted(universe - set(voting_parents.keys()))
    cycles = count_cycles(voting_parents)
    orphans = sorted(universe - set(voting_parents.keys()))  # no post-pass to attach them

    # ── 5-dimension accuracy vs gold (same口径 as Table A.1 full row) ──
    eval_mod = _load_eval_module()
    gold_locs = json.loads((OUT_DIR / "gold_standard.json").read_text()).get("locations") or []
    gold_chars = json.loads((OUT_DIR / "gold_characters.json").read_text()).get("characters") or []
    gold_rels = json.loads((OUT_DIR / "gold_relations.json").read_text()).get("relations") or []

    facts = load_chapter_fact_aggregates(novel_id)
    pipe = {
        "location_parents": voting_parents,
        "location_tiers": tiers,
        "characters_counter": facts["characters_counter"],
        "alias_map": facts["alias_map"],
        "relations": facts["relations"],
        "chapter_facts_loaded": facts["chapter_facts_loaded"],
    }
    loc_m = eval_mod.evaluate_locations(gold_locs, pipe)
    char_m = eval_mod.evaluate_characters(gold_chars, pipe)
    rel_m = eval_mod.evaluate_relations(gold_rels, pipe)

    # Same Overall weighting as eval_contamination_free.render_report
    overall = (
        0.2 * loc_m["entity_precision"]
        + 0.1 * char_m["character_recall"]
        + 0.2 * loc_m["tier_accuracy"]
        + 0.3 * loc_m["parent_precision"]
        + 0.2 * loc_m["structural_health"]
    )

    out = {
        "_description": (
            "Rebuttal baseline arm for Table A.1: greedy voting (VoteResolver) "
            "on the synthetic contamination-free novel 《星尘劫》. NO Edmonds MWA, "
            "NO knowledge priors, NO post-processing. Parent map = VoteResolver's "
            "own output only (merged-snapshot ws parents deliberately excluded to "
            "avoid contamination with the full-pipeline output). Accuracy metrics "
            "computed by eval_contamination_free.py's scoring functions, same "
            "weights as the full-pipeline row (Overall = 0.2·EntityP + 0.1·NameAcc "
            "+ 0.2·TierAcc + 0.3·ParentP + 0.2·Struct). Run on a scratch copy of "
            "the DB (AI_READER_DATA_DIR override); real DB untouched."
        ),
        "_script": "backend/scripts/stardust_voting_arm.py",
        "novel": NOVEL_TITLE,
        "novel_id": novel_id,
        "contamination_status": "synthetic (DeepSeek V3 generated, not in LLM pretraining)",
        "arm": "voting (greedy VoteResolver; no Edmonds / no priors / no post-processing)",
        "chapter_facts_loaded": facts["chapter_facts_loaded"],
        "scores": {
            "overall": round(overall, 4),
            "entity_precision": round(loc_m["entity_precision"], 4),
            "entity_recall": round(loc_m["entity_recall"], 4),
            "tier_accuracy": round(loc_m["tier_accuracy"], 4),
            "parent_precision": round(loc_m["parent_precision"], 4),
            "structural_health": round(loc_m["structural_health"], 4),
            "character_recall": round(char_m["character_recall"], 4),
            "relation_pair_recall": round(rel_m["relation_recall"], 4),
        },
        "structural": {
            "max_children": top[0][1] if top else 0,
            "max_children_node": top[0][0] if top else "",
            "root_count": len(roots),
            "roots": roots,
            "cycles": cycles,
            "has_cycle": cycles > 0,
            "orphan_count": len(orphans),
            "total_parents": len(voting_parents),
            "universe_locations": len(universe),
        },
        "counts": {
            "gold_locations": loc_m["gold_location_count"],
            "pipe_locations": loc_m["pipe_location_count"],
            "correctly_extracted": loc_m["correctly_extracted"],
            "hallucinated_by_pipe": loc_m["hallucinated_by_pipe"],
            "parent_total": loc_m["parent_total"],
            "parent_correct": loc_m["parent_correct"],
        },
        "errors": {
            "missing_locations": loc_m["missing_from_pipe"],
            "parent_mismatches_sample": loc_m["parent_mismatches"],
        },
        "full_pipeline_reference": {
            "_source": "benchmark.json (Table A.1 full pipeline row, same novel)",
            "overall": 0.751,
            "max_children": 10,
            "root_count": 1,
            "cycles": 0,
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n[stardust-voting] saved: {OUT_PATH}")
    print(json.dumps({k: out[k] for k in ("scores", "structural")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
