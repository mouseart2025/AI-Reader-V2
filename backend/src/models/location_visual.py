"""地貌性格（terrain）与地标类型（landmark）：从地点**描述**派生的视觉属性。

为什么单独放这里而不是塞进 `ChapterFact`：`chapter_facts` **不可变**，
任何派生数据都不能回写。这类字段是「二创」产物 —— 由描述再加工而来，
所以它进自己的表（`location_visuals`），并带模型名与时间戳，可整体重算。

两个轴刻意做成**封闭枚举**：上一轮 PoC 实测模型会给出越枚举值
（`landmark` 出现过 "皇宫大殿"）和字符串 `"null"`。
枚举是这里唯一可靠的质量门 —— 自由文本进不了地形场。

`terrain`（地貌性格）回答「这块地是什么地形」：
    mountain 山地/峰岭/崖岩 · water 水体（河湖海泉潭溪江洋）· forest 林木/园林植被
    coastal 海滨/沙滩/岸线 · plain 平原/旷野/田野 · urban 城郭/街市/聚落
    celestial 天界/仙境/佛国 · underworld 冥界/地府 · underground 洞穴/地宫/井

`landmark`（地标类型）回答「这里最显眼的人造物/地物是什么」，
它对应地图上的图标槽位 —— 上一轮结论里"游戏级细节的瓶颈是美术资产"，
这个轴就是给图标集用的键。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Terrain(str, Enum):
    MOUNTAIN = "mountain"
    WATER = "water"
    FOREST = "forest"
    COASTAL = "coastal"
    PLAIN = "plain"
    URBAN = "urban"
    CELESTIAL = "celestial"
    UNDERWORLD = "underworld"
    UNDERGROUND = "underground"


class Landmark(str, Enum):
    PALACE = "palace"          # 宫殿/王府/官署
    RESIDENCE = "residence"    # 宅院/民居/府邸
    TEMPLE = "temple"          # 寺庙/道观/庵
    TOWER = "tower"            # 塔/楼阁
    BRIDGE = "bridge"          # 桥
    GATE = "gate"              # 关隘/城门/牌坊
    GARDEN = "garden"          # 园林/苑/花园
    MARKET = "market"          # 街市/集市/店铺
    HARBOR = "harbor"          # 码头/渡口
    CAVE = "cave"              # 洞府/洞穴
    SPRING = "spring"          # 泉/井
    WALL = "wall"              # 城墙/寨墙
    ACADEMY = "academy"        # 书院/学馆
    CAMP = "camp"              # 营寨/军寨
    ALTAR = "altar"            # 坛/祭台


TERRAIN_VALUES = tuple(t.value for t in Terrain)
LANDMARK_VALUES = tuple(lm.value for lm in Landmark)


class LocationVisual(BaseModel):
    """一个地点的派生视觉属性。全空也是合法结果（描述没给信息时不猜）。"""

    name: str
    terrain: Terrain | None = None
    landmark: Landmark | None = None
    # 逐字引用描述中的一段，作为上面两项的证据；校验不过则置空（见 extractor）
    evidence: str = ""
    # 模型原话（未校验）。保留它是为了**让校验损失可量**，而不是被校验悄悄抹平。
    raw: str = ""
