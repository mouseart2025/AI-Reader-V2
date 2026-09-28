"""从地点**描述**抽取地貌性格（terrain）与地标类型（landmark）。

## 为什么是"带证据的字段抽取"而不是"自由二创"

项目的质量线是 evidence + 无幻觉。自由生成地貌会造出原文没有的地理
（项目本来就有幻觉审查，正是为这个），所以这里只做一件事：
把描述里**已经写着**的视觉信息，收敛到封闭枚举上，并要求逐字证据。

## 校验层是重点

上一轮 PoC（本地 qwen2.5:7b，西游 20 条）实测模型的三种越界行为：

1. 越枚举 —— `landmark` 给出 "皇宫大殿"（清单里没有）；
2. 字符串 `"null"` —— 不是 `None`，会当成合法值传下去；
3. evidence 只是地点名本身，或干脆是模型自己造的一句话。

因此：**枚举不匹配 → 置 None**，**evidence 不是描述的子串 → 置空**，
并且把模型原话留在 `LocationVisual.raw`，让校验损失**可量**而不是被抹平。

`_norm_enum` / `evidence_ok` 是纯函数，单独可测。
"""

from __future__ import annotations

import json
import logging
import random
import re

from src.infra.llm_client import _extract_json
from src.models.location_visual import (
    LANDMARK_VALUES,
    TERRAIN_VALUES,
    Landmark,
    LocationVisual,
    Terrain,
)

logger = logging.getLogger(__name__)

# 空值的各种写法。`"null"` 是实测踩到的：JSON 里写成字符串的 null。
_NULLISH = {
    "", "null", "none", "n/a", "na", "nan", "nil", "unknown", "undefined",
    "无", "没有", "不确定", "未知", "不适用", "-", "—",
}

# 模型偶尔用中文/近义英文回答。只做**显式**映射，不做模糊匹配 ——
# 猜出来的枚举值比空值更危险（它会被当成真判据喂进地形场）。
TERRAIN_ALIASES: dict[str, str] = {
    "山": "mountain", "山地": "mountain", "山脉": "mountain", "山峦": "mountain",
    "水": "water", "水体": "water", "水域": "water", "河流": "water", "湖泊": "water",
    "海洋": "water", "湖": "water", "河": "water", "海": "water",
    "森林": "forest", "林": "forest", "林地": "forest", "树林": "forest", "园林": "forest",
    "海滨": "coastal", "海岸": "coastal", "沙滩": "coastal", "海岸线": "coastal",
    "平原": "plain", "旷野": "plain", "田野": "plain", "草地": "plain",
    "城市": "urban", "城镇": "urban", "城郭": "urban", "聚落": "urban", "街市": "urban",
    "天界": "celestial", "仙境": "celestial", "仙界": "celestial", "佛国": "celestial",
    "冥界": "underworld", "地府": "underworld", "阴间": "underworld",
    "地下": "underground", "洞穴": "underground", "地宫": "underground", "洞府": "underground",
    "mountainous": "mountain", "waterside": "water", "woodland": "forest",
    "seaside": "coastal", "plains": "plain", "city": "urban", "urban_area": "urban",
}

LANDMARK_ALIASES: dict[str, str] = {
    "宫殿": "palace", "王府": "palace", "官署": "palace", "皇宫": "palace",
    "宅院": "residence", "民居": "residence", "府邸": "residence", "住宅": "residence",
    "寺庙": "temple", "寺": "temple", "道观": "temple", "庙": "temple", "庵": "temple",
    "塔": "tower", "楼阁": "tower", "楼": "tower",
    "桥": "bridge", "桥梁": "bridge",
    "关隘": "gate", "城门": "gate", "牌坊": "gate", "关口": "gate",
    "园林": "garden", "花园": "garden", "苑": "garden",
    "集市": "market", "街市": "market", "店铺": "market", "市场": "market",
    "码头": "harbor", "渡口": "harbor", "港": "harbor",
    "洞府": "cave", "洞穴": "cave", "洞": "cave",
    "泉": "spring", "井": "spring", "泉眼": "spring",
    "城墙": "wall", "寨墙": "wall", "城垣": "wall",
    "书院": "academy", "学馆": "academy", "学堂": "academy",
    "营寨": "camp", "军寨": "camp", "寨": "camp",
    "祭台": "altar", "坛": "altar", "祭坛": "altar",
}

_MIN_EVIDENCE_CHARS = 4

VISUAL_SYSTEM = f"""你为中文小说里的地点标注【视觉属性】，**只依据给出的描述**。

对每个地点给出三项：
1. terrain —— 这块地是什么地貌，**只能取以下之一，或 null**：
   {", ".join(TERRAIN_VALUES)}
2. landmark —— 这里最显眼的人造物/地物（地图图标用），**只能取以下之一，或 null**：
   {", ".join(LANDMARK_VALUES)}
3. evidence —— 从描述里**逐字复制**的一段（至少 {_MIN_EVIDENCE_CHARS} 个字），
   它要能支撑你上面的判断

铁律：
- **描述没说的不要猜**；判断不了就给 null。宁可三个都空，也不要编。
- 两个值都只能从上面的清单里选，**不许自造词**（例如写 "皇宫大殿" 是错的，
  清单里有 palace 就该用 palace）。
- evidence **必须逐字出现在描述里**（可以整句复制），不许改写、不许用地点名充数。
- 只输出 JSON 对象，键为地点名：
{{"地点A": {{"terrain": "mountain", "landmark": null, "evidence": "原文片段"}}, "地点B": {{"terrain": null, "landmark": null, "evidence": ""}}}}"""


def _norm_enum(value: object, aliases: dict[str, str], allowed: tuple[str, ...]) -> str | None:
    """把模型给的值收敛到枚举，收敛不了就 None。"""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _NULLISH:
        return None
    s = aliases.get(s, s)
    return s if s in allowed else None


def _squash(text: str) -> str:
    """去空白后比较：中文描述里换行/空格不该影响"逐字"判定。"""
    return re.sub(r"\s+", "", text or "")


def evidence_ok(evidence: object, description: str, name: str = "") -> bool:
    """证据是否真的来自描述。

    三条都过才算：非空、不像地点名的复读、且去空白后是描述的子串。
    最短长度是防"山""水"这类单字靠子串关系蒙混过关。
    """
    if not isinstance(evidence, str):
        return False
    ev = _squash(evidence)
    desc = _squash(description)
    if len(ev) < _MIN_EVIDENCE_CHARS or not desc:
        return False
    if name and ev == _squash(name):
        return False
    return ev in desc


def _load_entries(novel_id: str) -> list[dict]:
    """有描述的地点，同名取最长的那条描述。"""
    import sqlite3

    from src.infra.config import DB_PATH

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT fact_json FROM chapter_facts WHERE novel_id=?", (novel_id,)
        ).fetchall()
    finally:
        con.close()

    best: dict[str, dict] = {}
    for (fj,) in rows:
        try:
            d = json.loads(fj)
        except (TypeError, ValueError):
            continue
        for loc in d.get("locations") or []:
            name = (loc.get("name") or "").strip()
            desc = (loc.get("description") or "").strip()
            if not name or not desc:
                continue
            prev = best.get(name)
            if prev is None or len(desc) > len(prev["description"]):
                best[name] = {
                    "name": name,
                    "type": (loc.get("type") or "").strip(),
                    "description": desc,
                }
    return sorted(best.values(), key=lambda e: e["name"])


def _build_prompt(batch: list[dict]) -> str:
    lines = [
        f"- {e['name']}（{e['type'] or '未标注'}）：{e['description']}" for e in batch
    ]
    return (
        "【待标注地点】\n" + "\n".join(lines) + "\n\n"
        "请为上面每一个地点输出视觉属性（JSON 对象，键为地点名）。"
    )


def _validate(raw: dict, batch: list[dict]) -> list[LocationVisual]:
    """模型原始输出 → 受校验的 LocationVisual 列表（纯函数）。"""
    out: list[LocationVisual] = []
    wanted = {e["name"]: e for e in batch}
    for name, entry in wanted.items():
        item = raw.get(name)
        if not isinstance(item, dict):
            out.append(LocationVisual(name=name, raw=json.dumps(item, ensure_ascii=False)
                                      if item is not None else ""))
            continue
        terrain = _norm_enum(item.get("terrain"), TERRAIN_ALIASES, TERRAIN_VALUES)
        landmark = _norm_enum(item.get("landmark"), LANDMARK_ALIASES, LANDMARK_VALUES)
        ev = item.get("evidence")
        if not evidence_ok(ev, entry["description"], name):
            ev = ""
        out.append(
            LocationVisual(
                name=name,
                terrain=Terrain(terrain) if terrain else None,
                landmark=Landmark(landmark) if landmark else None,
                evidence=ev if isinstance(ev, str) else "",
                raw=json.dumps(item, ensure_ascii=False),
            )
        )
    return out


async def extract_location_visuals(
    novel_id: str,
    *,
    client=None,
    batch_size: int = 10,
    limit: int | None = None,
    seed: int = 0,
    model_label: str = "",
    on_batch=None,
) -> list[LocationVisual]:
    """Batch over the locations that have a description.

    `limit` samples with a fixed seed so a partial run is reproducible — the
    audit numbers have to be comparable between runs, which rules out "first N
    rows" (that would bias toward whichever names sort first).
    """
    if client is None:
        from src.infra.llm_client import get_llm_client

        client = get_llm_client()

    entries = _load_entries(novel_id)
    if limit is not None and limit < len(entries):
        entries = sorted(random.Random(seed).sample(entries, limit), key=lambda e: e["name"])
    if not entries:
        return []

    batches = (len(entries) + batch_size - 1) // batch_size
    results: list[LocationVisual] = []
    for i in range(0, len(entries), batch_size):
        batch = entries[i : i + batch_size]
        try:
            content, _usage = await client.generate(
                system=VISUAL_SYSTEM,
                prompt=_build_prompt(batch),
                format={"type": "object"},
                temperature=0.0,
                max_tokens=max(2000, 400 * len(batch)),
            )
        except Exception:
            logger.warning("visual extraction: batch %d failed", i // batch_size, exc_info=True)
            continue
        raw = content if isinstance(content, dict) else _extract_json(str(content))
        results.extend(_validate(raw if isinstance(raw, dict) else {}, batch))
        n = i // batch_size + 1
        logger.info("visual extraction: %d/%d batches, %d locations", n, batches, len(results))
        if on_batch is not None:
            on_batch(n, batches, results)
    return results


def summarize(visuals: list[LocationVisual]) -> dict:
    """把校验损失与取值分布算出来 —— 这是判断"这一轴能不能用"的全部依据。

    ⚠️ `value_without_evidence` 是这里最关键的一个数，因为它暴露了校验层的**边界**：
    `evidence_ok` 只能证明"这段字确实摘自描述"，**不能证明这段字支持这个标签**。
    实测例子：`玄英洞 → mountain`，证据是"三只犀牛精辟寒、辟暑、辟尘的洞府" ——
    逐字属实，但和山没有关系。所以这个数不是零就说明还有一层 entailment 没校。
    """
    n = len(visuals)
    terrain_hit = sum(1 for v in visuals if v.terrain is not None)
    landmark_hit = sum(1 for v in visuals if v.landmark is not None)
    ev_ok = sum(1 for v in visuals if v.evidence)
    values_total = sum(
        1 for v in visuals if v.terrain is not None or v.landmark is not None
    )
    # 有结论却没有合法证据 —— 这类是"没被校到"的，必须单独计数
    value_without_evidence = sum(
        1
        for v in visuals
        if (v.terrain is not None or v.landmark is not None) and not v.evidence
    )
    # 模型给了值却被校验拦下的数量（越枚举 / "null" / 别名不认）
    raw_terrain_rejected = raw_landmark_rejected = 0
    for v in visuals:
        try:
            item = json.loads(v.raw) if v.raw else None
        except (TypeError, ValueError):
            item = None
        if not isinstance(item, dict):
            continue
        if item.get("terrain") is not None and v.terrain is None and \
                _norm_enum(item.get("terrain"), TERRAIN_ALIASES, TERRAIN_VALUES) is None:
            raw_terrain_rejected += 1
        if item.get("landmark") is not None and v.landmark is None and \
                _norm_enum(item.get("landmark"), LANDMARK_ALIASES, LANDMARK_VALUES) is None:
            raw_landmark_rejected += 1

    def dist(getter) -> dict[str, int]:
        c: dict[str, int] = {}
        for v in visuals:
            k = getter(v)
            if k is not None:
                c[k] = c.get(k, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    return {
        "locations": n,
        "terrain_hit": terrain_hit,
        "landmark_hit": landmark_hit,
        "evidence_ok": ev_ok,
        "values_total": values_total,
        "value_without_evidence": value_without_evidence,
        "both_empty": sum(1 for v in visuals if v.terrain is None and v.landmark is None),
        "terrain_rejected": raw_terrain_rejected,
        "landmark_rejected": raw_landmark_rejected,
        "terrain_dist": dist(lambda v: v.terrain.value if v.terrain else None),
        "landmark_dist": dist(lambda v: v.landmark.value if v.landmark else None),
    }
