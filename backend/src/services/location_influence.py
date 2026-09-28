"""地点 → 地貌影响力类别（山 / 水 / 林）。

这段判定原本是 `generate_terrain` 里的函数内局部常量。抽出来的唯一理由是
**保证只有一个实现**：任何"用描述派生的地貌"与"用名字/type/icon 派生的地貌"
做对照的脚本，如果自己重写一遍判定，就会犯「量具复刻被测逻辑」那类错
（量具与被测代码不是同一份真相，改了一边另一边照旧报告）。

判定口径（v0.67.1 起）：
  - **名字用后缀匹配（endswith）而不是子串**，否则 `水帘洞`、`城池`、`南海普陀山`
    会被判成 water —— 这是实测踩过的假阳性；
  - type / icon 仍用子串匹配（它们本身就更可靠）。

一个地点可以同时是多种（山上的林子），所以返回集合而不是单值。
"""

from __future__ import annotations

MOUNTAIN_SUFFIXES = ("山", "峰", "岭", "崖", "岩", "丘")
WATER_SUFFIXES = ("河", "湖", "海", "泉", "潭", "溪", "江", "洋")
FOREST_SUFFIXES = ("林", "苑", "圃")
# 只与 type+icon 比对（不与名字比对）
WATER_TYPE_KEYWORDS = ("河", "湖", "海", "泉", "潭", "溪", "池", "港", "江", "洋", "水")

INFLUENCE_CLASSES = ("mountain", "water", "forest")


def influence_classes(
    name: str, loc_type: str = "", icon: str = ""
) -> frozenset[str]:
    """`{"mountain","water","forest"}` 的子集：该地点对地形场贡献哪些影响力。"""
    out: set[str] = set()
    type_icon = (loc_type or "") + (icon or "")
    if (
        any(name.endswith(s) for s in MOUNTAIN_SUFFIXES)
        or icon == "mountain"
        or any(k in type_icon for k in MOUNTAIN_SUFFIXES)
    ):
        out.add("mountain")
    # NOT substring match on name — avoids 水帘洞, 城池, etc.
    if (
        any(name.endswith(s) for s in WATER_SUFFIXES)
        or icon in ("water", "island")
        or any(k in type_icon for k in WATER_TYPE_KEYWORDS)
    ):
        out.add("water")
    if (
        any(name.endswith(s) for s in FOREST_SUFFIXES)
        or icon == "forest"
        or any(k in type_icon for k in FOREST_SUFFIXES)
    ):
        out.add("forest")
    return frozenset(out)
