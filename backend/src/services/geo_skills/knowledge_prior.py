"""KnowledgePrior — GeoSkill that injects domain knowledge as high-weight votes.

Uses Claude's knowledge of well-known novels to inject authoritative
parent-child relationships as prior votes. These priors give Edmonds'
algorithm strong signals for relationships that chapter-level extraction
often misses (e.g., "车迟国 is in 西牛贺洲").

This skill uses the LLM to generate priors for any novel, not just
hardcoded ones. For well-known novels, the LLM has strong domain knowledge.
"""

from __future__ import annotations

import json
import logging
from collections import Counter

from src.services.geo_skills.base import GeoSkill
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)

# Prior weight — must be high enough to override noisy chapter votes
# but not so high that it overrides strong evidence from many chapters.
# Typical chapter vote for a correct parent: 5-15 across 100 chapters.
# Prior weight of 20 ensures it wins over noise but loses to strong evidence.
_PRIOR_WEIGHT = 20

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "priors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "child": {"type": "string"},
                    "parent": {"type": "string"},
                },
                "required": ["child", "parent"],
            },
        },
    },
    "required": ["priors"],
}


class KnowledgePrior(GeoSkill):
    """Inject domain knowledge priors — hardcoded or via LLM.

    For well-known novels (西游记, 红楼梦, 水浒传, etc.), uses hardcoded
    geographic knowledge. For unknown novels, falls back to LLM.
    """

    def __init__(self, novel_title: str = ""):
        self._novel_title = novel_title

    @property
    def name(self) -> str:
        return "知识先验"

    @property
    def requires_llm(self) -> bool:
        return False  # hardcoded path doesn't need LLM

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        # Try hardcoded priors first
        priors = self._get_hardcoded_priors(snapshot)
        if priors:
            all_locs = set(snapshot.location_tiers.keys())
            votes: dict[str, Counter] = {}
            accepted = 0
            for child, parent in priors.items():
                if child in all_locs and parent in all_locs:
                    votes.setdefault(child, Counter())[parent] += _PRIOR_WEIGHT
                    accepted += 1
            logger.info(
                "KnowledgePrior (hardcoded): %d/%d priors accepted",
                accepted, len(priors),
            )
            return SkillResult(skill_name=self.name, new_votes=votes)

        # Fallback to LLM for unknown novels
        return await self._llm_priors(snapshot)

    def _get_hardcoded_priors(self, snapshot: HierarchySnapshot) -> dict[str, str]:
        """Return hardcoded priors for well-known novels."""
        title = self._novel_title

        if "西游" in title:
            return _XIYOUJI_PRIORS
        if "红楼" in title:
            return _HONGLOUMENG_PRIORS
        if "水浒" in title:
            return _SHUIHU_PRIORS
        if "三国" in title:
            return _SANGUO_PRIORS
        return {}

    async def _llm_priors(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.infra.llm_client import get_llm_client

        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        # Select important locations (freq≥3) grouped by tier
        continents = sorted(l for l, t in tiers.items() if t == "continent")
        kingdoms = sorted(l for l, t in tiers.items()
                         if t == "kingdom" and freq.get(l, 0) >= 3)
        regions = sorted(l for l, t in tiers.items()
                        if t == "region" and freq.get(l, 0) >= 3)

        # Find uber_root
        uber_root = None
        for l, t in tiers.items():
            if t == "world":
                uber_root = l
                break

        if not uber_root or (not kingdoms and not regions):
            return SkillResult.empty(self.name, "Insufficient data")

        # Build prompt — ask LLM about THIS novel's geography
        prompt = f"""小说「{self._novel_title}」的地理层级关系。

已知大区域（continent级）：{', '.join(continents) if continents else '无'}
已知国/大地点（kingdom级）：{', '.join(kingdoms[:30])}
已知山河区域（region级）：{', '.join(regions[:30])}
顶级节点：{uber_root}

请根据你对这部小说的了解，判断以下关系：
1. 每个 continent 的 parent 是谁？（通常是 {uber_root}）
2. 每个 kingdom 属于哪个 continent？
3. 每个 region 属于哪个 kingdom 或 continent？

规则：
- 只输出你确定的关系（不确定的跳过）
- child 和 parent 必须使用上面列出的名称
- parent 必须比 child 更大（continent > kingdom > region）

输出 JSON："""

        llm = get_llm_client()
        try:
            result, _ = await llm.generate(
                system="你是一个中国古典文学地理专家。请严格按照 JSON 格式输出。",
                prompt=prompt,
                format=_CLASSIFY_SCHEMA,
                temperature=0.1,
                max_tokens=4096,
                timeout=120,
            )
        except Exception as e:
            logger.warning("KnowledgePrior LLM failed: %s", e)
            return SkillResult.empty(self.name, str(e))

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                return SkillResult.empty(self.name, "JSON parse error")

        # Parse and validate priors
        all_locs = set(tiers.keys())
        votes: dict[str, Counter] = {}
        accepted = 0

        for prior in result.get("priors", []):
            child = prior.get("child", "")
            parent = prior.get("parent", "")
            if not child or not parent or child == parent:
                continue
            if child not in all_locs or parent not in all_locs:
                continue
            votes.setdefault(child, Counter())[parent] += _PRIOR_WEIGHT
            accepted += 1

        logger.info(
            "KnowledgePrior: %d priors accepted (weight=%d each)",
            accepted, _PRIOR_WEIGHT,
        )

        return SkillResult(
            skill_name=self.name,
            new_votes=votes,
            llm_calls=1,
        )


# ── Hardcoded priors for classic Chinese novels ──────────────

_XIYOUJI_PRIORS: dict[str, str] = {
    # ── 天下直属（四大部洲+独立区域） ──
    "东胜神洲": "天下", "西牛贺洲": "天下",
    "南赡部洲": "天下", "南膳部洲": "天下",
    "北俱芦洲": "天下",
    "天庭": "天下", "幽冥界": "天下", "南海": "天下",
    # ── 东胜神洲 ──
    "傲来国": "东胜神洲", "花果山": "傲来国", "水帘洞": "花果山",
    "东洋大海": "东胜神洲", "东海": "东胜神洲",
    "天罗地网": "花果山",
    # ── 天庭 ──
    "凌霄宝殿": "天庭", "灵霄宝殿": "天庭", "灵霄殿": "天庭",
    "南天门": "天庭", "东天门": "天庭",
    "兜率宫": "天庭", "瑶池": "天庭", "御马监": "天庭",
    "蟠桃园": "天庭", "通明殿": "天庭", "斗牛宫": "天庭",
    "丹房": "兜率宫", "东极妙岩宫": "天庭",
    # ── 南海（观音道场） ──
    "普陀山": "南海", "落伽山": "南海",
    "南海普陀落伽山": "南海", "潮音洞": "落伽山",
    "竹林": "落伽山", "紫竹林": "落伽山",
    # ── 幽冥界 ──
    "森罗殿": "幽冥界", "十八层地狱": "幽冥界", "翠云宫": "幽冥界",
    # ── 南膳部洲/大唐 ──
    "大唐": "南膳部洲", "东土大唐": "南膳部洲",
    "长安城": "大唐", "长安": "大唐",
    "两界山": "南膳部洲", "五行山": "两界山",
    "皇宫": "长安城", "金銮殿": "皇宫", "金銮宝殿": "皇宫",
    "白玉阶": "金銮殿",
    # ── 西牛贺洲：灵山 ──
    "灵山": "西牛贺洲", "灵山胜境": "西牛贺洲",
    "雷音寺": "灵山", "珍楼": "雷音寺",
    "西天": "灵山", "雷音宝刹": "灵山", "玉真观": "灵山",
    # ── 西牛贺洲：取经路上的国家 ──
    "车迟国": "西牛贺洲", "乌鸡国": "西牛贺洲",
    "朱紫国": "西牛贺洲", "宝象国": "西牛贺洲",
    "乌斯藏国": "西牛贺洲", "祭赛国": "西牛贺洲",
    "比丘国": "西牛贺洲", "灭法国": "西牛贺洲",
    "天竺国": "西牛贺洲", "西梁国": "西牛贺洲",
    "钦法国": "西牛贺洲",
    # ── 西牛贺洲：山川地理 ──
    "狮驼岭": "西牛贺洲", "火焰山": "西牛贺洲",
    "翠云山": "西牛贺洲", "黑风山": "西牛贺洲",
    "平顶山": "西牛贺洲", "号山": "西牛贺洲",
    "万寿山": "西牛贺洲", "碗子山": "西牛贺洲",
    "金皘山": "西牛贺洲", "通天河": "西牛贺洲",
    "流沙河": "西牛贺洲", "黄风岭": "西牛贺洲",
    "麒麟山": "西牛贺洲", "盘丝岭": "西牛贺洲",
    "陷空山": "西牛贺洲", "小雷音寺": "西牛贺洲",
    "六百里钻头号山": "西牛贺洲", "蛇盘山": "西牛贺洲",
    "高山": "西牛贺洲", "峨眉山": "西牛贺洲",
    "五台山": "西牛贺洲", "乱石山": "西牛贺洲",
    "南岭": "西牛贺洲", "西海": "西牛贺洲",
    "西洋大海": "西牛贺洲", "平阳之地": "西牛贺洲",
    "西天路上": "西牛贺洲",
    # ── 西牛贺洲内部子地点 ──
    "三清观": "车迟国", "三清殿": "三清观", "智渊寺": "车迟国",
    "御花园": "乌鸡国", "宝林寺": "乌鸡国",
    "方丈": "宝林寺", "禅堂": "宝林寺", "天王殿": "宝林寺",
    "后宰门": "乌鸡国", "端门": "乌鸡国",
    "朱紫国皇宫": "朱紫国", "五凤楼": "朱紫国皇宫",
    "会同馆": "朱紫国",
    "五庄观": "万寿山", "人参园": "五庄观",
    "莲花洞": "平顶山", "山凹": "平顶山",
    "西廊": "莲花洞", "妖精洞府": "莲花洞",
    "芭蕉洞": "翠云山",
    "碗子山波月洞": "碗子山",
    "金皘洞": "金皘山",
    "狮驼洞": "狮驼岭", "狮驼城": "狮驼岭", "正阳门": "狮驼城",
    "八百里狮驼岭": "狮驼岭",
    "松林": "号山", "云端": "号山",
    "黑风洞": "黑风山", "天井": "黑风洞",
    "陈家庄": "通天河",
    "藏风山凹": "黄风岭", "黑松林": "黄风岭",
    "金阶": "宝象国", "金亭馆驿": "宝象国",
    "獬豸洞": "麒麟山", "前门": "獬豸洞", "剥皮亭": "獬豸洞",
    "无底洞": "陷空山",
    "火云洞": "六百里钻头号山",
    "天竺": "天竺国", "玉华县": "天竺国", "豹头山": "天竺国",
    "金平府": "天竺国", "慈云寺": "金平府", "后园": "慈云寺",
    "玉华王府": "玉华县", "暴纱亭": "玉华王府", "吊桥": "玉华县",
    "福陵山": "乌斯藏国", "云栈洞": "福陵山",
    "乌斯藏国界": "乌斯藏国",
    "小西天": "小雷音寺",
    "乱石山碧波潭": "乱石山",
    "鹰愁涧": "蛇盘山",
    "草科": "平阳之地",
    "水晶宫": "东洋大海",
}

_HONGLOUMENG_PRIORS: dict[str, str] = {
    # ── 天下直属大区域 ──
    "都中": "天下", "金陵": "天下", "本省": "天下",
    "平安州": "天下", "海疆": "天下",
    "太虚幻境": "天下",  # 独立幻境
    "昌明隆盛之邦": "天下",
    # 都中 = 京城（北京），贾府所在
    # 石头城是金陵（南京）的古称，与都中是同级
    "石头城": "都中",  # 在小说语境中石头城常指贾府所在城市
    "荣国府": "都中", "宁国府": "都中",
    "宁荣街": "都中", "铁槛寺": "都中",
    "水月庵": "都中", "贾宅": "都中",
    "贾府": "都中", "北静王府": "都中",
    "东府": "都中",  # 宁国府别称
    "锦衣府": "都中", "神京": "都中",
    "都中城外": "都中", "北府": "都中",
    "王子腾府": "都中", "薛家": "都中",
    # 荣国府内部
    "大观园": "荣国府",
    "贾母处": "荣国府", "贾母正房": "荣国府",
    "凤姐院": "荣国府", "凤姐屋": "凤姐院",
    "凤姐处": "凤姐院", "茶房": "凤姐院", "巧姐处": "凤姐院",
    "王夫人处": "荣国府", "王夫人上房": "荣国府", "王夫人正房": "荣国府",
    "贾政处": "荣国府", "贾政书房": "荣国府",
    "贾赦处": "荣国府", "邢夫人处": "荣国府",
    "赵姨娘处": "荣国府",
    "梨香院": "荣国府", "外书房": "荣国府",
    "南北宽夹道": "荣国府", "荣禧堂": "荣国府",
    "家塾": "荣国府", "学房": "荣国府",
    "正房大院": "荣国府", "仪门": "荣国府", "二门口": "荣国府",
    "荣国府·二门": "荣国府", "荣国府·角门": "荣国府",
    "荣国府·穿堂": "荣国府",
    "薛蟠书房": "荣国府", "宝钗房": "荣国府",
    "袭人房": "荣国府", "宝玉房": "怡红院",
    # 大观园内部
    "怡红院": "大观园", "潇湘馆": "大观园",
    "蘅芜苑": "大观园", "稻香村": "大观园",
    "栊翠庵": "大观园", "秋爽斋": "大观园",
    "藕香榭": "大观园", "沁芳亭": "大观园",
    "紫菱洲": "大观园", "惜春处": "大观园",
    "探春处": "大观园", "迎春处": "大观园",
    "李纨处": "大观园", "宝钗处": "大观园",
    "怡红院·里间": "怡红院",
    "沁芳桥": "大观园", "暖香坞": "大观园",
    "蓼风轩": "大观园", "蓼溆": "大观园",
    "大观园·正门": "大观园", "大观园·角门": "大观园",
    "大观园·夹道": "大观园", "花阴下": "大观园",
    "池上": "大观园", "池中": "大观园", "池边": "大观园",
    "山子石": "大观园", "行宫": "大观园",
    "凸碧山庄": "大观园", "女儿棠": "大观园",
    # 宁国府内部
    "会芳园": "宁国府", "天香楼": "宁国府",
    "议事厅": "宁国府", "宁国府·上房": "宁国府",
    "宗祠": "宁国府", "尤二姐处": "宁国府",
    # 金陵（南京/石头城）
    "姑苏": "金陵", "江南": "金陵",
    # 太虚幻境
    "离恨天": "太虚幻境",
    # 铁槛寺
    "净室": "铁槛寺",
    # 荣国府补充（审核修正）
    "贾母正房": "荣国府", "贾政处": "荣国府",
    "二门口": "荣国府", "宝钗房": "荣国府", "袭人房": "荣国府",
    "薛蟠书房": "荣国府", "正房大院": "荣国府",
    "南北宽夹道": "荣国府",
    "穿夹道": "荣国府",  # 穿夹道在荣国府内
    "王夫人上房": "荣国府",
    # 其他
    "街市": "都中",
    "孙家": "都中城外", "下房": "荣国府",
    "维扬": "天下",  # 扬州古称
    "镇海统制": "海疆",
    "贾家义学": "荣国府",
}

_SHUIHU_PRIORS: dict[str, str] = {
    # ── 天下直属大区域（宋代路/大区） ──
    "山东": "天下", "河北": "天下", "京畿": "天下",
    "河南": "天下", "淮南": "天下", "淮西": "天下",
    "江州": "天下", "华州": "天下",
    "辽国": "天下", "杭州城": "天下",  # 方腊的根据地
    "太原县城": "天下",  # 应为河东路，LLM提取为太原县城
    "陕西": "华州",
    # ── 山东 ──
    "梁山泊": "山东", "济州": "山东", "青州": "山东",
    "阳谷县": "山东", "清风山": "山东", "高唐州": "山东",
    "凌州": "山东", "东昌府": "山东", "泰安州": "山东",
    "昭德": "山东", "琳琅山": "山东",
    # 梁山泊内部
    "忠义堂": "梁山泊", "金沙滩": "梁山泊",
    "聚义厅": "梁山泊", "宋江寨": "梁山泊",
    "梁山泊大寨": "梁山泊", "梁山泊军寨": "梁山泊",
    "水泊大寨": "梁山泊", "朱贵酒店": "梁山泊",
    "后山": "梁山泊", "宛子城": "梁山泊",
    "武冈镇": "梁山泊", "断金亭": "梁山泊",
    # 济州内部
    "郓城县": "济州", "济州府": "济州", "济州城": "济州",
    "石碣村": "济州", "黄泥冈": "济州",
    "宋家庄": "郓城县", "草堂": "宋家庄",
    "还道村": "郓城县", "东溪村": "郓城县",
    "宋家村": "郓城县", "县衙": "郓城县",
    "晁盖庄": "东溪村", "晁家庄": "东溪村",
    "芦花荡": "石碣村",
    # 青州内部
    "二龙山": "青州", "桃花山": "青州", "白虎山": "青州",
    # ── 京畿 ──
    "东京": "京畿", "北京大名府": "京畿", "大名府": "京畿",
    "北京": "京畿", "西京": "京畿", "陈桥驿": "京畿",
    "东平府": "京畿", "常州": "京畿", "京师": "京畿",
    # 东京内部
    "开封府": "东京", "枢密院": "东京", "文德殿": "东京",
    "马行街": "东京", "金梁桥": "东京", "李师师家": "东京",
    "端王宫": "东京", "宿太尉府": "东京",
    "紫宸殿": "东京", "西华门": "东京", "东华门": "东京",
    "蒲东郡": "东京",
    # 北京大名府内部
    "梁中书府": "北京大名府", "大牢": "北京", "留守司": "北京",
    "黄河": "大名府", "飞虎峪": "大名府",
    # ── 河北 ──
    "蓟州": "河北", "沧州": "河北", "卫州": "河北",
    "幽州": "河北", "霸州": "河北", "永清县": "河北",
    "檀州": "河北",
    # 蓟州内部
    "蓟州城": "蓟州", "独龙山": "蓟州", "九宫县": "蓟州",
    "祝家庄": "独龙山", "扈家庄": "独龙山", "李家庄": "独龙山",
    "独龙冈": "独龙山", "二仙山": "九宫县",
    # 沧州内部
    "沧州牢城营": "沧州", "天王堂": "沧州牢城营",
    # ── 河南 ──
    "孟州": "河南", "宛州": "河南",
    "孟州道": "孟州", "孟州城": "孟州", "安平寨": "孟州",
    "快活林": "孟州道",
    "荆湖": "宛州", "荆南": "荆湖",
    # ── 江州 ──
    "浔阳江": "江州", "江州城": "江州", "江州府": "江州",
    "牢城营": "江州", "无为军": "江州",
    "穆太公庄": "江州",
    "点视厅": "牢城营", "单身房": "牢城营", "抄事房": "牢城营",
    # ── 淮南 ──
    "扬州": "淮南",
    # ── 杭州/两浙（方腊势力） ──
    "两浙": "杭州城", "杭州": "两浙", "苏州": "两浙",
    "润州": "两浙", "睦州": "两浙", "歙州": "两浙", "秀州": "两浙",
    "乌龙岭": "两浙",
    "清溪县": "睦州", "帮源洞": "清溪县",
    "江南": "润州", "独松关": "江南", "宣州": "江南",
    "昱岭关": "两浙",
    # ── 河东（田虎势力） ──
    "河东": "太原县城",  # 跟随LLM提取的结构
    "五台山": "河东", "盖州": "河东", "晋宁": "河东",
    "威胜": "河东", "壶关": "河东", "汾阳": "河东",
    "威胜州": "河东", "平遥县": "河东",
    "陵川": "盖州", "高平": "盖州", "阳城": "盖州",
    "文殊寺": "五台山",
    # ── 其他 ──
    "关西": "天下",  # 陕西/关中，不在梁山泊后山
    "延安府": "关西", "渭州": "关西",
    "山西": "天下", "水泊": "山东",
    "南丰": "淮西", "淮西·南丰": "淮西",
    "登州": "天下", "信州": "天下",
    "燕京": "辽国",
    "龙虎山": "信州",
    "官道": "天下", "村镇": "天下",
    "山南军": "天下", "山南": "天下",
    "云安": "天下", "开州": "天下", "沂州": "天下",
    "陕州": "天下", "鳌山": "天下",
    "少华山": "华州",
}

_SANGUO_PRIORS: dict[str, str] = {
    "益州": "天下", "荆州": "天下", "扬州": "天下",
    "冀州": "天下", "豫州": "天下", "兖州": "天下",
    "徐州": "天下", "司州": "天下", "雍州": "天下",
    "成都": "益州", "许昌": "豫州", "洛阳": "司州",
    "长安": "司州", "襄阳": "荆州", "建业": "扬州",
}
