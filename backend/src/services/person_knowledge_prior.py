"""person_knowledge_prior — hardcoded alias groups for well-known classical novels.

Parallels geo_skills.knowledge_prior but for persons instead of locations.

The Union-Find in alias_resolver can only merge groups that share an alias.
For classical novels, the pre-scan LLM often creates SEPARATE primary entries
for the same character (e.g. 孙悟空 / 石猴 / 猴精 in 西游记) because the
narrative uses different names at different chapters. Cross-reference via
new_aliases is unreliable.

This module injects authoritative merge directives: "all names in one list
refer to the same character, merge them unconditionally."

Format: list of alias groups. First name is the preferred canonical display
form when the normal canonical selection can't decide (rare).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── 西游记 (Journey to the West) ──
_XIYOUJI_PERSON_GROUPS: list[list[str]] = [
    # 孙悟空 — 4 个 pre-scan 组融为一
    [
        "孙悟空", "悟空", "行者", "孙行者", "大圣", "齐天大圣",
        "石猴", "美猴王", "弼马温", "猴精", "老孙", "猴王",
        "斗战胜佛", "心猿", "猢狲", "孙大圣", "孙师", "泼猴",
    ],
    # 唐僧 — 三藏/陈玄奘/取经人 合并
    [
        "唐僧", "三藏", "陈玄奘", "玄奘", "圣僧", "唐三藏",
        "取经人", "取经僧", "金蝉子", "唐御弟", "玄奘法师",
        "三藏法师", "江流儿", "长老",  # 长老在这个上下文特指唐僧
    ],
    # 猪八戒
    [
        "猪八戒", "八戒", "悟能", "猪悟能", "猪刚鬣",
        "净坛使者", "天蓬元帅", "那呆子", "呆子", "老猪",
        "猪精", "朱三官儿", "猪师",
    ],
    # 沙僧 — 卷帘大将 合并
    [
        "沙僧", "沙和尚", "沙悟净", "卷帘大将",
        "金身罗汉", "悟净", "老沙", "沙四官儿",
    ],
    # 观音菩萨 — 变体合并
    [
        "观音菩萨", "观世音菩萨", "观音", "观世音",
        "南海菩萨", "南海观音",
    ],
    # 牛魔王
    [
        "牛魔王", "大力王", "平天大圣", "牛王", "老牛",
    ],
    # 白龙马
    [
        "白龙马", "玉龙", "小白龙", "敖烈", "龙马",
    ],
    # 如来佛祖
    [
        "如来佛祖", "如来", "释迦牟尼", "佛祖", "世尊",
    ],
    # 玉皇大帝
    [
        "玉皇大帝", "玉帝", "玉皇", "天尊", "圣帝",
        "昊天金阙玉皇大帝",
    ],
    # 太上老君
    [
        "太上老君", "老君", "道德天尊", "太清道德天尊",
    ],
    # 镇元大仙
    [
        "镇元大仙", "镇元子", "与世同君",
    ],
    # 二郎神
    [
        "二郎神", "二郎真君", "杨戬", "显圣真君",
    ],
    # 哪吒
    [
        "哪吒三太子", "哪吒", "三太子",
    ],
    # 托塔李天王
    [
        "托塔李天王", "李天王", "托塔天王",
    ],
]


# ── 红楼梦 (Dream of the Red Chamber) ──
_HONGLOUMENG_PERSON_GROUPS: list[list[str]] = [
    # 贾宝玉
    [
        "贾宝玉", "宝玉", "宝二爷", "宝哥儿", "怡红公子",
        "绛洞花主", "神瑛侍者", "富贵闲人", "无事忙", "怡红主人",
    ],
    # 贾母 — 史老太君 合并
    [
        "贾母", "史老太君", "老太君", "老祖宗", "老寿星",
        "史太君",
    ],
    # 王熙凤 — 凤姐 合并
    [
        "王熙凤", "凤姐", "熙凤", "凤辣子",
        "琏二奶奶", "凤丫头", "凤哥儿", "凤姐儿",
    ],
    # 薛宝钗
    [
        "薛宝钗", "宝钗", "宝姐姐", "宝丫头", "蘅芜君",
    ],
    # 林黛玉
    [
        "林黛玉", "黛玉", "林妹妹", "潇湘妃子", "颦儿",
        "潇湘子", "绛珠仙草",
    ],
    # 贾探春
    [
        "贾探春", "探春", "三姑娘", "蕉下客", "玫瑰花",
    ],
    # 贾惜春
    [
        "贾惜春", "惜春", "四姑娘", "藕榭", "藕谢",
    ],
    # 贾迎春
    [
        "贾迎春", "迎春", "二姑娘", "菱洲", "二木头",
    ],
    # 李纨
    [
        "李纨", "李宫裁", "稻香老农", "大奶奶",  # 李纨是 贾珠 的遗孀
    ],
    # 史湘云
    [
        "史湘云", "湘云", "枕霞旧友", "云丫头",
    ],
    # 薛姨妈
    [
        "薛姨妈", "薛太太", "姨妈", "姨太太",
    ],
    # 贾元春
    [
        "贾元春", "元春", "元妃", "贵妃", "贤德妃",
    ],
    # 贾惜春等补充
    [
        "王夫人", "王氏夫人",
    ],
    # 妙玉
    [
        "妙玉", "槛外人",
    ],
    # 香菱
    [
        "香菱", "甄英莲", "英莲", "秋菱",
    ],
    # 袭人
    [
        "袭人", "珍珠", "花袭人",
    ],
]


# ── 水浒传 (Water Margin) — 核心主角 ──
_SHUIHU_PERSON_GROUPS: list[list[str]] = [
    ["宋江", "宋公明", "及时雨", "呼保义", "黑三郎", "山东呼保义", "孝义黑三郎"],
    ["卢俊义", "卢员外", "玉麒麟"],
    ["吴用", "吴学究", "加亮先生", "智多星"],
    ["公孙胜", "公孙一清", "入云龙"],
    ["林冲", "豹子头", "林教头"],
    ["武松", "武二郎", "行者武松", "武都头", "武二"],
    ["鲁智深", "鲁达", "鲁提辖", "花和尚"],
    ["李逵", "铁牛", "黑旋风"],
]


# ── 三国演义 (Romance of the Three Kingdoms) — 核心主角 ──
_SANGUO_PERSON_GROUPS: list[list[str]] = [
    ["刘备", "玄德", "刘玄德", "皇叔", "昭烈帝", "先主"],
    ["关羽", "云长", "关云长", "关公", "美髯公", "武圣", "寿亭侯"],
    ["张飞", "翼德", "张翼德"],
    ["诸葛亮", "孔明", "诸葛孔明", "卧龙", "伏龙", "武侯"],
    ["曹操", "孟德", "曹孟德", "魏武", "魏武帝"],
    ["孙权", "仲谋", "孙仲谋", "吴主"],
    ["周瑜", "公瑾", "周公瑾"],
    ["赵云", "子龙", "赵子龙", "常山赵子龙"],
    ["司马懿", "仲达", "司马仲达"],
    ["吕布", "奉先", "温侯"],
]


def get_person_priors(novel_title: str) -> list[list[str]]:
    """Return hardcoded person alias groups for a well-known novel.

    Matches on novel title substring (so "西游记 精校本" still matches).
    Returns [] for unknown novels.
    """
    if not novel_title:
        return []
    if "西游" in novel_title:
        return _XIYOUJI_PERSON_GROUPS
    if "红楼" in novel_title:
        return _HONGLOUMENG_PERSON_GROUPS
    if "水浒" in novel_title:
        return _SHUIHU_PERSON_GROUPS
    if "三国" in novel_title:
        return _SANGUO_PERSON_GROUPS
    return []
