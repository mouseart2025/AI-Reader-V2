"""Shared relation type normalization and classification utilities.

Used by entity_aggregator and visualization_service.
"""

from __future__ import annotations

# ── Relation type normalization mapping ──
_RELATION_TYPE_NORM: dict[str, str] = {
    # Blood relations — parent-child
    "父子": "父子", "父女": "父女", "母子": "母子", "母女": "母女",
    "养父子": "父子", "养父女": "父女", "养母子": "母子", "养母女": "母女",
    "继父子": "父子", "继母子": "母子",
    # Blood relations — siblings
    "兄弟": "兄弟", "兄妹": "兄妹", "姐弟": "姐弟", "姐妹": "姐妹",
    "姊妹": "姐妹", "姊弟": "姐弟",  # 姊=姐 variant
    "义兄弟": "兄弟", "义兄妹": "兄妹", "结拜兄弟": "兄弟",
    # Blood relations — extended family
    "叔侄": "叔侄", "伯侄": "叔侄", "舅甥": "甥舅", "甥舅": "甥舅",
    "姑侄": "姑侄", "姨甥": "甥舅",
    "祖孙": "祖孙", "婆媳": "婆媳", "翁媳": "翁媳",
    "妯娌": "妯娌", "姑嫂": "姑嫂", "连襟": "连襟",
    "嫡庶": "嫡庶",
    # Blood relations — cousin/in-law
    "表兄弟": "表亲", "表姐妹": "表亲", "表亲": "表亲",
    "表兄妹": "表亲", "表姐弟": "表亲",
    "堂兄弟": "堂亲", "堂姐妹": "堂亲", "堂亲": "堂亲",
    "堂兄妹": "堂亲", "堂姐弟": "堂亲",
    "亲家": "亲家", "亲戚": "亲戚", "族人": "族人", "宗族": "族人",
    "亲族": "族人", "远亲": "亲戚",
    # Intimate
    "夫妻": "夫妻", "夫妇": "夫妻", "恋人": "恋人", "情侣": "恋人",
    "情人": "恋人", "爱人": "恋人", "未婚夫妻": "恋人",
    "妾": "夫妻", "侧室": "夫妻", "通房": "夫妻",
    "情敌": "情敌",
    # Hierarchical — master-servant
    "主仆": "主仆", "主人与仆人": "主仆",
    "宾主": "主仆", "主顾": "主仆",
    # Hierarchical — teacher-student
    "师徒": "师徒", "师生": "师徒", "师父与弟子": "师徒",
    # Hierarchical — political
    "君臣": "君臣", "上下级": "上下级", "领导与下属": "上下级",
    # Social
    "朋友": "朋友", "友人": "朋友", "好友": "朋友", "挚友": "朋友",
    "旧友": "朋友", "故交": "朋友", "世交": "世交", "故人": "朋友",
    "同门": "同门", "同门师兄弟": "同门", "同学": "同学",
    "同事": "同事", "邻居": "邻居", "邻里": "邻居",
    "搭档": "搭档", "伙伴": "搭档", "同侪": "同僚",
    "同僚": "同僚", "盟友": "盟友",
    "知己": "朋友", "密友": "朋友", "至交": "朋友",
    # Hostile
    "敌人": "敌对", "仇人": "敌对", "死敌": "敌对", "对手": "敌对",
    "仇敌": "敌对", "宿敌": "敌对", "仇家": "敌对",
    # Special narrative relations
    "梦中相遇": "奇遇", "救命之恩": "恩人", "恩人": "恩人",
    "灌溉": "其他",
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
    # Core family
    "父子": "family", "父女": "family", "母子": "family", "母女": "family",
    "兄弟": "family", "兄妹": "family", "姐弟": "family", "姐妹": "family",
    "叔侄": "family", "祖孙": "family", "婆媳": "family",
    "表亲": "family", "堂亲": "family",
    # Extended family
    "甥舅": "family", "姑侄": "family", "翁媳": "family",
    "妯娌": "family", "姑嫂": "family", "连襟": "family",
    "嫡庶": "family", "亲家": "family", "亲戚": "family", "族人": "family",
    # Intimate
    "夫妻": "intimate", "恋人": "intimate",
    "情敌": "hostile",
    # Hierarchical
    "师徒": "hierarchical", "主仆": "hierarchical", "君臣": "hierarchical",
    "上下级": "hierarchical",
    # Social
    "朋友": "social", "同门": "social", "同学": "social",
    "同事": "social", "邻居": "social", "搭档": "social",
    "同僚": "social", "盟友": "social", "世交": "social",
    "恩人": "social", "奇遇": "social",
    # Hostile
    "敌对": "hostile",
}


def classify_relation_category(normalized_type: str) -> str:
    """Classify a normalized relation type into a category."""
    if normalized_type in _RELATION_CATEGORY:
        return _RELATION_CATEGORY[normalized_type]
    # Keyword fallback
    if any(kw in normalized_type for kw in ("父", "母", "兄", "姐", "弟", "妹", "叔", "侄", "祖", "孙", "婆", "媳", "嫂", "舅", "姑", "族", "亲")):
        return "family"
    if any(kw in normalized_type for kw in ("夫", "妻", "恋", "情")):
        return "intimate"
    if any(kw in normalized_type for kw in ("师", "主", "君", "臣", "仆")):
        return "hierarchical"
    if any(kw in normalized_type for kw in ("敌", "仇")):
        return "hostile"
    if any(kw in normalized_type for kw in ("友", "同", "邻", "盟")):
        return "social"
    return "other"
