"""Story 5.5 实体净化 —— 剔除不该进入层级树的非地点/泛指实体。

由来(2026-09-08 三国 180 条人工抽查):
    错边 21 条里 9 条(43%)属于「实体质量问题」,而自动指标 A/B **命中 0 条**:
      · 非地点 5: 关公(人物) / 东汉(朝代) / 北俱芦洲(佛教神话)
                 / instance_东吴(内部标识符残留)
      · 通名   4: 各关隘 / 各寺院 / 官道 / 江岸

为什么现有 `_is_generic_location` 拦不住:
    它是**精确匹配的黑名单穷举**(`name in _XXX`),凡是没枚举到的形态
    (各* 泛指、instance_* 前缀、人物名) 一律放行。

本模块补的是**形态规则 + 数据驱动**,不是继续穷举:
    1. `instance_*` 前缀          —— 内部标识符,必剔
    2. `各*` 泛指                 —— 各关隘/各寺院/各郡…
    3. 通名黑名单(小表)          —— 官道/江岸/河岸/大道…
    4. 朝代/政权名                —— 东汉/西汉…
    5. 佛教四大部洲               —— 北俱芦洲等(仅当本小说 locations 无
       真实证据时剔除;西游/封神的部洲是地理骨架,按提及次数豁免)
    6. **数据驱动人物名**         —— 在 characters 中出现、却几乎不出现在
       locations 里的名字,判定为人物而非地点(如 关公)

副作用:不合格实体既不能做 child 也不能做 parent。若它已是 parent,
其子节点**上移到祖父**(有祖父)或直接删除(无祖父),避免产生孤儿。

用法:由 `build_default_orchestrator` 在 suffix 之后自动挂载。
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from src.infra.config import DB_PATH
from src.services.geo_skills.base import GeoSkill, SkillResult
from src.services.geo_skills.snapshot import HierarchySnapshot

# ── 形态规则 ────────────────────────────────────────────────────────────
INSTANCE_PREFIX = "instance_"        # 内部标识符残留


def _is_collective_generic(name: str) -> bool:
    """「各*」泛指:各关隘 / 各寺院 / 各郡 / 各处。"""
    return name.startswith("各") and len(name) <= 5


# 通名黑名单(小表,只收明确无指称功能的词)
GENERIC_PATH_WORDS: frozenset[str] = frozenset({
    "官道", "大道", "小道", "山道", "要道", "故道",
    "江岸", "河岸", "岸上", "岸边", "江边", "河边",
    "路口", "渡口", "关口", "隘口",
    "城外", "城下", "山下", "山下寨",
})

# 朝代 / 政权名(不是地点)
DYNASTY_NAMES: frozenset[str] = frozenset({
    "东汉", "西汉", "蜀汉", "东周", "西周", "东晋", "西晋",
    "大汉", "汉朝", "秦朝", "唐朝", "宋朝", "明朝", "元朝",
})

# 佛教四大部洲——仅在"本小说无真实地点证据"时剔除(三国噪声场景);
# 西游/封神中它们是真实地理骨架,execute() 按 locations 提及次数豁免
BUDDHIST_CONTINENTS: frozenset[str] = frozenset({
    "北俱芦洲", "东胜神洲", "西牛贺洲", "南赡部洲",
    "北俱泸州", "东胜神州", "西牛货洲", "南瞻部洲",
})


class EntityPurifier(GeoSkill):
    """剔除层级树中的非地点 / 泛指实体。"""

    def __init__(self, novel_id: str, min_character_mentions: int = 2):
        self.novel_id = novel_id
        self._min_char_mentions = min_character_mentions

    @property
    def name(self) -> str:
        return "实体净化"

    # ── 数据驱动:人物名判定 ────────────────────────────────────────────
    def _mention_counts(self) -> tuple[Counter[str], Counter[str]]:
        """统计本小说 chapter_facts 中名字在 characters / locations 的出现次数。"""
        char_c: Counter[str] = Counter()
        loc_c: Counter[str] = Counter()
        con = sqlite3.connect(str(DB_PATH))
        try:
            rows = con.execute(
                "SELECT fact_json FROM chapter_facts WHERE novel_id=?",
                (self.novel_id,),
            ).fetchall()
        finally:
            con.close()

        for (raw,) in rows:
            try:
                fact = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for ch in fact.get("characters") or []:
                nm = ch.get("name") if isinstance(ch, dict) else ch
                if nm:
                    char_c[nm] += 1
            for loc in fact.get("locations") or []:
                nm = loc.get("name")
                if nm:
                    loc_c[nm] += 1
        return char_c, loc_c

    def _character_names(
        self, char_c: Counter[str], loc_c: Counter[str],
    ) -> set[str]:
        """在 characters 里出现、却几乎不在 locations 里出现的名字 = 人物。

        关公: characters 2 次 / locations 0 次 → 人物,剔除。
        """
        return {
            name for name, n in char_c.items()
            if n >= self._min_char_mentions and loc_c.get(name, 0) == 0
        }

    def classify(self, name: str, people: set[str]) -> str | None:
        """返回剔除原因,或 None 表示保留。"""
        if not name:
            return "empty"
        if name.startswith(INSTANCE_PREFIX):
            return "instance_* 内部标识符"
        if _is_collective_generic(name):
            return "各* 泛指"
        if name in GENERIC_PATH_WORDS:
            return "通名"
        if name in DYNASTY_NAMES:
            return "朝代/政权名"
        if name in BUDDHIST_CONTINENTS:
            return "佛教部洲(神话层)"
        if name in people:
            return "人物名"
        return None

    async def execute(self, snapshot: HierarchySnapshot) -> SkillResult:
        parents = snapshot.location_parents
        if not parents:
            return SkillResult.empty(self.name, "No parents to purify")

        char_c, loc_c = self._mention_counts()
        people = self._character_names(char_c, loc_c)
        # 佛教部洲证据豁免:本小说 locations 中确有多次提及(西游/封神的
        # 四大部洲是真实地理骨架)则保留;三国式零星噪声(loc<2)仍剔除
        continent_keep = {n for n in BUDDHIST_CONTINENTS if loc_c.get(n, 0) >= 2}

        # 1) 判定所有出现的实体(child 与 parent 都要判)
        all_entities: set[str] = set(parents.keys()) | set(parents.values())
        bad = {n: r for n in all_entities
               if n not in continent_keep
               and (r := self.classify(n, people)) is not None}

        if not bad:
            return SkillResult.empty(self.name, "No invalid entities")

        # 2) 不合格实体本身不做 child;同时从 tiers 移除,否则会被
        #    _inject_layer_roots 的 Phase 0 当作孤儿重新挂回 uber_root。
        overrides: dict[str, str | None] = {n: None for n in bad}
        tier_updates: dict[str, str | None] = {n: None for n in bad}

        # 3) 不合格实体不做 parent:子节点上移到祖父,无祖父则删除
        lifted = dropped = 0
        for child, parent in parents.items():
            if parent not in bad or child in bad:
                continue
            grand = parents.get(parent)
            if grand and grand not in bad and grand != child:
                overrides[child] = grand
                lifted += 1
            else:
                overrides[child] = None
                dropped += 1

        import logging
        logging.getLogger(__name__).info(
            "EntityPurifier: 剔除 %d 个非地点/泛指实体,子节点上移 %d / 删除 %d",
            len(bad), lifted, dropped,
        )

        return SkillResult(
            skill_name=self.name,
            parent_overrides=overrides,
            tier_updates=tier_updates,
        )
