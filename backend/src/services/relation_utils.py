"""Shared relation type normalization and classification utilities.

Used by entity_aggregator and visualization_service.
"""

from __future__ import annotations

# ── Relation type normalization mapping ──
_RELATION_TYPE_NORM: dict[str, str] = {
    # Blood relations
    "父子": "父子", "父女": "父女", "母子": "母子", "母女": "母女",
    "兄弟": "兄弟", "兄妹": "兄妹", "姐弟": "姐弟", "姐妹": "姐妹",
    "叔侄": "叔侄", "祖孙": "祖孙", "婆媳": "婆媳",
    "表兄弟": "表亲", "表姐妹": "表亲", "表亲": "表亲",
    "堂兄弟": "堂亲", "堂姐妹": "堂亲", "堂亲": "堂亲",
    # Intimate
    "夫妻": "夫妻", "夫妇": "夫妻", "恋人": "恋人", "情侣": "恋人",
    "情人": "恋人", "爱人": "恋人", "未婚夫妻": "恋人",
    # Hierarchical
    "师徒": "师徒", "师生": "师徒", "师父与弟子": "师徒",
    "主仆": "主仆", "主人与仆人": "主仆",
    "君臣": "君臣", "上下级": "上下级", "领导与下属": "上下级",
    # Social
    "朋友": "朋友", "友人": "朋友", "好友": "朋友", "挚友": "朋友",
    "同门": "同门", "同门师兄弟": "同门", "同学": "同学",
    "同事": "同事", "邻居": "邻居", "搭档": "搭档", "伙伴": "搭档",
    # Hostile
    "敌人": "敌对", "仇人": "敌对", "死敌": "敌对", "对手": "敌对",
    "仇敌": "敌对", "宿敌": "敌对",
    # Organization
    "同僚": "同僚", "盟友": "盟友",
}


def normalize_relation_type(raw: str) -> str:
    """Normalize a relation type string. Exact match -> contains match -> as-is."""
    if raw in _RELATION_TYPE_NORM:
        return _RELATION_TYPE_NORM[raw]
    for key, norm in _RELATION_TYPE_NORM.items():
        if key in raw:
            return norm
    return raw


# ── Relation category classification ──
_RELATION_CATEGORY: dict[str, str] = {
    "父子": "family", "父女": "family", "母子": "family", "母女": "family",
    "兄弟": "family", "兄妹": "family", "姐弟": "family", "姐妹": "family",
    "叔侄": "family", "祖孙": "family", "婆媳": "family",
    "表亲": "family", "堂亲": "family",
    "夫妻": "intimate", "恋人": "intimate",
    "师徒": "hierarchical", "主仆": "hierarchical", "君臣": "hierarchical",
    "上下级": "hierarchical",
    "朋友": "social", "同门": "social", "同学": "social",
    "同事": "social", "邻居": "social", "搭档": "social",
    "同僚": "social", "盟友": "social",
    "敌对": "hostile",
}


def classify_relation_category(normalized_type: str) -> str:
    """Classify a normalized relation type into a category."""
    if normalized_type in _RELATION_CATEGORY:
        return _RELATION_CATEGORY[normalized_type]
    # Keyword fallback
    if any(kw in normalized_type for kw in ("父", "母", "兄", "姐", "弟", "妹", "叔", "侄", "祖", "孙")):
        return "family"
    if any(kw in normalized_type for kw in ("夫", "妻", "恋", "情")):
        return "intimate"
    if any(kw in normalized_type for kw in ("师", "主", "君", "臣")):
        return "hierarchical"
    if any(kw in normalized_type for kw in ("敌", "仇")):
        return "hostile"
    if any(kw in normalized_type for kw in ("友", "同", "邻")):
        return "social"
    return "other"
