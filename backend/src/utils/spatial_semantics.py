"""Direction semantics for LLM-extracted hierarchy relations.

The extractor frequently emits ``contains`` with "located in" wording::

    {"source": "内布拉斯加州", "target": "美国",
     "value": "内布拉斯加州位于美国中部"}

The rank heuristic fixes most of these because the located entity is smaller,
but same-rank pairs (州 vs 国, region vs region) slip through and invert the
tree. The relation's ``value`` text is the most reliable signal, so read it
before falling back to morphology.

This module is shared by the old WorldStructureAgent vote pass and the v2
VoteBuilder (geo_skills) so both directions cannot drift apart.
"""

from __future__ import annotations

import re

# "A 位于/在/属于 B" → A is inside B.
_LOCATED_IN_RE = re.compile(
    r"位于|处在|坐落|地处|属于|"
    r"在[^，。；]{0,12}?(?:中|内|里|上|内部|境内)|"
    r"是[^，。；]{0,10}?(?:的一部分|内部|上部|顶部|组成部分|舱室|构件)"
)

# "A 包括/下辖 B" → A contains B.
_CONTAINS_RE = re.compile(r"包括|下辖|管辖|境内有|内有|分布着|涵盖|覆盖")

# A continent/ocean/range cannot really sit inside a smaller target. When a
# "located in" phrase says so, the phrase is more likely about a sub-feature
# than about the macro source (e.g. "彩霞山脉位于彩霞山范围内").
_MACRO_SUFFIXES = ("洲", "洋", "大陆", "山脉", "群岛", "高原", "平原")

# Direction results
SOURCE_PARENT = "source_parent"
TARGET_PARENT = "target_parent"


def _non_overlapping_positions(
    value: str, source: str, target: str,
) -> tuple[int, int]:
    """Find source/target positions without one name matching inside the other.

    "大西洋洲位于大西洋海底" used to find 大西洋 at position 0 (inside
    大西洋洲), which made both names look like the subject and lost the cue.
    """
    s_pos = value.find(source)
    t_pos = value.find(target)
    if s_pos < 0 or t_pos < 0:
        return s_pos, t_pos
    if len(source) > len(target) and s_pos <= t_pos < s_pos + len(source):
        t_pos = value.find(target, s_pos + len(source))
    elif len(target) > len(source) and t_pos <= s_pos < t_pos + len(target):
        s_pos = value.find(source, t_pos + len(target))
    return s_pos, t_pos


def contains_direction(
    source: str, target: str, value: str = "",
) -> str | None:
    """Read a ``contains`` edge's real direction from its value text.

    Returns:
      * ``SOURCE_PARENT`` — source contains target (keep the edge as written);
      * ``TARGET_PARENT`` — source is inside target (the edge is inverted);
      * ``None`` — no usable signal, caller should fall back to rank.

    The subject is whichever entity is mentioned *before* the first cue, and
    only the earliest cue counts. That keeps a subordinate clause such as
    "A包括B，B位于C" from being read backwards.
    """
    s = (source or "").strip()
    t = (target or "").strip()
    v = (value or "").strip()
    if not s or not t or not v or s == t:
        return None

    s_pos, t_pos = _non_overlapping_positions(v, s, t)
    if s_pos < 0 and t_pos < 0:
        return None

    cues: list[tuple[int, str]] = []
    m = _LOCATED_IN_RE.search(v)
    if m:
        cues.append((m.start(), TARGET_PARENT))
    m = _CONTAINS_RE.search(v)
    if m:
        cues.append((m.start(), SOURCE_PARENT))
    if not cues:
        return None
    cue_pos, semantics = min(cues, key=lambda item: item[0])

    if s_pos >= 0 and s_pos < cue_pos and (t_pos < 0 or s_pos < t_pos):
        direction = semantics
    elif t_pos >= 0 and t_pos < cue_pos and (s_pos < 0 or t_pos < s_pos):
        # Target is the subject: invert the cue.
        direction = (
            SOURCE_PARENT if semantics == TARGET_PARENT else TARGET_PARENT
        )
    else:
        return None

    if (
        direction == TARGET_PARENT
        and s.endswith(_MACRO_SUFFIXES)
        and not t.endswith(_MACRO_SUFFIXES)
    ):
        return None
    return direction
