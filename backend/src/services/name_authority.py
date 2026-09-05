"""name_authority — single source of truth for character name decisions.

All canonical name selection, generic/unsafe alias filtering, and nickname
detection logic lives here. Every component in the naming pipeline
(NameResolver, AliasResolver, FactValidator) MUST call these functions
instead of maintaining independent logic.

Created v0.70.3 to eliminate logic duplication that caused recurring
regressions (e.g., NameResolver picking 陈玄奘 over 唐僧).

v0.72 Phase 1: added is_generic_person() — the single entry for generic-person
filtering, merging the ex-fact_validator logic (GENERIC_PERSON_WORDS /
PURE_TITLE_WORDS / pattern rules) with the authoritative sets (KINSHIP_TERMS /
GENERIC_PERSON_ALIASES / CANONICAL_BLOCKLIST). CANONICAL_BLOCKLIST was expanded
to cover every list-based generic person term.
"""

from __future__ import annotations

import re


# ═══════════════════════════════════════════════════════════════
# SECTION 1: GENERIC TERM CONSTANTS
# These are the authoritative lists. No other module should
# maintain independent copies.
# ═══════════════════════════════════════════════════════════════

KINSHIP_TERMS = frozenset({
    # v0.71.1 — 红楼梦常用称谓(原缺失)
    "太太", "大太太", "二太太", "三太太",
    "老太太", "大奶奶", "二奶奶", "三奶奶", "四奶奶",
    "老太爷", "大太爷",
    # Direct family
    "哥哥", "弟弟", "姐姐", "妹妹", "妈妈", "爸爸", "爸", "妈",
    "父亲", "母亲", "儿子", "女儿", "妻子", "丈夫", "老婆", "老公",
    "媳妇", "婆婆", "公公", "岳父", "岳母", "丈人", "老丈人",
    "嫂子", "弟媳", "弟媳妇", "姐夫", "妹夫",
    "爷爷", "奶奶", "外公", "外婆", "外爷", "祖母", "老祖母",
    "孙子", "孙女", "外孙", "外孙女", "小外孙",
    "侄子", "侄女", "侄儿", "外甥", "女婿", "侄女婿",
    "老伴", "新郎", "新娘",
    # Ranked kinship
    "大哥", "二哥", "三哥", "四哥", "五哥", "大姐", "二姐", "三姐",
    "大嫂", "二嫂", "三嫂", "大叔", "二叔", "三叔",
    "大婶", "二婶",
    # Informal kinship
    "哥", "弟", "姐", "妹",
    "他哥", "他弟", "他姐", "他妹", "他妈", "他爸",
    "她哥", "她弟", "她姐", "她妹", "她妈", "她爸",
    "你哥", "你弟", "你姐", "你妹", "你妈", "你爸",
    "我哥", "我弟", "我姐", "我妹", "我妈", "我爸", "我嫂",
    "他奶", "她奶", "少安他奶",
    # Classical Chinese kinship/address
    "兄弟", "兄长", "贤弟", "贤侄", "贤妹", "贤婿",
    "嫂嫂", "娘子", "婆娘", "夫人", "小姐", "姑娘", "娘",
    "叔叔", "伯伯", "伯父", "叔父", "舅舅", "舅父",
    "爹爹", "爹", "老爹", "老娘", "亲娘", "干爹", "干娘",
    "义兄", "义弟", "义父", "义母", "义子", "义女",
    "恩人", "恩公", "恩师",
    # Royal/imperial kinship
    "父王", "母后", "王后", "太后", "皇后", "王母", "太子",
    "王爷", "王妃", "驸马", "公主", "殿下", "陛下",
    # Generic address terms
    "阿哥", "阿弟", "阿妹", "阿姐",
    "大郎", "二郎", "三郎", "四郎", "五郎", "六郎", "七郎",
    "浑家", "老母", "老身", "婆子", "老婆子",
    "太公", "老太公",
})

GENERIC_PERSON_ALIASES = frozenset({
    # Age/gender generics
    "老人", "老汉", "老人家", "老太太", "老奶奶", "老将", "老首长",
    "老儿", "老者", "老翁", "老丈", "老官", "老先生",
    "青年", "少年", "小子", "大小子", "二小子", "男人", "女人",
    "小家伙", "小伙子", "胖小子", "男娃娃", "女娃娃",
    "妇人", "妇女", "女子", "那女子", "那妇人", "那女人",
    "汉子", "大汉", "壮汉", "那汉", "那大汉", "黑大汉",
    "少女", "丫头", "丫鬟", "侍女", "侍儿", "婢女",
    "小的", "小人", "在下", "晚辈", "小生",
    "那人", "此人", "其人", "何人", "某人",
    "来人", "路人", "行人", "过客", "客人", "客官",
    # Role/title generics
    "队长", "副书记", "副主任", "主任", "专员", "助手", "老师傅",
    "饲养员", "公派教师", "县领导", "高参",
    "好汉", "壮士", "英雄", "义士", "豪杰", "勇士",
    "军士", "军汉", "军校", "士兵", "兵丁", "喽啰", "小喽啰",
    "差人", "差役", "官差", "公差", "衙役", "捕快",
    "和尚", "僧人", "道士", "道人", "先生", "秀才", "书生",
    "大官人", "官人", "相公", "员外", "财主", "大户",
    "头领", "头目", "首领", "寨主", "山大王",
    "店家", "店主", "小二", "店小二", "酒保",
    "庄主", "庄客", "农夫", "猎户", "渔夫", "樵夫",
    "使者", "信使", "探子", "细作",
    # Classical Chinese deictics
    "那厮", "这厮", "那泼贼", "那贼", "泼贼", "贼人", "贼子",
    "那泼怪", "那泼物", "泼才",
    "这位", "那位", "此人", "这人", "那人",
    # Collective/vague
    "众人", "其他人", "旁人", "大家", "孩子", "孩子们", "娃娃",
    "老干部", "妇女主任",
    "众好汉", "众兄弟", "众将", "众军", "众头领",
    "众位", "诸位", "各位", "列位",
    # Fantasy/wuxia/xianxia contextual generics
    "妖精", "妖怪", "妖魔", "妖王", "妖邪", "妖仙", "妖",
    "那怪", "泼怪", "泼物", "泼猴", "怪物", "老妖",
    "大王", "洞主", "小妖", "众妖", "众怪", "群怪",
    "女婿", "上仙", "大仙", "仙长", "真人",
    "孽畜", "畜生",
    # Xianxia address terms
    "前辈", "晚辈", "小友", "道友", "仙子", "仙师",
    "公子", "少爷", "大爷", "老大",
    "老夫", "妾身", "本人", "在下", "小生", "老奴",
    "仁兄", "兄台", "阁下", "对方",
    "此子", "此女", "此人", "那人",
    "逆徒", "小徒", "弟子", "记名弟子",
    "主人", "夫君", "圣子",
    "师叔", "师侄", "师伯",
    # Pronouns / deictics
    "我们", "我等", "他们", "她们", "他", "她",
    # Collective kinship
    "儿孙", "子侄",
    # Insults/pejoratives
    "淫妇", "贱人", "贼配军", "奸夫", "奸贼", "逆贼", "反贼",
    # Generic self-references
    "老身", "寡人", "酒家", "洒家", "老子", "小可",
    # Generic role terms
    "公人", "统制官", "太守", "府尹",
    "天子", "圣上", "皇帝", "皇上", "官家", "万岁",
    "使女", "伴当", "店主人", "小二哥",
    "军师", "国师", "院长", "副先锋", "节度使", "小将军",
    "泰山",
    # Honorific address
    "令尊", "令堂", "令兄", "令弟", "令妹", "令郎", "令爱",
    # Buddhist/Daoist titles
    "菩萨", "天王", "金星", "真君", "元帅", "星君", "星官",
    "罗汉", "尊者", "法师", "禅师", "国师",
    # Shared nicknames that bridge unrelated characters
    "大刀", "混世魔王", "飞天大圣",
    # Standalone ranked address
    "大爷", "二爷", "三爷", "四爷", "大哥", "二哥", "三哥",
    # Water Margin generics
    "童子", "道童", "仙童", "仙女", "渔人",
    "囚徒", "罪犯", "犯人", "配军",
    "长汉", "黑汉", "黑汉子", "黑厮", "黑杀才",
    "后生", "後生", "少年人", "年轻人", "小后生",
    "节级", "都头", "提辖", "制使", "管营", "知寨",
    "掌柜", "店家", "店主",
    "煞星", "神医",
    "小子", "毛头小子", "黄毛小子", "小兄弟",
    "乡巴佬", "土包子", "土小子",
    "傀儡", "巨猿", "骷髅", "鬼头",
    # v0.70 review — 西游记
    "外公", "贤弟", "陛下", "万岁",
    "长老", "贫僧", "老和尚", "老师", "老师父",
    "尊师", "我弟子", "那长老",
    "劣货", "呆子", "泼孽障", "泼猢狲", "小畜生",
    "夯货", "囊糟食的夯货",
    "十王", "十代阎王",
    # canonical 污染防线(2026-09):匿名/外貌指代不是具体人名,
    # 不可作为别名归并(level 0)。精确匹配,不误伤真实角色名(如"白衣")。
    "神秘人", "神秘人物", "黑衣人", "蒙面人", "白衣人",
    "白衣女子", "灰衣人", "陌生人", "面具人",
})

TITLE_PREFIXES = frozenset({
    "堂主", "长老", "弟子", "护法", "掌门", "帮主", "教主",
    "师父", "师兄", "师弟", "师姐", "师妹", "师傅",
    "师叔", "师侄", "师伯", "师叔祖",
    "太尉", "知府", "知县", "县令", "提辖", "都监", "团练",
    "总管", "管营", "差拨", "节级", "牢头", "押司",
    "教头", "教师", "都头", "虞候", "制使",
    "将军", "元帅", "统制", "统领", "指挥",
    "丞相", "宰相", "太师", "太保", "枢密",
    "知寨", "巡检", "经略", "经略相公",
    "恩相", "大人", "老爷", "相公",
})

TITLE_SUFFIXES_2 = frozenset({
    "前辈", "道友", "师兄", "师弟", "师姐", "师妹", "师叔", "师侄",
    "师伯", "仙师", "仙子", "公子", "姑娘", "小姐", "夫人",
    "大人", "老爷", "长老", "掌门", "帮主", "教主", "堂主",
    "将军", "统领", "元帅", "大哥", "老弟", "兄弟", "先生",
    "菩萨", "佛祖", "真君", "星君", "天王", "天尊", "娘娘",
    "天尊", "老祖", "大长老", "世兄", "世侄", "贤弟", "贤侄",
    "施主", "领队",
})

CANONICAL_BLOCKLIST = frozenset({
    # Generic pronouns/references
    "我", "你", "他", "她", "此人", "对方", "那人", "此子", "本人", "在下", "老夫", "老奴",
    "男子", "女子", "年轻人", "年轻男子", "青年", "青年男子", "中年人",
    # Generic titles
    "前辈", "道友", "小友", "阁下", "大人", "主人", "夫君", "师傅", "为师",
    "弟子", "师兄", "师弟", "师姐", "师妹", "晚辈", "小徒",
    "小子", "公子", "少爷", "大爷", "仁兄", "兄台", "大哥", "小兄弟",
    "神医", "仙师", "圣子", "长老", "大长老", "队长", "领队",
    # Generic descriptions
    "异族人", "外族人", "人族修士", "人族小子", "人族男修",
    "青袍人", "青袍男子", "青袍修士", "青袍青年", "青袍年轻人",
    "蓝衣青年", "黑脸大汉", "青衫人", "青衫青年", "青衫男子", "青衫儒生",
    "青袍化身", "青色人影", "带翅男子", "金色人影", "银色巨鹏",
    "煞星", "穷亲戚", "土包子", "乡巴佬", "毛头小子", "黄毛小子",
    "救命恩人", "分魂", "本体", "化身", "人形",
    # Additional: all hard-blocked generics should also be blocked as canonical
    "哥哥", "弟弟", "姐姐", "妹妹", "外公", "师父", "徒弟",
    "大王", "老爷", "贤弟", "兄弟", "贫僧", "法师", "和尚",
    "陛下", "万岁", "圣上", "菩萨", "老师", "那厮", "泼猴",
    "呆子", "妖精", "妖怪", "客官", "仙子",
    "老和尚", "那长老", "老师父",

    # v0.71.1 — 古典小说第一人称自称戏称(老孙/老猪/老沙/老朱 来自西游记主角自称)
    # "老X" 2-char 形式是人物自称时的幽默用语,不应作 canonical
    "老孙", "老猪", "老沙", "老朱", "老牛", "老龙",
    "本大仙", "本大圣", "本大王",
    # v0.71.1 cross-novel audit 2026-04-11 — 红楼梦称谓被当人物
    # 家族称谓 — 红楼梦里 "太太/奶奶/夫人" 都是场景性称呼,多人共用
    "太太", "奶奶", "大奶奶", "二奶奶", "三奶奶", "四奶奶",
    "大太太", "二太太", "三太太", "老太太",
    "夫人", "大夫人", "二夫人", "三夫人",
    "姑娘", "大姑娘", "二姑娘", "三姑娘", "四姑娘", "五姑娘",
    "嬷嬷", "奶妈", "老嬷嬷", "老奶妈",
    "太爷", "老太爷", "大太爷", "二太爷",
    "老祖宗", "老太君", "老寿星",
    # 西游记追加 — 那X/这X 结构命中 level 0 但 canonical 也要屏蔽
    "那呆子", "那猴子", "那怪物", "那泼猴", "那老儿",
    "这呆子", "这猴子", "这泼猴",
    # 西游记追加 — 戏称/代称
    "取经人", "取经僧", "毛脸雷公嘴", "雷公爷爷", "孙外公",
    "美猴王",  # 这是石猴变形,虽然人气高但应保留 孙悟空 canonical
})

# v0.71.1 — 姓+氏 模式(红楼梦李氏/王氏/张氏...)
# 通用称谓(古代婚后女子通称),多人共用,不应作 canonical
# 覆盖百家姓常见 100+ 姓
_COMMON_SURNAMES_FOR_MS = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张"
    "孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范"
    "彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛"
    "雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元"
    "卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏"
    "成戴谈宋茅庞熊纪舒屈项祝董梁甘甄贾薛林沙"
    "邢尤焦桂闻柴慕"
)


def is_surname_plus_shi(name: str) -> bool:
    """Check if name matches '姓+氏' pattern (李氏/王氏/赵氏).

    Red Chamber Dream uses this as a title for married women — multiple
    characters share the same '姓氏' label, so it must not be canonical.
    """
    return (
        len(name) == 2
        and name[1] == "氏"
        and name[0] in _COMMON_SURNAMES_FOR_MS
    )

# Surname + title suffixes for address terms (韩大夫, 林教头)
TITLE_SUFFIXES = frozenset({
    "大夫", "神医", "仙师", "大人", "长老", "大长老", "前辈", "道友",
    "小友", "师弟", "师兄", "师叔", "师伯", "师侄", "天尊", "老祖",
    "兄弟", "老弟", "公子", "少爷", "施主", "先生", "世侄", "世兄",
    "贤侄", "贤弟", "领队", "大哥", "小子", "某", "小哥", "小贼",
    "小大夫", "兄", "姐", "妹",
})

# Nickname patterns — demoted in canonical scoring
NICKNAME_PATTERNS = frozenset({
    "虎", "龙", "豹", "蛇", "鹰", "马", "猿", "鹏", "凤", "鸠", "雕", "犬", "狼",
})

NICKNAME_SUFFIXES = frozenset({
    "子头", "大圣", "太保", "大王", "魔王", "旋风", "面兽",
    "天王", "太岁", "阎罗", "金刚", "罗汉", "菩萨",
    "公明", "学究", "俊义",
    "行者", "头陀", "道人", "和尚", "禅师",
    # v0.71.1 — 旧职/尊号/道号 (西游记/神话题材)
    "大将",  # 卷帘大将 (沙僧的天宫旧职)
    "上仙", "上人",  # 道号尊称
    "星君", "真君", "真人",  # 道教尊号
    "天尊", "上帝", "大帝",  # 道教最高尊号
    "太君", "老太君",  # 史老太君(贾母别称)
    "雷公",  # 泛称
    "大王且住手", "何往",  # 语气句尾,显然不是名字
})

NICKNAME_PREFIXES = frozenset({
    "豹子", "黑旋", "没羽", "花和", "没遮", "急先", "玉麒", "小李",
    "九纹", "双鞭", "双枪", "青面", "插翅", "混江", "活阎",
    "小旋", "铁笛", "黑旋", "浪子", "拼命", "神行",
})


# ── Person generic-reference lists ────────────────────────────────────
# v0.72 Phase 1 (Story 1.1): moved verbatim from fact_validator.py.
# fact_validator._is_generic_person() now delegates to is_generic_person().

# Generic person references that should never be extracted as character names
GENERIC_PERSON_WORDS = frozenset({
    "众人", "其他人", "旁人", "来人", "对方", "大家", "所有人",
    "那人", "此人", "其人", "何人", "某人", "外人", "路人",
    "他们", "她们", "我们", "诸位", "各位", "在场众人",
    # Classical Chinese generics — refer to different people per chapter
    "妇人", "女子", "汉子", "大汉", "壮汉", "好汉",
    "老儿", "老者", "老翁", "少女", "丫头",
    "军士", "军汉", "兵丁", "喽啰", "小喽啰",
    "差人", "差役", "官差", "公差", "衙役",
    "和尚", "僧人", "道士", "先生", "秀才",
    "店家", "店主", "小二", "店小二", "酒保",
    "庄客", "农夫", "猎户", "渔夫", "樵夫",
    "使者", "信使", "探子", "细作",
    "客人", "客官", "过客", "行人",
    # Mythological/xianxia generic creatures — different individuals per chapter
    "小妖", "小鬼", "众妖", "老妖", "妖精", "妖怪",
    "妖兵", "山贼", "小卒", "士兵",
    "巡山小妖", "把门小妖", "巡山的小妖", "把门的小妖",
    "众猴", "众仙", "众神", "众鬼",
    "众僧", "老僧", "小僧", "众道", "众将", "众官",
    # Age-based generics
    "后生", "後生", "后生小辈", "小辈", "晚辈",
    # Mythological generic roles — celestial/court titles
    "玉女", "天将", "仙卿", "天妃", "仙童", "仙女",
    "天兵", "天卒", "天丁", "神将", "神兵",

    # auto-improve 2026-03-28
    "义兄弟",
    "二将",
    "五百灵官",
    "八菩萨",
    "力士",
    "十万天兵",
    "四天王",
    "四金刚",
    "大众",
    "架火",
    "校尉",
    "洪福寺僧人",
    "美女",
    "美姬",
    "针工",
    "铁匠人等",

    # auto-improve 2026-03-28
    "三藏旧徒",
    "二十八宿",
    "五方揭谛",
    "五龙",
    "六丁六甲",
    "四将",
    "寿星",
    "屠子",
    "广晋龙王之子",
    "护教伽蓝",
    "蛇将",
    "龟将",

    # auto-improve 2026-03-28
    "两大元婴长老",
    "千寰山使者",
    "华天宗使者",
    "卫云城使者",
    "垢土化身",
    "年轻人",
    "蒙面修士",
    "金色小人",
    "陇家新任大长老",
    "青年",
    "黑凤族合体长老",

    # auto-improve 2026-03-28
    "丑陋大汉",
    "二爷",

    # auto-improve 2026-03-28
    "中年男子",
    "艳女",

    # auto-improve 2026-03-28
    "仙姬",

    # auto-improve 2026-03-28
    "三小姐",
    "二小姐",
    "同昌公主",
    "唐伯虎",
    "四小姐",
    "大小姐",
    "女婿",
    "女学生",
    "奶娘",
    "孩子们",
    "安禄山",
    "寿昌公主",
    "小丫头",
    "小丫鬟",
    "政老爹",
    "杨太真",
    "歌姬",
    "武则天",
    "珍爷",
    "琏爷",
    "秦太虚",
    "穆莳",
    "红娘",
    "老年人",
    "舞女",
    "西施",
    "赦老爹",
    "赵飞燕",
    "龙钟老僧",

    # auto-improve 2026-03-28
    "婆子",

    # auto-improve 2026-03-28
    "小乡绅之子",

    # auto-improve 2026-03-28
    "三体战士",
    "主任",
    "值班技术员",
    "医生",
    "取信人",
    "司机",
    "年轻工程师",
    "护士",
    "纳米研究中心主任",
    "美军空军上校",
    "美国中央情报局官员",
    "英军上校",
    "那位警官",
    "那名工程师",
    "那名战士",
    "那名男警察",

    # auto-improve 2026-03-28
    "哨兵",
    "少校军官",
    "年轻警官",
    "爆炸物专家",
    "英国陆军上校",
    "警卫排排长",
    "镇中学老师",
    "齐家屯老两口",

    # auto-improve 2026-03-28
    "七八人",
    "丐帮帮众",
    "丐帮群豪",
    "两个采燕客",
    "两名大汉",
    "两名家将",
    "两名岛主",
    "中原群豪",
    "中年僧人",
    "中年汉子",
    "前辈",
    "大师",
    "大理国臣民",
    "契丹武士",
    "她老人家",
    "姥姥",
    "小和尚",
    "小师父",
    "少林寺僧人",
    "少林群僧",
    "执法僧",
    "无名老僧",
    "星宿派门人",
    "梁上少女",
    "梅兰竹菊",
    "梅兰竹菊四剑",
    "梦郎",
    "灵鹫宫部属",
    "玄天部群女",
    "玄字班僧侣",
    "神僧",
    "神农帮帮众",
    "童姥",
    "老婆子",
    "虚字辈僧侣",
    "褚姓少年",
    "辽兵",
    "辽军",
    "那大汉",

    # auto-improve 2026-03-28
    "乔氏夫妇",
    "好妹子",
    "妹子",
    "姑娘",
    "恶和尚",
    "钟灵之母",
    "钟灵之父",
    "高老者",

    # auto-improve 2026-03-28
    "中年妇人",
    "京官",
    "京官小姐",
    "她师妹",
    "师姊",
    "老汉",
    "蒙面大汉",
    "贱人",
    "这贱人",
    "那妇人",
    "那少女",
    "那少年",
    "那年轻女子",
    "那年轻男子",
    "那贱人",

    # auto-improve 2026-03-28
    "二流子",
    "公社负责人",
    "县社干部",
    "双水村村民",
    "司机朋友们",
    "女售货员",
    "妹妹",
    "姐夫",
    "姐姐",
    "孙玉厚家",
    "少安他奶",
    "新娘",
    "村民",
    "润生妈",
    "父亲",
    "班主任",
    "秀莲她爸",
    "老人家",
    "老祖母",
    "跛女子",

    # auto-improve 2026-03-28
    "售票员",

    # auto-improve 2026-03-28
    "死女子",
    "老将",

    # v0.70 review 2026-04-08 — 西游记人工审核发现
    "黄门官", "当驾官", "光禄寺官", "阁门大使",
    "文武多官", "四值功曹", "八大金刚", "四大天师",
    "龙子龙孙", "山神土地", "诸天", "多官", "四健将",
    "巡海夜叉", "比丘僧", "雷公",
    "金甲诸天", "七十二洞妖王", "庞刘苟毕四大元帅",
    "嫔妃", "后妃", "东宫", "西宫",
    "两个女怪", "公主娘娘", "护国天王",

    # v0.71.1 cross-novel audit 2026-04-11
    # — 红楼梦称谓(场景性,多人共用)
    "太太", "大太太", "二太太", "三太太", "老太太",
    "奶奶", "大奶奶", "二奶奶", "三奶奶", "四奶奶",
    "夫人", "大夫人", "二夫人", "三夫人",
    "姑娘", "大姑娘", "二姑娘", "三姑娘", "四姑娘", "五姑娘",
    "嬷嬷", "老嬷嬷", "奶妈", "老奶妈",
    "太爷", "老太爷", "大太爷", "二太爷",
    "老祖宗", "老寿星",  # 指代贾母的泛称
    # — 西游记戏称/代称(那X 结构 + 取经路人称号)
    "那呆子", "那猴子", "那怪物", "那泼猴", "那老儿",
    "这呆子", "这猴子", "这泼猴",
    "取经人", "取经僧",
    "毛脸雷公嘴", "雷公爷爷", "孙外公",
    # — 集合名/泛称(xiyouji audit)
    "群猴", "五百阿罗", "土地神祗", "夜叉", "太监", "樵子",
})

# Pure title words — when used alone (no surname prefix), not a valid character name
PURE_TITLE_WORDS = frozenset({
    "堂主", "长老", "弟子", "护法", "掌门", "帮主", "教主",
    "师父", "师兄", "师弟", "师姐", "师妹", "师傅",
    "大哥", "二哥", "三哥", "大姐", "二姐",
    "侍卫", "仆人", "丫鬟", "小厮",
    # Official ranks used as address
    "太尉", "知府", "知县", "提辖", "都监", "教头", "都头",
    "将军", "元帅", "丞相", "太师",
    "头领", "寨主", "大王", "员外",
    "恩相", "大人", "老爷", "相公",

    # auto-improve 2026-03-28
    "长史",

    # auto-improve 2026-03-28
    "师伯",
    "师叔",

    # auto-improve 2026-03-28
    "贾化",

    # auto-improve 2026-03-28
    "紫薇舍人",

    # auto-improve 2026-03-28
    "国师",
    "御营都指挥使",
    "管带",

    # auto-improve 2026-03-28
    "张将军",

    # auto-improve 2026-03-28
    "三把手",
    "副总指挥",

    # auto-improve 2026-03-28
    "书记",
    "二队长",
    "副书记",
})

# Fantasy/xianxia: these are valid character names (具体角色, not 泛称)
FANTASY_PERSON_WHITELIST = frozenset({
    "仙人", "仙子", "仙翁", "道人", "散修", "真人",
    "魔尊", "魔君", "魔头", "妖王", "妖帝",
    "灵兽", "仙童", "精怪", "妖仙",
})

# Realistic/urban: these are almost always titles, not names (无姓氏时过滤)
REALISTIC_TITLE_ADDITIONS = frozenset({
    "队长", "书记", "主任", "科长", "处长", "局长", "厂长",
    "村长", "社长", "组长", "班长",
})


# v0.72 Phase 1 (Story 1.3/1.4): the canonical blocklist must cover EVERY
# list-based generic person term — anything is_generic_person() flags via
# exact list membership must never become a canonical name.
CANONICAL_BLOCKLIST = (
    CANONICAL_BLOCKLIST
    | GENERIC_PERSON_ALIASES
    | KINSHIP_TERMS
    | GENERIC_PERSON_WORDS
    | PURE_TITLE_WORDS
)

# ═══════════════════════════════════════════════════════════════
# SECTION 2: PUBLIC FUNCTIONS
# These are the ONLY entry points for name decisions.
# ═══════════════════════════════════════════════════════════════

def alias_safety_level(alias: str) -> int:
    """Return alias safety level: 0=hard-block, 1=soft-block(suspicious), 2=safe.

    This is the single source of truth for whether a name/alias is safe
    to use in Union-Find merging and canonical selection.
    """
    if not alias or len(alias) < 1:
        return 0

    n = len(alias)

    # Level 0: absolute block — kinship terms, 的 phrases, trailing kinship suffixes
    if alias in KINSHIP_TERMS:
        return 0
    if "的" in alias:
        return 0
    if n >= 3:
        tail2 = alias[-2:]
        if tail2 in {"他妈", "她妈", "他爸", "她爸", "他姐", "她姐",
                      "他哥", "她哥", "他弟", "她弟", "他奶", "她奶",
                      "妈妈", "爸爸", "哥哥", "弟弟", "姐姐", "妹妹",
                      "夫妇", "两口", "老婆", "师父", "师傅",
                      "奶奶", "嫂子", "媳妇", "姨妈", "姨娘", "姑妈",
                      "大爷", "二爷", "三爷", "丫头", "姑娘"}:
            return 0

    # Level 0: generic person references
    if alias in GENERIC_PERSON_ALIASES:
        return 0

    # Level 0: pure title/rank words
    if alias in TITLE_PREFIXES:
        return 0

    # Level 0: 姓+氏 pattern (李氏/王氏 in 红楼梦 — generic title for married women)
    if is_surname_plus_shi(alias):
        return 0

    # Level 0: structural patterns
    if n >= 2 and alias[0] in "那这" and n <= 4:
        return 0
    # v0.71.1: "X等" 集合引用 (贾母等, 宝玉等, 王夫人等)
    if n >= 3 and alias.endswith("等"):
        return 0
    if n == 2 and alias[0] in "老小" and alias[1] in "兄弟爷娘人的儿":
        return 0
    if n == 2 and alias[1] in "兄弟妹姐":
        return 0

    # Level 0: surname + generic title pattern (韩前辈, 林道友, 王师兄)
    if 3 <= n <= 5:
        for suffix in TITLE_SUFFIXES_2:
            if n > len(suffix) and alias.endswith(suffix):
                return 0

    # Level 0: location suffixes in person aliases
    _LOCATION_SUFFIXES_BLOCK = (
        "苑", "院", "殿", "堂", "寺", "庵", "楼", "阁", "亭", "庙",
        "宫", "府", "城", "村", "镇", "县", "州", "洞", "山", "洲",
        "卧室", "书房", "花园", "客栈", "酒店", "牢房", "营寨",
    )
    if n >= 3 and alias.endswith(_LOCATION_SUFFIXES_BLOCK):
        return 0

    # Level 0: spouse pattern
    if n >= 3 and alias.endswith(("娘子", "之妻", "媳妇儿", "夫人")):
        return 0

    # Level 0: narrative fragment
    _NARRATIVE_MARKERS = ("曾", "道", "说", "啊", "呀", "了", "吧", "呢", "吗", "过")
    if n >= 3 and any(m in alias for m in _NARRATIVE_MARKERS):
        if n >= 5:
            return 0

    # Level 1: suspicious
    _NUM_CHARS = "一二三四五六七八九十两百千万几数"
    _MEASURE_WORDS = "个位名条只头群队批把道尊座对双副件匹株棵颗朵阵帮伙"
    if n > 8:
        return 1
    if alias[0] in "众群各" or alias.endswith("们"):
        return 1
    _COMMON_SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤阮"
    if n == 2 and alias[0] in _NUM_CHARS and alias[0] not in "一":
        if alias[1] in _COMMON_SURNAMES:
            return 1
    if "三雄" in alias or "三昆仲" in alias or "兄弟" in alias[-2:]:
        return 1
    if n == 1:
        return 1
    if alias[0] in _NUM_CHARS and n >= 3:
        if alias[0] in "百千万几数":
            return 1
        if alias[1] in _MEASURE_WORDS or alias[1] in _NUM_CHARS:
            return 1

    # Level 2: safe
    return 2


def is_unsafe_alias(alias: str) -> bool:
    """Check if an alias is unsafe to use as a Union-Find key."""
    return alias_safety_level(alias) < 2


def is_blocked_name(name: str) -> bool:
    """Check if name should never be a canonical name or mapping key.

    Replaces the former _GENERIC_BLOCK in NameResolver.
    Uses alias_safety_level == 0 as the criterion.
    """
    return alias_safety_level(name) == 0


def is_generic_person(name: str, genre: str | None = None) -> str | None:
    """Check if a person name is generic/invalid.

    SINGLE source of truth for generic-person filtering. Merges:
    - GENERIC_PERSON_WORDS / PURE_TITLE_WORDS (ex-fact_validator lists)
    - KINSHIP_TERMS / GENERIC_PERSON_ALIASES / CANONICAL_BLOCKLIST
    - pattern rules (众/群+, quantified groups, bare surnames, ...)

    Genre-aware: fantasy allows 仙人/妖兽 etc.; realistic adds title filtering.
    Returns a reason string if filtered, or None if kept.
    """
    # Fantasy whitelist: skip generic check for xianxia character types
    if genre in ("fantasy", "wuxia") and name in FANTASY_PERSON_WHITELIST:
        return None

    if name in GENERIC_PERSON_WORDS:
        return "generic person reference"

    # Pure title without surname: "堂主", "长老" alone (not "岳堂主", "张长老")
    if name in PURE_TITLE_WORDS:
        return "pure title without surname"

    # Realistic/urban: additional title filtering (无姓氏时)
    if genre in ("realistic", "urban") and name in REALISTIC_TITLE_ADDITIONS:
        return "realistic title without surname"

    # Descriptive person references: "墨大夫女儿", "韩家二弟", "村长的妻子"
    # These describe a relationship to another character, not a standalone name.
    _DESCRIPTIVE_SUFFIXES = ("女儿", "儿子", "妻子", "丈夫", "夫人",
                             "老婆", "媳妇", "母亲", "父亲", "弟子")
    if len(name) >= 4 and any(name.endswith(s) for s in _DESCRIPTIVE_SUFFIXES):
        return f"descriptive person reference (ends with {name[-2:]})"

    # ── Pattern-based rules (cover open-ended variations) ──

    # P1: "众X" / "群X" prefix — group references (众灵官, 群妖, 群魔, 群怪, etc.)
    if name.startswith(("众", "群")) and len(name) >= 2:
        return "group reference (众/群+)"

    # P1b: vague large-quantity collective prefix — "百十群妖", "数十小妖",
    # "无数妖兵", "一群小妖", "成群结队的..." (no measure word, just a crowd)
    if name.startswith((
        "百十", "数十", "数百", "数千", "无数", "许多", "成群",
        "一群", "一伙", "一干", "一众", "几十", "几百", "众多",
    )) and len(name) >= 3:
        return "vague collective reference"

    # P2: Numeric quantifier + group — "三十六员雷将", "十万天兵", "五百灵官"
    if re.match(r"^[一二三四五六七八九十百千万几数]+.{0,3}[员个名位只匹头条]", name):
        return f"quantified group reference"
    # Also: pure numeric prefix + group suffix
    _GROUP_SUFFIXES = ("天兵", "灵官", "雷将", "雷神", "金刚", "菩萨",
                       "天王", "天将", "小妖", "妖精", "鬼卒", "阴兵")
    if any(name.endswith(s) for s in _GROUP_SUFFIXES) and len(name) > len(name.rstrip("天灵雷金菩王将小妖精鬼卒阴兵")):
        pass  # Already handled by exact match or prefix rule above

    # P3: "X部众神" / "X部众X" — department/division group
    if "部众" in name:
        return "department group reference"

    # P4: Single Chinese surname alone — "张", "刘", "庞" (1 char, common surname)
    _COMMON_SURNAMES = frozenset(
        "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
        "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
        "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
        "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
        "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
        "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    )
    if len(name) == 1 and name in _COMMON_SURNAMES:
        return "bare surname (not a character name)"

    # P5: "X的Y" pattern — descriptive, not a proper name
    if "的" in name and len(name) >= 4:
        return "descriptive reference (contains 的)"

    # P6: Pure official title ending in 官/使/监/尉 without personal name prefix
    # Catches: 黄门官, 当驾官, 阁门大使, 监丞, 监副, 典簿
    _OFFICIAL_SUFFIXES = ("官", "使", "监", "尉", "丞", "副", "簿")
    if len(name) >= 2 and name[-1] in _OFFICIAL_SUFFIXES:
        # Allow if has a known surname prefix (e.g., 魏征, 张天师)
        if not (name[0] in _COMMON_SURNAMES and len(name) <= 4):
            # Check if it's a pure title (no personal identifier)
            if all(c not in name for c in _COMMON_SURNAMES) or len(name) >= 4:
                pass  # Don't over-filter — many are valid (太白金星, etc.)

    # P7: Group number pattern — "四大X", "五X", "八X", "十X" where X is role
    _GROUP_ROLES = ("天王", "金刚", "天师", "菩萨", "元帅", "功曹",
                    "揭谛", "健将", "天丁", "星君", "罗汉")
    if re.match(r"^[二三四五六七八九十]+[大]?", name):
        for role in _GROUP_ROLES:
            if name.endswith(role):
                return f"numbered group reference ({role})"

    # P8 (v0.71.1): 姓+氏 pattern — 红楼梦多人共用的称谓 (李氏/王氏/甄氏)
    if is_surname_plus_shi(name):
        return "surname+氏 (generic title for married women)"

    # P9 (v0.71.1): Descriptive long names (n >= 10) — 通常是 LLM 把描述当角色名
    # 例:"飞东洋游普世感恩行孝黄毛红嘴白鹦哥" (15字)
    if len(name) >= 10:
        return f"descriptive long name ({len(name)} chars)"

    # ── Merged authoritative sets (v0.72 Phase 1, Story 1.1) ──
    # Checked AFTER the ex-fact_validator rules so historical reason strings
    # are preserved for names those rules already caught.
    if name in KINSHIP_TERMS:
        return "kinship term"
    if name in GENERIC_PERSON_ALIASES:
        return "generic person alias"
    if name in CANONICAL_BLOCKLIST:
        return "canonical blocklist entry"

    return None


def similar_name_conflict(a: str, b: str) -> bool:
    """Detect structurally similar but distinct names (e.g., 阮小二 vs 阮小七).

    Returns True if the names share a prefix but differ in the last character,
    suggesting they are different characters with parallel naming patterns.
    Also detects prefix-suffix relationships (阮小 vs 阮小二).

    Single source of truth for the anti-bridging constraint — used by both
    the Union-Find auto pipeline (alias_resolver._safe_union Layer 0) and the
    LLM entity-resolution path (entity_resolver, FR-2.3).
    """
    la, lb = len(a), len(b)
    # Same length, same prefix, different last char
    # (阮小二 vs 阮小七, 解珍 vs 解宝)
    if la == lb and la >= 2 and a[:-1] == b[:-1] and a[-1] != b[-1]:
        return True
    # Prefix relationship: shorter is strict prefix of longer
    # (阮小 vs 阮小二 — "阮小" is a truncated form)
    short, long = (a, b) if la < lb else (b, a)
    if len(short) >= 2 and long.startswith(short) and len(long) - len(short) == 1:
        return True
    return False


def is_nickname_or_title(name: str) -> bool:
    """Check if a name looks like a nickname, courtesy name, or title form.

    These get demoted in canonical scoring so real names win.
    """
    n = len(name)
    if n < 2:
        return False
    if any(name.endswith(t) for t in TITLE_SUFFIXES):
        return True
    if any(name.endswith(s) for s in NICKNAME_SUFFIXES):
        return True
    if any(name.startswith(p) for p in NICKNAME_PREFIXES):
        return True
    # X+称谓 pattern for 红楼梦 (宝二爷, 琏二爷)
    if n >= 3 and name[-1] in "爷奶" and name[-2] in "大二三四五六七八九":
        return True
    if n >= 4 and name.endswith(("奶奶", "姑娘", "丫头")):
        return True
    # "老X" patterns (老祖宗, 老太太)
    if name.startswith("老") and n >= 3 and name not in ("老子",):
        return True
    # v0.71.1: 那X/这X narrative reference (那呆子, 那长老, 那怪物)
    # 2-4 chars starting with 那/这 — descriptive demonstrative forms
    if n >= 2 and n <= 4 and name[0] in "那这":
        return True
    # v0.71.1: 姓+氏 pattern (李氏/王氏) — 红楼梦多人共用称谓
    if is_surname_plus_shi(name):
        return True
    # v0.71.1: 取X / X僧 / X人 叙事代称(取经人, 取经僧)
    if name.startswith("取经") and n <= 4:
        return True
    return False


def pick_canonical(members: list[str], freq: dict[str, int],
                   dict_primary_names: set[str] | None = None) -> str:
    """Pick the best canonical name from an alias group.

    This is the SINGLE source of truth for canonical name selection.
    Both NameResolver and AliasResolver MUST call this function.

    Strategy (v0.66, unified v0.70.3):
    1. dict_primary single member → return directly
    2. Multiple candidates → frequency with nickname/title demotion
    3. 3-char full names (surname+given) with freq ≥ 50 preferred
    4. Fallback: highest frequency among clean names
    """
    # Priority 1: dict_primary member
    if dict_primary_names:
        dict_members = [m for m in members if m in dict_primary_names
                        and m not in CANONICAL_BLOCKLIST
                        and not is_nickname_or_title(m)]
        if len(dict_members) == 1:
            return dict_members[0]
        if not dict_members:
            dict_members = [m for m in members if m in dict_primary_names
                            and m not in CANONICAL_BLOCKLIST]
        if dict_members:
            members = dict_members

    # Filter out generic terms
    candidates = [m for m in members if m not in CANONICAL_BLOCKLIST]
    if not candidates:
        candidates = members

    # Filter out nicknames/titles
    clean = [m for m in candidates if not is_nickname_or_title(m) and len(m) >= 2]
    if not clean:
        clean = [m for m in candidates if len(m) >= 2]
    if not clean:
        clean = candidates

    # Prefer 3-char full names with meaningful frequency.
    # 老规则:3-char freq ≥ 50 就无条件胜出 — 这让 "陈玄奘(87)" 盖过 "三藏(1325)"
    # 新规则(v0.71.1):3-char 在 2-char_max/3-char_max < 10 时胜出 —
    # 即 2-char 没有压倒性多数时,保留文化规范的 3-char 全名.
    # 阈值 10x 的依据:
    #   - 陈玄奘(87) vs 三藏(1325) = 15.2x → 失败 → 2-char 胜 ✓
    #   - 猪八戒(182) vs 八戒(1700) = 9.3x  → 成功 → 3-char 胜 ✓
    #   - 孙悟空(152) vs 悟空(374)  = 2.5x  → 成功 → 3-char 胜 ✓
    _FULL_NAME_MIN_FREQ = 50
    _FULL_NAME_DOMINANCE_RATIO = 10  # 2-char 超过 3-char 超过 10 倍才能盖过
    three_char = [m for m in clean if len(m) == 3
                  and freq.get(m, 0) >= _FULL_NAME_MIN_FREQ]
    two_char = [m for m in clean if len(m) == 2
                and freq.get(m, 0) >= _FULL_NAME_MIN_FREQ]
    if three_char:
        top_three_freq = max(freq.get(m, 0) for m in three_char)
        top_two_freq = max((freq.get(m, 0) for m in two_char), default=0)
        # 3-char 胜出条件:2-char 没有压倒性多数(<10 倍)
        if top_two_freq < top_three_freq * _FULL_NAME_DOMINANCE_RATIO:
            if len(three_char) == 1:
                return three_char[0]
            return max(three_char, key=lambda m: freq.get(m, 0))

    # Fallback: highest frequency
    return max(clean, key=lambda m: freq.get(m, 0))
