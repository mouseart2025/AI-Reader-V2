"""Hallucination-island filter (issue #30).

Root cause: small local LLMs (e.g. qwen3:8b) can leak pretrained knowledge —
writing characters from other well-known novels (e.g. 《凡人修仙传》) into the
extracted facts of the current novel. Those names never literally appear in the
source text, so a grounding check against the original chapters catches them
reliably, without any extra LLM calls.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from src.db.sqlite_db import get_connection

logger = logging.getLogger(__name__)

# novel_id -> (chapter_count, corpus)
_corpus_cache: dict[str, tuple[int, str]] = {}
_MAX_CORPUS_CACHE = 3

# (novel_id, chapter_count, names_frozenset_hash) -> ungrounded set
_result_cache: dict[tuple[str, int, int], set[str]] = {}
_MAX_RESULT_CACHE = 64

# Names shorter than this cannot be verified reliably (too many spurious
# substring hits), so they are always kept.
MIN_VERIFIABLE_LEN = 2


async def _get_corpus(novel_id: str) -> tuple[int, str]:
    """Concatenate all chapter text of a novel (titles included), cached."""
    conn = await get_connection()
    try:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM chapters WHERE novel_id = ?", (novel_id,)
        )
        row = await cursor.fetchone()
        count: int = row["n"] if row else 0

        cached = _corpus_cache.get(novel_id)
        if cached and cached[0] == count:
            return cached

        cursor = await conn.execute(
            "SELECT title, content FROM chapters WHERE novel_id = ? ORDER BY chapter_num",
            (novel_id,),
        )
        rows = await cursor.fetchall()
        corpus = "\n".join(f"{r['title'] or ''}\n{r['content'] or ''}" for r in rows)

        if len(_corpus_cache) >= _MAX_CORPUS_CACHE:
            _corpus_cache.pop(next(iter(_corpus_cache)))
        _corpus_cache[novel_id] = (count, corpus)
        return (count, corpus)
    finally:
        await conn.close()


def find_ungrounded_names(
    corpus: str, names_aliases: dict[str, set[str]]
) -> set[str]:
    """Return canonical names with zero textual evidence in the corpus.

    A name is grounded when the canonical name OR any of its aliases (length
    >= MIN_VERIFIABLE_LEN) appears literally in the corpus. Names whose every
    candidate is shorter than MIN_VERIFIABLE_LEN are kept (unverifiable).
    """
    ungrounded: set[str] = set()
    for canonical, aliases in names_aliases.items():
        candidates = {canonical, *aliases}
        verifiable = [n for n in candidates if len(n) >= MIN_VERIFIABLE_LEN]
        if not verifiable:
            continue
        if not any(n in corpus for n in verifiable):
            ungrounded.add(canonical)
    return ungrounded


async def get_ungrounded_persons(
    novel_id: str,
    person_names: Iterable[str],
    alias_map: dict[str, str],
) -> set[str]:
    """Find person entities that never literally appear in the novel text.

    Grounding is checked against the FULL book (not the analyzed range) so a
    real character mentioned only in later chapters is never filtered out.
    """
    names = set(person_names)
    if not names:
        return set()

    count, corpus = await _get_corpus(novel_id)
    if not corpus:
        return set()

    cache_key = (novel_id, count, hash(frozenset(names)))
    cached = _result_cache.get(cache_key)
    if cached is not None:
        return set(cached)

    names_aliases: dict[str, set[str]] = {name: set() for name in names}
    for alias, canonical in alias_map.items():
        if canonical in names_aliases:
            names_aliases[canonical].add(alias)

    ungrounded = find_ungrounded_names(corpus, names_aliases)
    if ungrounded:
        logger.info(
            "novel %s: filtered %d ungrounded person entities (issue #30): %s",
            novel_id,
            len(ungrounded),
            sorted(ungrounded),
        )

    if len(_result_cache) >= _MAX_RESULT_CACHE:
        _result_cache.pop(next(iter(_result_cache)))
    _result_cache[cache_key] = set(ungrounded)
    return ungrounded


def invalidate_cache(novel_id: str) -> None:
    """Drop cached corpus/results for a novel (e.g. after re-import)."""
    _corpus_cache.pop(novel_id, None)
    for key in [k for k in _result_cache if k[0] == novel_id]:
        _result_cache.pop(key, None)
