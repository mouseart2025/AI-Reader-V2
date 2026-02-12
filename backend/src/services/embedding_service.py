"""Embedding service: ChromaDB + BGE-base-zh-v1.5 for semantic search."""

import json
import logging
import os
from typing import Any

# Force offline mode — use cached model without contacting HuggingFace
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from src.infra.config import CHROMA_DIR, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# Module-level singletons
_client: chromadb.ClientAPI | None = None
_embed_fn: Any = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _client


def _get_embed_fn() -> SentenceTransformerEmbeddingFunction:
    global _embed_fn
    if _embed_fn is None:
        logger.info("Loading embedding model: %s", EMBEDDING_MODEL)
        _embed_fn = SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL,
            device="mps",  # Apple Silicon GPU acceleration
        )
    return _embed_fn


def _chapters_collection(novel_id: str) -> chromadb.Collection:
    """Get or create the chapters collection for a novel."""
    return _get_client().get_or_create_collection(
        name=f"{novel_id}_chapters",
        embedding_function=_get_embed_fn(),
        metadata={"hnsw:space": "cosine"},
    )


def _entities_collection(novel_id: str) -> chromadb.Collection:
    """Get or create the entities collection for a novel."""
    return _get_client().get_or_create_collection(
        name=f"{novel_id}_entities",
        embedding_function=_get_embed_fn(),
        metadata={"hnsw:space": "cosine"},
    )


def index_chapter(
    novel_id: str,
    chapter_num: int,
    chapter_text: str,
    fact_summary: str | None = None,
) -> None:
    """Index a chapter's text and fact summary into ChromaDB.

    Called after ChapterFact extraction completes for a chapter.
    The chapter text is chunked if too long (ChromaDB has limits).
    """
    col = _chapters_collection(novel_id)

    # Build document: combine fact summary + chapter text excerpt
    # Keep under ~2000 chars for embedding quality
    doc_parts = []
    if fact_summary:
        doc_parts.append(fact_summary[:500])
    doc_parts.append(chapter_text[:1500])
    doc = "\n".join(doc_parts)

    doc_id = f"ch_{chapter_num}"

    # Upsert (handles re-analysis)
    col.upsert(
        ids=[doc_id],
        documents=[doc],
        metadatas=[{"chapter_num": chapter_num, "novel_id": novel_id}],
    )


def index_entities_from_fact(novel_id: str, chapter_num: int, fact_data: dict) -> None:
    """Index entity descriptions from a ChapterFact into ChromaDB.

    Only indexes new or updated entity descriptions.
    """
    col = _entities_collection(novel_id)
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []

    # Characters
    for ch in fact_data.get("characters", []):
        name = ch.get("name", "")
        if not name:
            continue
        parts = [f"人物: {name}"]
        if ch.get("appearance"):
            parts.append(f"外貌: {ch['appearance']}")
        if ch.get("abilities_gained"):
            for ab in ch["abilities_gained"]:
                parts.append(f"能力: {ab.get('name', '')} - {ab.get('description', '')}")
        if ch.get("locations_in_chapter"):
            parts.append(f"出现地点: {', '.join(ch['locations_in_chapter'])}")
        doc = " | ".join(parts)
        ids.append(f"person_{name}")
        docs.append(doc)
        metas.append({"name": name, "type": "person", "first_chapter": chapter_num})

    # Locations
    for loc in fact_data.get("locations", []):
        name = loc.get("name", "")
        if not name:
            continue
        parts = [f"地点: {name}", f"类型: {loc.get('type', '')}"]
        if loc.get("parent"):
            parts.append(f"上级: {loc['parent']}")
        if loc.get("description"):
            parts.append(loc["description"])
        doc = " | ".join(parts)
        ids.append(f"location_{name}")
        docs.append(doc)
        metas.append({"name": name, "type": "location", "first_chapter": chapter_num})

    # Concepts
    for nc in fact_data.get("new_concepts", []):
        name = nc.get("name", "")
        if not name:
            continue
        doc = f"概念: {name} - {nc.get('category', '')} - {nc.get('definition', '')}"
        ids.append(f"concept_{name}")
        docs.append(doc)
        metas.append({"name": name, "type": "concept", "first_chapter": chapter_num})

    # Organizations
    for oe in fact_data.get("org_events", []):
        org_name = oe.get("org_name", "")
        if not org_name:
            continue
        parts = [f"组织: {org_name}", f"类型: {oe.get('org_type', '')}"]
        if oe.get("description"):
            parts.append(oe["description"])
        doc = " | ".join(parts)
        eid = f"org_{org_name}"
        if eid not in ids:
            ids.append(eid)
            docs.append(doc)
            metas.append({"name": org_name, "type": "org", "first_chapter": chapter_num})

    if ids:
        col.upsert(ids=ids, documents=docs, metadatas=metas)


def search_chapters(
    novel_id: str,
    query: str,
    n_results: int = 5,
) -> list[dict]:
    """Semantic search across chapter embeddings.

    Returns list of {chapter_num, distance, document} dicts.
    """
    col = _chapters_collection(novel_id)
    if col.count() == 0:
        return []

    results = col.query(
        query_texts=[query],
        n_results=min(n_results, col.count()),
    )

    matches: list[dict] = []
    if results and results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            matches.append({
                "chapter_num": meta.get("chapter_num", 0),
                "distance": results["distances"][0][i] if results["distances"] else 0,
                "document": results["documents"][0][i] if results["documents"] else "",
            })

    return matches


def search_entities(
    novel_id: str,
    query: str,
    n_results: int = 10,
    entity_type: str | None = None,
) -> list[dict]:
    """Semantic search across entity embeddings.

    Returns list of {name, type, first_chapter, distance, document} dicts.
    """
    col = _entities_collection(novel_id)
    if col.count() == 0:
        return []

    where_filter = {"type": entity_type} if entity_type else None
    results = col.query(
        query_texts=[query],
        n_results=min(n_results, col.count()),
        where=where_filter,
    )

    matches: list[dict] = []
    if results and results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            matches.append({
                "name": meta.get("name", ""),
                "type": meta.get("type", ""),
                "first_chapter": meta.get("first_chapter", 0),
                "distance": results["distances"][0][i] if results["distances"] else 0,
                "document": results["documents"][0][i] if results["documents"] else "",
            })

    return matches


def delete_novel_collections(novel_id: str) -> None:
    """Delete all ChromaDB collections for a novel."""
    client = _get_client()
    for suffix in ("_chapters", "_entities"):
        name = f"{novel_id}{suffix}"
        try:
            client.delete_collection(name)
        except Exception:
            pass  # Collection may not exist


def build_fact_summary(fact_data: dict) -> str:
    """Build a text summary of a ChapterFact for embedding."""
    parts: list[str] = []

    for ch in fact_data.get("characters", []):
        parts.append(f"人物{ch.get('name', '')}出场")

    for evt in fact_data.get("events", []):
        summary = evt.get("summary", "")
        if summary:
            parts.append(summary)

    for rel in fact_data.get("relationships", []):
        parts.append(f"{rel.get('person_a', '')}与{rel.get('person_b', '')}的关系: {rel.get('relation_type', '')}")

    for nc in fact_data.get("new_concepts", []):
        parts.append(f"概念「{nc.get('name', '')}」: {nc.get('definition', '')[:50]}")

    return " | ".join(parts)
