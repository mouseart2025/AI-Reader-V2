"""SnapshotStore — versioned persistence for HierarchySnapshot.

Stores snapshot versions in SQLite alongside world_structures.
Enables rollback, A/B comparison, and paper metric tracking.

Schema: hierarchy_snapshots table
  - novel_id TEXT
  - version INT
  - tag TEXT (e.g., "votes", "skeleton", "final")
  - snapshot_json TEXT
  - metrics_json TEXT
  - created_at TEXT
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter

from src.services.geo_skills.snapshot import (
    HierarchyMetrics,
    HierarchySnapshot,
)

logger = logging.getLogger(__name__)

_TABLE_CREATED = False


async def _ensure_table():
    """Create hierarchy_snapshots table if it doesn't exist."""
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    from src.db.sqlite_db import get_connection

    conn = await get_connection()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS hierarchy_snapshots (
                novel_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                tag TEXT NOT NULL DEFAULT '',
                snapshot_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (novel_id, version)
            )
        """)
        await conn.commit()
        _TABLE_CREATED = True
    finally:
        await conn.close()


def _serialize_snapshot(snap: HierarchySnapshot) -> str:
    """Serialize snapshot to JSON string."""
    data = {
        "location_parents": snap.location_parents,
        "location_tiers": snap.location_tiers,
        "parent_votes": {k: dict(v) for k, v in snap.parent_votes.items()},
        "location_frequencies": dict(snap.location_frequencies),
        "chapter_settings": {str(k): v for k, v in snap.chapter_settings.items()},
        "location_chapters": snap.location_chapters,
        "version": snap.version,
        "source": snap.source,
        "timestamp": snap.timestamp,
        "novel_genre_hint": snap.novel_genre_hint,
    }
    return json.dumps(data, ensure_ascii=False)


def _deserialize_snapshot(raw: str) -> HierarchySnapshot:
    """Deserialize snapshot from JSON string."""
    data = json.loads(raw)
    return HierarchySnapshot(
        location_parents=data.get("location_parents", {}),
        location_tiers=data.get("location_tiers", {}),
        parent_votes={
            k: Counter(v) for k, v in data.get("parent_votes", {}).items()
        },
        location_frequencies=Counter(data.get("location_frequencies", {})),
        chapter_settings={
            int(k): v for k, v in data.get("chapter_settings", {}).items()
        },
        location_chapters=data.get("location_chapters", {}),
        version=data.get("version", 0),
        source=data.get("source", ""),
        timestamp=data.get("timestamp", 0.0),
        novel_genre_hint=data.get("novel_genre_hint", ""),
    )


class SnapshotStore:
    """Versioned snapshot persistence."""

    async def save(
        self,
        novel_id: str,
        snapshot: HierarchySnapshot,
        tag: str = "",
    ) -> None:
        """Save a snapshot version. Computes and stores metrics."""
        await _ensure_table()
        from src.db.sqlite_db import get_connection

        metrics = HierarchyMetrics.compute(snapshot)
        snap_json = _serialize_snapshot(snapshot)
        metrics_json = json.dumps({
            "avg_depth": metrics.avg_depth,
            "max_depth": metrics.max_depth,
            "root_count": metrics.root_count,
            "max_children": metrics.max_children,
            "max_children_node": metrics.max_children_node,
            "total_locations": metrics.total_locations,
            "depth_distribution": metrics.depth_distribution,
        }, ensure_ascii=False)

        conn = await get_connection()
        try:
            await conn.execute(
                """INSERT OR REPLACE INTO hierarchy_snapshots
                   (novel_id, version, tag, snapshot_json, metrics_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (novel_id, snapshot.version, tag, snap_json, metrics_json),
            )
            await conn.commit()
            logger.info(
                "Snapshot saved: novel=%s v%d tag=%s %s",
                novel_id[:8], snapshot.version, tag, metrics.summary(),
            )
        finally:
            await conn.close()

    async def load_latest(self, novel_id: str) -> HierarchySnapshot | None:
        """Load the latest snapshot version."""
        await _ensure_table()
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                """SELECT snapshot_json FROM hierarchy_snapshots
                   WHERE novel_id = ? ORDER BY version DESC LIMIT 1""",
                (novel_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return _deserialize_snapshot(row["snapshot_json"])
        finally:
            await conn.close()

    async def load_version(
        self, novel_id: str, version: int,
    ) -> HierarchySnapshot | None:
        """Load a specific snapshot version."""
        await _ensure_table()
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                """SELECT snapshot_json FROM hierarchy_snapshots
                   WHERE novel_id = ? AND version = ?""",
                (novel_id, version),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return _deserialize_snapshot(row["snapshot_json"])
        finally:
            await conn.close()

    async def list_versions(self, novel_id: str) -> list[dict]:
        """List all snapshot versions with metrics (for paper tracking)."""
        await _ensure_table()
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                """SELECT version, tag, metrics_json, created_at
                   FROM hierarchy_snapshots
                   WHERE novel_id = ? ORDER BY version""",
                (novel_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "version": r["version"],
                    "tag": r["tag"],
                    "metrics": json.loads(r["metrics_json"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            await conn.close()

    async def rollback(self, novel_id: str, version: int) -> HierarchySnapshot | None:
        """Rollback to a specific version by loading it and saving as new latest.

        Does NOT delete later versions — they remain for comparison.
        """
        snap = await self.load_version(novel_id, version)
        if not snap:
            return None
        # Get current max version
        await _ensure_table()
        from src.db.sqlite_db import get_connection

        conn = await get_connection()
        try:
            cursor = await conn.execute(
                "SELECT MAX(version) as mv FROM hierarchy_snapshots WHERE novel_id = ?",
                (novel_id,),
            )
            row = await cursor.fetchone()
            max_v = row["mv"] if row and row["mv"] is not None else 0
        finally:
            await conn.close()

        # Save as new version with rollback tag
        new_snap = HierarchySnapshot(
            location_parents=snap.location_parents,
            location_tiers=snap.location_tiers,
            parent_votes=snap.parent_votes,
            location_frequencies=snap.location_frequencies,
            chapter_settings=snap.chapter_settings,
            location_chapters=snap.location_chapters,
            version=max_v + 1,
            source=f"rollback_to_v{version}",
            timestamp=time.time(),
            novel_genre_hint=snap.novel_genre_hint,
        )
        await self.save(novel_id, new_snap, tag=f"rollback_v{version}")
        logger.info(
            "Rollback: novel=%s v%d → v%d",
            novel_id[:8], version, new_snap.version,
        )
        return new_snap


async def snapshot_from_world_structure(novel_id: str) -> HierarchySnapshot:
    """Create initial snapshot from current WorldStructure + chapter facts.

    This is the bridge between old system and new snapshot system.
    """
    from src.db import world_structure_store
    from src.db.sqlite_db import get_connection
    import json as _json
    from src.extraction.fact_validator import _is_generic_location
    from src.services.world_structure_agent import _get_suffix_rank, TIER_ORDER

    ws = await world_structure_store.load(novel_id)
    if not ws:
        return HierarchySnapshot(
            location_parents={}, location_tiers={},
            parent_votes={}, location_frequencies=Counter(),
            chapter_settings={}, location_chapters={},
            source="empty", timestamp=time.time(),
        )

    # Build frequency and chapter data from chapter facts
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id = ? ORDER BY chapter_id",
            (novel_id,),
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()

    loc_freq: Counter = Counter()
    chapter_settings: dict[int, str] = {}
    location_chapters: dict[str, list[int]] = {}
    tiers = ws.location_tiers if ws.location_tiers else {}

    for row in rows:
        data = _json.loads(row["fact_json"])
        ch_id = data.get("chapter_id", 0)
        locations = data.get("locations", [])
        for loc in locations:
            name = loc.get("name", "")
            if name:
                loc_freq[name] += 1
                location_chapters.setdefault(name, []).append(ch_id)
        # Primary setting
        settings = [
            l for l in locations
            if l.get("role") == "setting" and l.get("name")
            and not _is_generic_location(l["name"])
        ]
        if settings:
            best_rank, best_name = 999, ""
            for loc in settings:
                suf = _get_suffix_rank(loc["name"])
                rank = suf if suf is not None else TIER_ORDER.get(
                    tiers.get(loc["name"], "city"), 4)
                if rank < best_rank:
                    best_rank = rank
                    best_name = loc["name"]
            if best_name:
                chapter_settings[ch_id] = best_name
        elif locations:
            for loc in locations:
                ln = loc.get("name", "")
                if ln and not _is_generic_location(ln):
                    chapter_settings[ch_id] = ln
                    break

    return HierarchySnapshot(
        location_parents=dict(ws.location_parents) if ws.location_parents else {},
        location_tiers=dict(ws.location_tiers) if ws.location_tiers else {},
        parent_votes={},  # votes will be built by VoteBuilder skill
        location_frequencies=loc_freq,
        chapter_settings=chapter_settings,
        location_chapters=location_chapters,
        version=0,
        source="world_structure_import",
        timestamp=time.time(),
        novel_genre_hint=ws.novel_genre_hint or "",
    )
