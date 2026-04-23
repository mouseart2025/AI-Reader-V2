"""Domain label normalization for multilingual extraction outputs.

The current UI and exports still display the legacy Chinese labels, so these
helpers preserve canonical labels while adding stable IDs for i18n rendering.
"""

from __future__ import annotations

import re


def _key(value: str | None) -> str:
    return " ".join((value or "").casefold().strip().split())


def _slug(value: str | None, fallback: str) -> str:
    text = _key(value)
    if not text:
        return fallback
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE).strip("_")
    return text or fallback


def _normalize(raw: str | None, mapping: dict[str, str], fallback: str | None = None) -> str:
    text = (raw or "").strip()
    if not text:
        return fallback or ""
    normalized_key = _key(text)
    if normalized_key in mapping:
        return mapping[normalized_key]
    for needle, value in mapping.items():
        if needle and needle in normalized_key:
            return value
    return text


_EVENT_TYPE_MAP = {
    "战斗": "战斗",
    "成长": "成长",
    "社交": "社交",
    "旅行": "旅行",
    "其他": "其他",
    "角色登场": "角色登场",
    "物品交接": "物品交接",
    "组织变动": "组织变动",
    "关系变化": "关系变化",
    "chiến đấu": "战斗",
    "giao chiến": "战斗",
    "trận chiến": "战斗",
    "chiến sự": "战斗",
    "trưởng thành": "成长",
    "tu luyện": "成长",
    "phát triển": "成长",
    "xã giao": "社交",
    "gặp gỡ": "社交",
    "đối thoại": "社交",
    "du hành": "旅行",
    "hành trình": "旅行",
    "di chuyển": "旅行",
    "khác": "其他",
    "battle": "战斗",
    "combat": "战斗",
    "growth": "成长",
    "social": "社交",
    "travel": "旅行",
    "journey": "旅行",
    "other": "其他",
}

_EVENT_TYPE_IDS = {
    "战斗": "battle",
    "成长": "growth",
    "社交": "social",
    "旅行": "travel",
    "其他": "other",
    "角色登场": "character_appearance",
    "物品交接": "item_transfer",
    "组织变动": "org_change",
    "关系变化": "relation_change",
}


def normalize_event_type(raw: str | None) -> str:
    return _normalize(raw, _EVENT_TYPE_MAP, "其他")


def event_type_id(raw: str | None) -> str:
    return _EVENT_TYPE_IDS.get(normalize_event_type(raw), "other")


_LOCATION_TYPE_MAP = {
    "河流": "河流",
    "江": "江",
    "渡口": "渡口",
    "城市": "城市",
    "城镇": "城镇",
    "村庄": "村庄",
    "山": "山",
    "山脉": "山脉",
    "寺庙": "寺庙",
    "府": "府",
    "湖泊": "湖泊",
    "溪流": "溪流",
    "地点": "地点",
    "区域": "区域",
    "sông": "河流",
    "dòng sông": "河流",
    "con sông": "河流",
    "bến": "渡口",
    "bến sông": "渡口",
    "thành": "城市",
    "thành phố": "城市",
    "kinh đô": "城市",
    "làng": "村庄",
    "núi": "山",
    "chùa": "寺庙",
    "đền": "寺庙",
    "phủ": "府",
    "hồ": "湖泊",
    "suối": "溪流",
    "địa điểm": "地点",
    "khu vực": "区域",
    "river": "河流",
    "ferry": "渡口",
    "ford": "渡口",
    "city": "城市",
    "town": "城镇",
    "village": "村庄",
    "mountain": "山",
    "temple": "寺庙",
    "lake": "湖泊",
    "stream": "溪流",
    "location": "地点",
    "region": "区域",
}

_LOCATION_TYPE_IDS = {
    "河流": "river",
    "江": "river",
    "渡口": "ferry",
    "城市": "city",
    "城镇": "town",
    "村庄": "village",
    "山": "mountain",
    "山脉": "mountain_range",
    "寺庙": "temple",
    "府": "residence",
    "湖泊": "lake",
    "溪流": "stream",
    "地点": "location",
    "区域": "region",
}


def normalize_location_type(raw: str | None) -> str:
    return _normalize(raw, _LOCATION_TYPE_MAP, "地点")


def location_type_id(raw: str | None) -> str:
    label = normalize_location_type(raw)
    return _LOCATION_TYPE_IDS.get(label, f"location.{_slug(label, 'unknown')}")


_ORG_TYPE_MAP = {
    "军队": "军队",
    "朝廷": "朝廷",
    "门派": "门派",
    "宗门": "门派",
    "帮派": "帮派",
    "家族": "家族",
    "国家": "国家",
    "组织": "组织",
    "quân đội": "军队",
    "nghĩa quân": "军队",
    "triều đình": "朝廷",
    "môn phái": "门派",
    "bang phái": "帮派",
    "gia tộc": "家族",
    "quốc gia": "国家",
    "tổ chức": "组织",
    "army": "军队",
    "military": "军队",
    "court": "朝廷",
    "sect": "门派",
    "clan": "家族",
    "faction": "组织",
    "organization": "组织",
    "kingdom": "国家",
}

_ORG_TYPE_IDS = {
    "军队": "army",
    "朝廷": "court",
    "门派": "sect",
    "帮派": "gang",
    "家族": "clan",
    "国家": "state",
    "组织": "organization",
}


def normalize_org_type(raw: str | None) -> str:
    return _normalize(raw, _ORG_TYPE_MAP, "")


def org_type_id(raw: str | None) -> str:
    label = normalize_org_type(raw)
    return _ORG_TYPE_IDS.get(label, f"org.{_slug(label, 'unknown')}")


_ORG_ACTION_MAP = {
    "加入": "加入",
    "离开": "离开",
    "晋升": "晋升",
    "阵亡": "阵亡",
    "叛出": "叛出",
    "逐出": "逐出",
    "出现": "出现",
    "创建": "创建",
    "成立": "成立",
    "gia nhập": "加入",
    "tham gia": "加入",
    "rời": "离开",
    "rời khỏi": "离开",
    "thăng chức": "晋升",
    "hy sinh": "阵亡",
    "tử trận": "阵亡",
    "phản bội": "叛出",
    "trục xuất": "逐出",
    "xuất hiện": "出现",
    "lập": "创建",
    "thành lập": "成立",
    "join": "加入",
    "leave": "离开",
    "promote": "晋升",
    "killed": "阵亡",
    "defect": "叛出",
    "expel": "逐出",
    "appear": "出现",
    "create": "创建",
    "found": "成立",
}


def normalize_org_action(raw: str | None) -> str:
    return _normalize(raw, _ORG_ACTION_MAP, "加入")


_ORG_ACTION_IDS = {
    "加入": "join",
    "离开": "leave",
    "晋升": "promote",
    "阵亡": "killed",
    "叛出": "defect",
    "逐出": "expel",
    "出现": "appear",
    "创建": "create",
    "成立": "found",
}


def org_action_id(raw: str | None) -> str:
    return _ORG_ACTION_IDS.get(normalize_org_action(raw), "join")


_ITEM_ACTION_MAP = {
    "出现": "出现",
    "获得": "获得",
    "使用": "使用",
    "赠予": "赠予",
    "消耗": "消耗",
    "丢失": "丢失",
    "损毁": "损毁",
    "xuất hiện": "出现",
    "nhận": "获得",
    "đạt được": "获得",
    "dùng": "使用",
    "sử dụng": "使用",
    "trao": "赠予",
    "tặng": "赠予",
    "tiêu hao": "消耗",
    "mất": "丢失",
    "hư hại": "损毁",
    "appear": "出现",
    "obtain": "获得",
    "use": "使用",
    "give": "赠予",
    "consume": "消耗",
    "lose": "丢失",
    "destroy": "损毁",
}


def normalize_item_action(raw: str | None) -> str:
    return _normalize(raw, _ITEM_ACTION_MAP, "出现")


_ITEM_ACTION_IDS = {
    "出现": "appear",
    "获得": "obtain",
    "使用": "use",
    "赠予": "give",
    "消耗": "consume",
    "丢失": "lose",
    "损毁": "destroy",
}


def item_action_id(raw: str | None) -> str:
    return _ITEM_ACTION_IDS.get(normalize_item_action(raw), "appear")


_ITEM_TYPE_MAP = {
    "兵书": "兵书",
    "武器": "武器",
    "法宝": "法宝",
    "丹药": "丹药",
    "书信": "书信",
    "物品": "物品",
    "sách lược": "兵书",
    "binh thư": "兵书",
    "vũ khí": "武器",
    "bảo vật": "法宝",
    "đan dược": "丹药",
    "thư": "书信",
    "vật phẩm": "物品",
    "strategy book": "兵书",
    "weapon": "武器",
    "artifact": "法宝",
    "medicine": "丹药",
    "letter": "书信",
    "item": "物品",
}

_ITEM_TYPE_IDS = {
    "兵书": "strategy_book",
    "武器": "weapon",
    "法宝": "artifact",
    "丹药": "medicine",
    "书信": "letter",
    "物品": "item",
}


def normalize_item_type(raw: str | None) -> str:
    return _normalize(raw, _ITEM_TYPE_MAP, raw or "")


def item_type_id(raw: str | None) -> str:
    label = normalize_item_type(raw)
    return _ITEM_TYPE_IDS.get(label, f"item.{_slug(label, 'unknown')}")


_CONCEPT_CATEGORY_MAP = {
    "书": "书籍",
    "书籍": "书籍",
    "book": "书籍",
    "sách": "书籍",
    "功法": "功法",
    "部功法": "功法",
    "本功法": "功法",
    "bộ công pháp": "功法",
    "công pháp": "功法",
    "cultivation_method": "功法",
    "cultivation method": "功法",
    "cultivation technique": "功法",
    "技能": "技能",
    "武技": "技能",
    "武学知识": "技能",
    "道法": "技能",
    "kỹ năng": "技能",
    "skill": "技能",
    "spell": "技能",
    "martial art": "技能",
    "social_status": "身份",
    "身份": "身份",
    "身分": "身份",
    "职务": "身份",
    "职位": "身份",
    "人物称号": "身份",
    "title": "身份",
    "status": "身份",
    "role": "身份",
    "vai trò": "身份",
    "thân phận": "身份",
    "chức vụ": "身份",
    "组织": "组织",
    "tổ chức": "组织",
    "organization": "组织",
    "组织部门": "组织部门",
    "organization_department": "组织部门",
    "组织制度": "组织制度",
    "组织结构": "组织制度",
    "organization_structure": "组织制度",
    "cấu trúc tổ chức": "组织制度",
    "地点": "地点",
    "地點": "地点",
    "địa điểm": "地点",
    "location": "地点",
    "建筑": "建筑",
    "kiến trúc": "建筑",
    "architecture": "建筑",
    "机制": "机制",
    "mechanism": "机制",
    "cơ chế": "机制",
    "药物": "药物",
    "丹药": "药物",
    "thuốc": "药物",
    "medicine": "药物",
    "毒药": "毒药",
    "độc dược": "毒药",
    "poison": "毒药",
    "植物": "植物果实",
    "果实": "植物果实",
    "thực vật/quả": "植物果实",
    "plant": "植物果实",
    "fruit": "植物果实",
    "材料": "材料",
    "material": "材料",
    "武器": "武器",
    "vũ khí": "武器",
    "weapon": "武器",
    "物品": "物品",
    "vật phẩm": "物品",
    "item": "物品",
    "惩罚": "惩罚",
    "punishment": "惩罚",
}

_CONCEPT_CATEGORY_IDS = {
    "书籍": "book",
    "功法": "cultivation_method",
    "技能": "skill",
    "身份": "social_status",
    "组织": "organization",
    "组织部门": "organization_department",
    "组织制度": "organization_structure",
    "地点": "location",
    "建筑": "architecture",
    "机制": "mechanism",
    "药物": "medicine",
    "毒药": "poison",
    "植物果实": "plant_fruit",
    "材料": "material",
    "武器": "weapon",
    "物品": "item",
    "惩罚": "punishment",
}


def normalize_concept_category(raw: str | None) -> str:
    return _normalize(raw, _CONCEPT_CATEGORY_MAP, raw or "")


def concept_category_id(raw: str | None) -> str:
    label = normalize_concept_category(raw)
    return _CONCEPT_CATEGORY_IDS.get(label, f"concept.{_slug(label, 'unknown')}")


_SCENE_TONE_MAP = {
    "战斗": "战斗",
    "紧张": "紧张",
    "悲伤": "悲伤",
    "欢乐": "欢乐",
    "平静": "平静",
    "推理": "推理",
    "恐怖": "恐惧",
    "恐惧": "恐惧",
    "感动": "温馨",
    "温馨": "温馨",
    "愤怒": "愤怒",
    "神秘": "神秘",
    "搞笑": "搞笑",
    "chiến đấu": "战斗",
    "giao chiến": "战斗",
    "căng thẳng": "紧张",
    "hồi hộp": "紧张",
    "buồn": "悲伤",
    "bi thương": "悲伤",
    "vui": "欢乐",
    "vui vẻ": "欢乐",
    "bình lặng": "平静",
    "yên bình": "平静",
    "suy luận": "推理",
    "điều tra": "推理",
    "kinh dị": "恐惧",
    "sợ hãi": "恐惧",
    "cảm động": "温馨",
    "ấm áp": "温馨",
    "giận dữ": "愤怒",
    "bí ẩn": "神秘",
    "hài hước": "搞笑",
    "battle": "战斗",
    "combat": "战斗",
    "tense": "紧张",
    "sad": "悲伤",
    "joyful": "欢乐",
    "happy": "欢乐",
    "calm": "平静",
    "deduction": "推理",
    "reasoning": "推理",
    "horror": "恐惧",
    "fear": "恐惧",
    "moving": "温馨",
    "warm": "温馨",
    "angry": "愤怒",
    "mysterious": "神秘",
    "funny": "搞笑",
}

_SCENE_TONE_IDS = {
    "战斗": "battle",
    "紧张": "tense",
    "悲伤": "sad",
    "欢乐": "joyful",
    "平静": "calm",
    "推理": "deduction",
    "恐惧": "fear",
    "温馨": "warm",
    "愤怒": "angry",
    "神秘": "mysterious",
    "搞笑": "funny",
}


def normalize_scene_tone(raw: str | None) -> str:
    return _normalize(raw, _SCENE_TONE_MAP, "平静")


def scene_tone_id(raw: str | None) -> str:
    label = normalize_scene_tone(raw)
    return _SCENE_TONE_IDS.get(label, f"tone.{_slug(label, 'unknown')}")


_SCENE_EVENT_TYPE_MAP = {
    "对话": "对话",
    "战斗": "战斗",
    "旅行": "旅行",
    "描写": "描写",
    "回忆": "回忆",
    "推理": "推理",
    "调查": "调查",
    "đối thoại": "对话",
    "trò chuyện": "对话",
    "chiến đấu": "战斗",
    "giao chiến": "战斗",
    "di chuyển": "旅行",
    "du hành": "旅行",
    "hành trình": "旅行",
    "miêu tả": "描写",
    "mô tả": "描写",
    "hồi tưởng": "回忆",
    "nhớ lại": "回忆",
    "suy luận": "推理",
    "điều tra": "调查",
    "dialogue": "对话",
    "conversation": "对话",
    "battle": "战斗",
    "combat": "战斗",
    "travel": "旅行",
    "journey": "旅行",
    "description": "描写",
    "descriptive": "描写",
    "flashback": "回忆",
    "memory": "回忆",
    "deduction": "推理",
    "investigation": "调查",
}

_SCENE_EVENT_TYPE_IDS = {
    "对话": "dialogue",
    "战斗": "battle",
    "旅行": "travel",
    "描写": "description",
    "回忆": "flashback",
    "推理": "deduction",
    "调查": "investigation",
}


def normalize_scene_event_type(raw: str | None) -> str:
    return _normalize(raw, _SCENE_EVENT_TYPE_MAP, "描写")


def scene_event_type_id(raw: str | None) -> str:
    label = normalize_scene_event_type(raw)
    return _SCENE_EVENT_TYPE_IDS.get(label, f"scene_event.{_slug(label, 'unknown')}")


_SCENE_TIME_MAP = {
    "早": "早",
    "晨": "早",
    "上午": "早",
    "sáng": "早",
    "morning": "早",
    "午": "午",
    "中午": "午",
    "trưa": "午",
    "noon": "午",
    "晚": "晚",
    "暮": "晚",
    "傍晚": "晚",
    "chiều": "晚",
    "chiều tối": "晚",
    "dusk": "晚",
    "evening": "晚",
    "夜": "夜",
    "đêm": "夜",
    "night": "夜",
}

_SCENE_TIME_IDS = {
    "早": "morning",
    "午": "noon",
    "晚": "dusk",
    "夜": "night",
}


def normalize_scene_time_of_day(raw: str | None) -> str:
    return _normalize(raw, _SCENE_TIME_MAP, "")


def scene_time_of_day_id(raw: str | None) -> str:
    label = normalize_scene_time_of_day(raw)
    return _SCENE_TIME_IDS.get(label, "")


_SCENE_ROLE_MAP = {
    "提及": "提及",
    "mentioned": "提及",
    "mention": "提及",
    "场所": "场所",
    "setting": "场所",
    "location": "场所",
    "主": "主",
    "主角": "主",
    "main": "主",
    "lead": "主",
    "配": "配",
    "配角": "配",
    "supporting": "配",
    "support": "配",
    "出场": "出场",
    "appearance": "出场",
    "present": "出场",
}

_SCENE_ROLE_IDS = {
    "提及": "mentioned",
    "场所": "setting",
    "主": "lead",
    "配": "supporting",
    "出场": "appearance",
}


def normalize_scene_role(raw: str | None) -> str:
    return _normalize(raw, _SCENE_ROLE_MAP, "提及")


def scene_role_id(raw: str | None) -> str:
    label = normalize_scene_role(raw)
    return _SCENE_ROLE_IDS.get(label, f"scene_role.{_slug(label, 'unknown')}")


def normalize_scene_labels(scene: dict) -> dict:
    """Normalize mutable scene display labels and attach stable IDs."""
    tone = normalize_scene_tone(scene.get("emotional_tone"))
    event = normalize_scene_event_type(scene.get("event_type"))
    time_of_day = normalize_scene_time_of_day(scene.get("time_of_day"))
    scene["emotional_tone"] = tone
    scene["emotional_tone_id"] = scene_tone_id(tone)
    scene["event_type"] = event
    scene["event_type_id"] = scene_event_type_id(event)
    scene["time_of_day"] = time_of_day
    scene["time_of_day_id"] = scene_time_of_day_id(time_of_day)
    return scene
