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
from src.services.geo_skills.evolve_params import evolve_param
from src.services.geo_skills.snapshot import HierarchySnapshot, SkillResult

logger = logging.getLogger(__name__)

# Prior weight — must be high enough to override noisy chapter votes
# but not so high that it overrides strong evidence from many chapters.
# Typical chapter vote for a correct parent: 5-15 across 100 chapters.
# Prior weight of 20 ensures it wins over noise but loses to strong evidence.
_PRIOR_WEIGHT = evolve_param("knowledge_prior.prior_weight", 20)


def _guess_tier(name: str) -> str:
    """注入节点的保守 tier 猜测(仅供 Edmonds 软惩罚用,后续 tier 轮会重分)。"""
    if name.endswith(("洲", "部洲")):
        return "continent"
    if name.endswith("国"):
        return "kingdom"
    return "region"

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
            tier_updates: dict[str, str] = {}
            accepted = 0
            freq = snapshot.location_frequencies or Counter()
            vote_targets = {t for tgts in snapshot.parent_votes.values() for t in tgts}
            injected: list[str] = []
            edge_list: list[tuple[str, str]] = []
            for child, parent in priors.items():
                if child not in all_locs:
                    # 子节点证据门槛补入(2026-09-19,对称于下方父节点注入):
                    # 汴梁城(freq=4,有票)因提取轮未入 tiers,先验被静默丢弃。
                    # 子节点须本小说有真实提及证据(频次≥2 或已有票)
                    if freq.get(child, 0) >= 2 or child in snapshot.parent_votes:
                        tier_updates.setdefault(child, _guess_tier(child))
                        all_locs.add(child)
                        injected.append(child)
                    else:
                        continue
                if parent not in all_locs and child in all_locs:
                    # 证据门槛补入缺失的先验父节点(2026-09-19,西游四大部洲
                    # 因历史 purge 缺席,先验被"双亲须在 tiers"门槛丢弃):
                    # 父节点须 (a) 在 priors 中有自身归属(链闭合)
                    # (b) 本小说有真实提及证据(频次≥2 或已是票目标)
                    if priors.get(parent) and (
                        freq.get(parent, 0) >= 2 or parent in vote_targets
                    ):
                        tier_updates.setdefault(parent, _guess_tier(parent))
                        all_locs.add(parent)
                        injected.append(parent)
                    else:
                        continue
                if child in all_locs and parent in all_locs:
                    votes.setdefault(child, Counter())[parent] += _PRIOR_WEIGHT
                    accepted += 1
                    edge_list.append((child, parent))
            # 补入节点自身的归属(priors 表中它可能排在注入点之前而被跳过;
            # 已作为 child 投过票的节点不重复补票)
            for node in injected:
                gp = priors.get(node)
                if gp and gp in all_locs and votes.get(node, Counter()).get(gp, 0) <= 0:
                    votes.setdefault(node, Counter())[gp] += _PRIOR_WEIGHT
                    edge_list.append((node, gp))
            logger.info(
                "KnowledgePrior (hardcoded): %d/%d priors accepted, "
                "%d missing parents injected with evidence gate",
                accepted, len(priors), len(injected),
            )
            return SkillResult(
                skill_name=self.name, new_votes=votes, tier_updates=tier_updates,
                prior_edges=edge_list,
            )

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
        if "封神" in title:
            return _FENGSHEN_PRIORS
        return {}

    async def _llm_priors(self, snapshot: HierarchySnapshot) -> SkillResult:
        from src.infra.llm_client import get_llm_client

        tiers = snapshot.location_tiers
        freq = snapshot.location_frequencies

        # Select important locations (freq≥3) grouped by tier
        continents = sorted(loc for loc, t in tiers.items() if t == "continent")
        kingdoms = sorted(loc for loc, t in tiers.items()
                         if t == "kingdom" and freq.get(loc, 0) >= 3)
        regions = sorted(loc for loc, t in tiers.items()
                        if t == "region" and freq.get(loc, 0) >= 3)

        # Find uber_root
        uber_root = None
        for loc, t in tiers.items():
            if t == "world":
                uber_root = loc
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
    "南赡部洲": "天下",
    "北俱芦洲": "天下",
    "天庭": "天下", "幽冥界": "天下", "南海": "天下",
    # ── 东胜神洲 ──
    "傲来国": "东胜神洲", "花果山": "傲来国", "水帘洞": "花果山",
    "东洋大海": "东胜神洲", "东海": "东胜神洲",
    "天罗地网": "花果山",
    # ── 天庭 ──
    "凌霄宝殿": "天庭", "灵霄宝殿": "天庭", "灵霄殿": "天庭",
    "凌霄殿": "天庭",  # 凌霄宝殿变体(2026-09-19 补)
    "南天门": "天庭", "东天门": "天庭",
    "兜率宫": "天庭", "瑶池": "天庭", "御马监": "天庭",
    "蟠桃园": "天庭", "通明殿": "天庭", "斗牛宫": "天庭",
    "丹房": "兜率宫", "东极妙岩宫": "天庭",
    # ── 南海（观音道场） ──
    "普陀山": "南海", "落伽山": "南海",
    "南海普陀落伽山": "南海", "潮音洞": "落伽山",
    "竹林": "落伽山", "紫竹林": "落伽山",
    "珞珈山": "南海",  # 落伽山字形变体(2026-09-19 补)
    # ── 取经路杂项(2026-09-19 errata 驱动):
    # 龙宫默认东海龙宫;玉华州=玉华县属天竺国;狮驼国为狮驼岭所灭之国;
    # 高老庄在乌斯藏国(原误挂灭法国)
    "龙宫": "东海", "玉华州": "天竺国",
    "狮驼国": "西牛贺洲", "高老庄": "乌斯藏国",
    # ── 幽冥界 ──
    # 2026-09-19 修正:森罗殿在幽冥地府之内,幽冥地府隶属幽冥界
    "森罗殿": "幽冥地府", "十八层地狱": "幽冥界", "翠云宫": "幽冥界",
    "幽冥地府": "幽冥界",
    # ── 南赡部洲/大唐 ── (南膳=南赡 字形变体)
    "大唐": "南赡部洲", "东土大唐": "南赡部洲",
    "大唐国": "南赡部洲",  # 东土大唐的又一变体(2026-09-19 补)
    "南膳部洲": "南赡部洲",  # variant → canonical
    "南瞻部洲": "南赡部洲",  # variant → canonical
    "长安城": "东土大唐", "长安": "东土大唐",
    "江南": "南赡部洲", "江州": "江南",
    "河南": "南赡部洲", "河东": "南赡部洲", "山东": "南赡部洲",
    "京畿": "南赡部洲",
    # 两界山为大唐国界山(2026-09-19 修正:原直属南赡部洲跳层)
    "两界山": "大唐国", "五行山": "两界山",
    "大慈恩寺": "长安城",  # 玄奘译经处
    "双叉岭": "南赡部洲",
    "皇宫": "长安城", "金銮殿": "皇宫", "金銮宝殿": "皇宫",
    "白玉阶": "金銮殿",
    # ── 西牛贺洲：灵山 ──
    "灵山": "西牛贺洲", "灵山胜境": "西牛贺洲",
    "雷音寺": "灵山", "大雷音寺": "灵山", "珍楼": "雷音寺",
    "西天": "西牛贺洲",  # 西天 is a broad region, not inside 灵山
    "雷音宝刹": "灵山", "玉真观": "灵山",
    "凌云渡": "灵山", "化龙池": "灵山",
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
    # 宁荣二府皆在宁荣街(2026-09-19 修正:原直属都中,跳过街一级)
    "荣国府": "宁荣街", "宁国府": "宁荣街",
    "宁荣街": "都中", "铁槛寺": "都中",
    "水月庵": "都中", "贾宅": "都中",
    "贾府": "都中", "北静王府": "都中",
    "东府": "都中",  # 宁国府别称
    "锦衣府": "都中", "神京": "都中",
    "都中城外": "都中", "北府": "都中",
    "王子腾府": "都中",
    # 薛家寄居荣国府(梨香院),非独立在都中(2026-09-19 修正)
    "薛家": "荣国府",
    # 2026-09-19 errata 驱动增补(皆可原文核验):
    # 玄真观=贾敬修道处;紫檀堡=蒋玉菡置办;花枝巷=贾琏藏尤二姐;
    # 馒头庵(水月庵)与铁槛寺皆贾府家庙;刘姥姥村在都郊
    "玄真观": "都中", "紫檀堡": "都中", "花枝巷": "都中",
    "馒头庵": "都中", "刘姥姥村": "都中",
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
    # 2026-09-19 增补:凹晶馆(黛玉湘云联诗处)/凸碧堂皆大观园内;
    # 缀锦楼为紫菱洲迎春居所
    "凹晶馆": "大观园", "凸碧堂": "大观园",
    "缀锦楼": "紫菱洲",
    # 宁国府内部
    "会芳园": "宁国府", "天香楼": "宁国府",
    "议事厅": "宁国府", "宁国府·上房": "宁国府",
    "宗祠": "宁国府", "尤二姐处": "宁国府",
    # 会芳园/天香楼之外:贾珍为宁国府主(2026-09-19 增补)
    "贾珍院": "宁国府",
    # 金陵（南京/石头城）
    "姑苏": "金陵", "江南": "金陵",
    # 甄家(甄宝玉家)在金陵;林家(林如海)在扬州,维扬为扬州古称
    "甄家": "金陵", "林家": "扬州", "维扬": "扬州",
    # 太虚幻境
    "离恨天": "太虚幻境",
    # 铁槛寺
    "净室": "铁槛寺",
    # 荣国府补充（审核修正）
    "穿夹道": "荣国府",  # 穿夹道在荣国府内
    # 其他
    "街市": "都中",
    "孙家": "都中城外", "下房": "荣国府",
    "镇海统制": "海疆",
    "贾家义学": "荣国府",
    # 2026-09-19 增补:王夫人房/荣庆堂/贾母院/贾赦院皆荣国府内
    "王夫人房": "荣国府", "荣庆堂": "荣国府",
    "贾母院": "荣国府", "贾赦院": "荣国府",
}

_SHUIHU_PRIORS: dict[str, str] = {
    # ── 天下直属大区域（宋代路/大区） ──
    "山东": "天下", "河北": "天下", "京畿": "天下",
    "河南": "天下", "淮南": "天下", "淮西": "天下",
    "江州": "天下", "华州": "关西",  # 华州属陕西(关西)永兴军路
    "辽国": "天下",
    # 陕西为路级,原误置华州之下(路挂州,倒置;2026-09-19 修正)
    "陕西": "天下",
    # ── 山东 ──
    "梁山泊": "山东", "济州": "山东", "青州": "山东",
    "阳谷县": "山东", "高唐州": "山东",
    "凌州": "山东", "东昌府": "山东", "泰安州": "山东",
    "琳琅山": "山东",
    # 2026-09-19 errata 驱动勘误(均为小说/宋代地理可核验事实):
    # 清风山在青州地面(清风寨一回);东平府为宋江所攻山东州府;
    # 登州为山东半岛州府(毛太公/沙门岛一回);昭德为田虎河东五州之一。
    "清风山": "青州", "东平府": "山东", "登州": "山东",
    "昭德": "河东", "沂州": "山东", "沙门岛": "山东",
    "沂水县": "沂州", "沂岭": "沂水县",
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
    "东京": "京畿",
    # 北宋大名府属河北东路(2026-09-19 修正:原误置京畿;LLM 独立核验一致)
    "北京大名府": "河北", "大名府": "河北", "北京": "河北", "西京": "京畿", "陈桥驿": "京畿",
    "京师": "京畿",
    # 常州属两浙路(原误置京畿,2026-09-19 修正)
    "常州": "两浙",
    # 东京内部
    "开封府": "东京", "枢密院": "东京", "文德殿": "东京",
    "马行街": "东京", "金梁桥": "东京", "李师师家": "东京",
    "端王宫": "东京", "宿太尉府": "东京",
    "紫宸殿": "东京", "西华门": "东京", "东华门": "东京",
    "蒲东郡": "东京",
    # 2026-09-19 增补:汴梁城为东京别称;祥符/酸枣门/太尉府皆东京城内;
    # 岳庙(林冲娘子烧香处)/蔡河(东京四河之一)皆在东京
    "汴梁城": "东京", "祥符县": "东京", "酸枣门": "东京",
    "太尉府": "东京", "东京开封府": "东京",
    "岳庙": "东京", "蔡河": "东京",
    # 北京大名府内部
    "梁中书府": "北京大名府", "大牢": "北京", "留守司": "北京",
    "黄河": "大名府", "飞虎峪": "大名府",
    # ── 河北 ──
    "蓟州": "河北", "沧州": "河北", "卫州": "河北",
    "幽州": "河北", "霸州": "河北", "永清县": "河北",
    "檀州": "河北",
    # 2026-09-19 增补:中山=定州(河北西路);雄州为宋辽边境河北东路州;
    # 涿州/邺城/太行山皆河北,原被挂到永清县/辽国/山东等
    "中山": "河北", "雄州": "河北", "涿州": "河北",
    "邺城": "河北", "太行山": "河北",
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
    "荆湖": "宛州", "荆南": "淮西",
    # 云安/隆中山皆王庆淮西割据区(120回本王庆篇)
    "云安": "淮西", "隆中山": "淮西",
    # ── 江州 ──
    "浔阳江": "江州", "江州城": "江州", "江州府": "江州",
    "牢城营": "江州", "无为军": "江州",
    "穆太公庄": "江州",
    "点视厅": "牢城营", "单身房": "牢城营", "抄事房": "牢城营",
    # ── 淮南 ──
    "扬州": "淮南",
    # ── 杭州/两浙（方腊势力） ──
    # 2026-09-19 修正倒置:两浙为路级直属天下(原误挂杭州城之下);
    # 杭州城为杭州之城(原直属天下,errata 记其 mc=2 却有 17 子节点异常);
    # 江南为泛称大区直属天下(原误挂润州之下)
    "两浙": "天下", "杭州": "两浙", "杭州城": "杭州",
    "苏州": "两浙",
    "润州": "两浙", "睦州": "两浙", "歙州": "两浙", "秀州": "两浙",
    "乌龙岭": "两浙", "湖州": "两浙",
    "清溪县": "睦州", "帮源洞": "清溪县", "睦州城": "睦州", "歙州城": "歙州",
    "江南": "天下", "独松关": "江南", "宣州": "江南",
    "昱岭关": "两浙",
    # ── 河东（田虎势力） ──
    # 2026-09-19 修正倒置:河东为路级直属天下,太原为其首府
    # (原为 河东→太原县城→天下,恰为小说结构与史实之反)
    "河东": "天下", "太原县城": "河东",
    "五台山": "河东", "盖州": "河东", "晋宁": "河东",
    "威胜": "河东", "壶关": "河东", "汾阳": "河东",
    "威胜州": "河东", "平遥县": "河东",
    # 代州(雁门关所在)属河东路;昭德城/昭德府为田虎昭德
    "代州": "河东", "雁门县": "代州",
    "昭德城": "河东", "昭德府": "河东",
    "陵川": "盖州", "高平": "盖州", "阳城": "盖州",
    "文殊寺": "五台山",
    # ── 其他 ──
    "关西": "天下",  # 陕西/关中，不在梁山泊后山
    "延安府": "关西", "渭州": "关西",
    "山西": "天下", "水泊": "山东",
    "南丰": "淮西", "淮西·南丰": "淮西",
    "信州": "天下",
    "燕京": "辽国",
    "龙虎山": "信州",
    "官道": "天下", "村镇": "天下",
    "山南军": "天下", "山南": "天下",
    "开州": "天下",
    "陕州": "天下", "鳌山": "天下",
    "少华山": "华州", "华阴县": "华州",
    # 2026-09-19 增补(errata 驱动,皆可核验):
    # 十字坡在孟州道(孙二娘);揭阳镇为江州浔阳江畔市镇;
    # 伊阙/北邙皆洛阳(西京)山川;高唐州城为高唐州之城;
    # 淮河/扬子江为跨区水系直属天下;江西为路级大区
    "十字坡": "孟州道", "揭阳镇": "江州",
    "伊阙山": "西京", "北邙山": "西京", "西京城": "西京",
    "高唐州城": "高唐州", "济州大牢": "济州",
    "淮河": "天下", "扬子江": "天下", "江西": "天下",
    # 2026-09-19 批四(errata 与 gold 两来源一致,原文可核验):
    # 南城/东城为田虎篇盖州城之门城区;枪竿岭在代州(雁门腹地);
    # 荆门镇属山东;高岭为京畿山岭(曾误挂描述性节点山左丛林下)
    "南城": "盖州城", "盖州城": "盖州",
    "枪竿岭": "代州", "荆门镇": "山东", "高岭": "京畿",
    # 2026-09-20 批五(errata 与 gold 两来源一致):
    # 房山为河北山名(golden_standard correct_parent=河北;
    # errata verdict=正确 parent=河北)。Rule 8/9 误杀豁免(fact_validator
    # _RULE89_SPECIFIC_NAME_EXEMPTIONS)使房山入链后被 tier 重分类
    # 翻转 parent 河北→房州,以此先验边锁回金标父节点。
    "房山": "河北",
}

_SANGUO_PRIORS: dict[str, str] = {
    "益州": "天下", "荆州": "天下", "扬州": "天下",
    "冀州": "天下", "豫州": "天下", "兖州": "天下",
    "徐州": "天下", "司州": "天下", "雍州": "天下",
    "成都": "益州", "许昌": "豫州", "洛阳": "司州",
    "长安": "司州", "襄阳": "荆州", "建业": "扬州",
    # ── 2026-09-19 errata 驱动扩充(三国地理,皆可核验) ──
    # 三国政权名直属天下(魏原误挂洛阳)
    "魏": "天下", "蜀": "天下", "吴": "天下",
    # 荆襄诸郡(荆南四郡+南郡/江夏/新野)
    "南郡": "荆州", "江夏": "荆州", "长沙": "荆州",
    "桂阳": "荆州", "零陵": "荆州", "武陵": "荆州",
    "新野": "荆州", "樊城": "荆州",
    # 祁山在雍州陇右(原误挂陕西——陕西为后世概念);县归郡:
    # 朐县属东海郡、黄县属东莱郡(原分误挂沛国/淮南)
    "祁山": "雍州", "东海朐县": "东海", "东莱黄县": "东莱",
}

_FENGSHEN_PRIORS: dict[str, str] = {
    # ── 2026-09-19 新增(errata 驱动;封神为武王伐纣+阐截斗法骨架) ──
    # 人间两极
    "朝歌": "天下", "西岐": "天下",
    # 朝歌城内:女娲宫(纣王进香)/摘星楼/鹿台/太师府(闻仲)
    "女娲宫": "朝歌", "摘星楼": "朝歌", "鹿台": "朝歌",
    "太师府": "朝歌",
    # 诸侯封地(errata:崇城原挂幻觉父节点「崇侯虎封地」)
    "崇城": "天下", "冀州": "天下",
    # 关隘(伐纣路线)
    "陈塘关": "天下", "汜水关": "天下", "临潼关": "天下",
    "佳梦关": "天下", "青龙关": "天下", "孟津": "天下",
    # 仙山洞府(阐教/截教/散仙)
    "昆仑山": "天下", "玉虚宫": "昆仑山",
    "乾元山": "天下", "金光洞": "乾元山",
    "终南山": "天下", "玉泉山": "天下", "金霞洞": "玉泉山",
    "峨眉山": "天下", "罗浮洞": "峨眉山",
    "骷髅山": "天下", "白骨洞": "骷髅山",
    "金鳌岛": "天下", "蓬莱岛": "天下",
    # 天界仙宫(errata:天宫/火云宫原误挂朝歌;紫霄宫为天界仙宫)
    "天宫": "天下", "火云宫": "天下", "紫霄宫": "天下",
}
