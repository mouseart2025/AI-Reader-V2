"""Shared text feature computation functions.

Used by both chapter_splitter (genre detection) and chapter_classifier
(dialogue detection) to avoid circular imports.
"""

import re

# Dialogue markers: ASCII quotes, CJK brackets
_DIALOGUE_CHARS = set('""「」『』')
_DIALOGUE_LINE_RE = re.compile(r'^[""「]', re.MULTILINE)


def compute_dialogue_ratio(text: str) -> float:
    """Compute ratio of lines starting with dialogue markers.

    Returns a float between 0.0 and 1.0.
    """
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return 0.0
    dialogue_lines = sum(1 for l in lines if _DIALOGUE_LINE_RE.search(l.strip()))
    return dialogue_lines / len(lines)
