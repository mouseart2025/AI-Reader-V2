#!/usr/bin/env python3
"""Rebuild Chroma embeddings for already-stored chapter facts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infra.config import DB_PATH
from src.services import embedding_service


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("novel_id")
    args = parser.parse_args()

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.chapter_num, c.content, f.fact_json
            FROM chapter_facts f
            JOIN chapters c ON c.id = f.chapter_id
            WHERE f.novel_id = ?
            ORDER BY c.chapter_num
            """,
            (args.novel_id,),
        ).fetchall()
    finally:
        connection.close()

    # SQLite facts remain authoritative. Only this novel's derived Chroma
    # collections are deleted, then immediately rebuilt from those facts.
    embedding_service.delete_novel_collections(args.novel_id)
    for row in rows:
        fact = json.loads(row["fact_json"])
        summary = embedding_service.build_fact_summary(fact)
        embedding_service.index_chapter(
            args.novel_id,
            int(row["chapter_num"]),
            str(row["content"]),
            summary,
        )
        embedding_service.index_entities_from_fact(
            args.novel_id,
            int(row["chapter_num"]),
            fact,
        )

    print(f"reindexed_chapters: {len(rows)}")


if __name__ == "__main__":
    main()
