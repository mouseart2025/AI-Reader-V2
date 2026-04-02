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
        return "KnowledgePrior"

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
    "都中": "天下", "金陵": "天下",
    "荣国府": "都中", "宁国府": "都中",
    "大观园": "荣国府",
    "怡红院": "大观园", "潇湘馆": "大观园",
    "蘅芜苑": "大观园", "稻香村": "大观园",
}

_SHUIHU_PRIORS: dict[str, str] = {
    "山东": "天下", "河北": "天下", "京畿": "天下",
    "东京": "京畿", "梁山泊": "山东", "济州": "山东",
    "沧州": "河北", "江州": "天下",
}

_SANGUO_PRIORS: dict[str, str] = {
    "益州": "天下", "荆州": "天下", "扬州": "天下",
    "冀州": "天下", "豫州": "天下", "兖州": "天下",
    "徐州": "天下", "司州": "天下", "雍州": "天下",
    "成都": "益州", "许昌": "豫州", "洛阳": "司州",
    "长安": "司州", "襄阳": "荆州", "建业": "扬州",
}
