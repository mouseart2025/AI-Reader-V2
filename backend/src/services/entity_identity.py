"""Shared entity-name normalization for aggregation and UI-facing listings."""

from __future__ import annotations

from typing import Iterable, Mapping

from src.extraction.fact_validator import _is_cjk_dominant_name, _normalize_char_variants


def normalize_entity_name(name: str | None) -> str:
    """Trim whitespace and normalize known character variants."""
    text = _normalize_char_variants((name or "").strip())
    return " ".join(text.split())


def entity_identity_key(name: str | None) -> str:
    """Return a merge key for the same entity across harmless spelling variants."""
    text = normalize_entity_name(name)
    if not text:
        return ""
    if _is_cjk_dominant_name(text):
        return text
    return text.casefold()


def same_entity_name(left: str | None, right: str | None) -> bool:
    """Case-insensitive for non-CJK, exact for CJK after variant normalization."""
    left_key = entity_identity_key(left)
    return bool(left_key) and left_key == entity_identity_key(right)


def is_minor_non_cjk_truncation(name: str | None, canonical: str | None) -> bool:
    """Return True for small non-CJK truncations like 'vô dan' -> 'vô danh'."""
    short = normalize_entity_name(name)
    long = normalize_entity_name(canonical)
    if not short or not long or short == long:
        return False
    if _is_cjk_dominant_name(short) or _is_cjk_dominant_name(long):
        return False

    short_tokens = short.split()
    long_tokens = long.split()
    if len(short_tokens) != len(long_tokens):
        return False

    differences = 0
    for short_token, long_token in zip(short_tokens, long_tokens):
        if short_token == long_token:
            continue
        if long_token.startswith(short_token) and len(long_token) - len(short_token) == 1:
            differences += 1
            continue
        return False

    return differences == 1


def choose_display_name(
    names: Iterable[str],
    weights: Mapping[str, int] | None = None,
) -> str:
    """Pick the best display name from a set of equivalent spellings."""
    candidates = {
        normalized
        for raw in names
        if (normalized := normalize_entity_name(raw))
    }
    if not candidates:
        return ""

    weight_map: dict[str, int] = {}
    if weights:
        for raw_name, weight in weights.items():
            normalized = normalize_entity_name(raw_name)
            if normalized:
                weight_map[normalized] = weight_map.get(normalized, 0) + weight

    def score(name: str) -> tuple[int, int, int, int, str]:
        if _is_cjk_dominant_name(name):
            return (
                weight_map.get(name, 0),
                0,
                0,
                len(name),
                name,
            )
        else:
            words = [word for word in name.split() if any(ch.isalpha() for ch in word)]
            case_score = (
                1 if name != name.casefold() else 0,
                sum(1 for word in words if word[:1].isupper()),
            )
        return (
            case_score[0],
            case_score[1],
            weight_map.get(name, 0),
            len(name),
            name,
        )

    return max(candidates, key=score)
