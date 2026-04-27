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
    "义兄弟": "结拜兄弟", "义兄妹": "兄妹",
    "结拜兄弟": "结拜兄弟", "拜把子": "结拜兄弟", "金兰": "结拜兄弟",
    "义结金兰": "结拜兄弟", "把兄弟": "结拜兄弟",
    # Blood relations — extended family
    "叔侄": "叔侄", "伯侄": "叔侄", "舅甥": "甥舅", "甥舅": "甥舅",
    "姑侄": "姑侄", "姨甥": "甥舅",
    "祖孙": "祖孙", "婆媳": "婆媳", "翁媳": "翁媳",
    "妯娌": "妯娌", "姑嫂": "姑嫂", "连襟": "连襟",
    "嫂叔": "嫂叔", "嫂弟": "嫂叔", "叔嫂": "嫂叔", "弟嫂": "嫂叔",
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
    # One-sided / attempted — NOT intimate
    "求亲": "求亲", "招亲": "求亲", "求婚": "求亲", "逼婚": "逼婚",
    "爱慕": "爱慕", "单相思": "爱慕", "倾慕": "爱慕", "暗恋": "爱慕",
    "未遂": "求亲",  # LLM may use the category label as type
    # Hierarchical — master-servant
    "主仆": "主仆", "主人与仆人": "主仆",
    "宾主": "主仆", "主顾": "主仆",
    # Hierarchical — teacher-student
    "师徒": "师徒", "师生": "师徒", "师父与弟子": "师徒",
    # Hierarchical — political
    "君臣": "君臣", "上下级": "上下级", "领导与下属": "上下级",
    "quân thần": "君臣", "vua tôi": "君臣",
    "cấp trên cấp dưới": "上下级", "trên dưới": "上下级",
    "chủ tướng thuộc hạ": "上下级", "chủ tướng": "上下级",
    "bề trên": "上下级", "subordinate": "上下级", "superior": "上下级",
    # Social
    "朋友": "朋友", "友人": "朋友", "好友": "朋友", "挚友": "朋友",
    "旧友": "朋友", "故交": "朋友", "世交": "世交", "故人": "朋友",
    "同门": "同门",
    "师兄弟": "师兄弟", "同门师兄弟": "师兄弟",
    "师兄妹": "师兄弟", "师姐弟": "师兄弟", "师姐妹": "师兄弟",
    "同学": "同学",
    "同事": "同事", "邻居": "邻居", "邻里": "邻居",
    "搭档": "搭档", "伙伴": "搭档", "同侪": "同僚",
    "同僚": "同僚", "盟友": "盟友",
    "知己": "朋友", "密友": "朋友", "至交": "朋友",
    "bạn": "朋友", "bạn bè": "朋友", "bằng hữu": "朋友",
    "đồng minh": "盟友", "minh hữu": "盟友",
    "đồng môn": "同门", "đồng liêu": "同僚", "đồng sự": "同事",
    "friend": "朋友", "ally": "盟友", "colleague": "同事",
    # Hostile
    "敌人": "敌对", "仇人": "敌对", "死敌": "敌对", "对手": "敌对",
    "仇敌": "敌对", "宿敌": "敌对", "仇家": "敌对",
    "kẻ thù": "敌对", "địch": "敌对", "đối địch": "敌对",
    "enemy": "敌对", "rival": "敌对", "hostile": "敌对",
    # Vietnamese / English family and intimate relations
    "cha con": "父子", "mẹ con": "母子", "phụ tử": "父子", "mẫu tử": "母子",
    "anh em": "兄弟", "chị em": "姐妹", "huynh đệ": "兄弟",
    "vợ chồng": "夫妻", "phu thê": "夫妻", "người yêu": "恋人",
    "father": "父子", "mother": "母子", "brother": "兄弟",
    "sister": "姐妹", "spouse": "夫妻", "lover": "恋人",
    # Vietnamese / English teacher-student and service relations
    "thầy trò": "师徒", "sư đồ": "师徒", "chủ tớ": "主仆",
    "master disciple": "师徒", "teacher student": "师徒",
    "master servant": "主仆",
    # Special narrative relations
    "梦中相遇": "奇遇", "救命之恩": "恩人", "恩人": "恩人",
    "灌溉": "其他",
}

_RELATION_TYPE_IDS: dict[str, str] = {
    "父子": "family.parent_child",
    "父女": "family.parent_child",
    "母子": "family.parent_child",
    "母女": "family.parent_child",
    "兄弟": "family.sibling",
    "兄妹": "family.sibling",
    "姐弟": "family.sibling",
    "姐妹": "family.sibling",
    "结拜兄弟": "social.sworn_sibling",
    "叔侄": "family.extended",
    "甥舅": "family.extended",
    "祖孙": "family.grandparent",
    "表亲": "family.cousin",
    "堂亲": "family.cousin",
    "亲戚": "family.relative",
    "族人": "family.clan",
    "夫妻": "intimate.spouse",
    "恋人": "intimate.lover",
    "情敌": "hostile.love_rival",
    "求亲": "social.courtship",
    "逼婚": "hostile.forced_marriage",
    "爱慕": "social.admiration",
    "主仆": "hierarchical.master_servant",
    "师徒": "hierarchical.teacher_student",
    "君臣": "hierarchical.ruler_subject",
    "上下级": "hierarchical.superior_subordinate",
    "朋友": "social.friend",
    "同门": "social.same_sect",
    "师兄弟": "social.fellow_disciple",
    "同学": "social.classmate",
    "同事": "social.colleague",
    "邻居": "social.neighbor",
    "搭档": "social.partner",
    "同僚": "social.colleague",
    "盟友": "social.ally",
    "世交": "social.family_friend",
    "恩人": "social.benefactor",
    "奇遇": "social.encounter",
    "敌对": "hostile.enemy",
    "其他": "other",
}


def normalize_relation_type(raw: str) -> str:
    """Normalize a relation type string. Exact match -> contains match -> as-is."""
    if raw in _RELATION_TYPE_NORM:
        return _RELATION_TYPE_NORM[raw]
    for key, norm in _RELATION_TYPE_NORM.items():
        if key in raw:
            return norm
    return raw


def relation_type_id(raw: str) -> str:
    """Return a stable relation type ID for API/i18n consumers."""
    normalized = normalize_relation_type(raw)
    if normalized in _RELATION_TYPE_IDS:
        return _RELATION_TYPE_IDS[normalized]
    fallback = normalized.strip().casefold().replace(" ", "_") or "unknown"
    return f"relation.{fallback}"


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
    "嫂叔": "family",
    "嫡庶": "family", "亲家": "family", "亲戚": "family", "族人": "family",
    # Marriage — treated as family (primary kinship institution in classic Chinese novels)
    "夫妻": "family",
    # Sworn brotherhood — treated as intimate bond (cultural parallel to family tie)
    "结拜兄弟": "intimate",
    # Intimate — romantic only
    "恋人": "intimate",
    "情敌": "hostile",
    # One-sided / attempted — social, not intimate
    "求亲": "social", "逼婚": "hostile", "爱慕": "social",
    # Hierarchical — vertical master-subordinate bonds only
    "师徒": "hierarchical", "主仆": "hierarchical", "君臣": "hierarchical",
    "上下级": "hierarchical",
    # Social — horizontal peer bonds (same-teacher siblings are peers, not hierarchy)
    "朋友": "social", "同学": "social",
    "同事": "social", "邻居": "social", "搭档": "social",
    "同僚": "social", "盟友": "social", "世交": "social",
    "恩人": "social", "奇遇": "social",
    "同门": "social", "师兄弟": "social",
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
