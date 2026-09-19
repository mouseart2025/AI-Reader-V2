"""WorldStructure Pydantic models for multi-layer world map."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class LayerType(str, Enum):
    overworld = "overworld"
    underground = "underground"
    sky = "sky"
    sea = "sea"
    pocket = "pocket"
    spirit = "spirit"
    underwater = "underwater"    # 海底/龙宫/水下（蓝色调）


class LocationTier(str, Enum):
    world = "world"           # 整个世界 — 仅容器，不显示为点
    realm = "realm"           # 架空特殊空间（仙界/魔域/秘境/洞天…）— 非地理尺度，弱提示呈现
    continent = "continent"   # 大洲/大陆/界/域 — zoom 6+
    kingdom = "kingdom"       # 国/大区域 — zoom 7+
    region = "region"         # 郡/山脉/海域 — zoom 8+
    city = "city"             # 城/镇/村/寺庙/门派 — zoom 9+
    site = "site"             # 具体地点（客栈、桥、洞口）— zoom 10+
    building = "building"     # 建筑内部/房间 — zoom 11+


class LocationIcon(str, Enum):
    capital = "capital"
    city = "city"
    town = "town"
    village = "village"
    camp = "camp"
    mountain = "mountain"
    forest = "forest"
    water = "water"
    desert = "desert"
    island = "island"
    temple = "temple"
    palace = "palace"
    cave = "cave"
    tower = "tower"
    gate = "gate"
    portal = "portal"
    ruins = "ruins"
    sacred = "sacred"
    generic = "generic"


class SpatialScale(str, Enum):
    room = "room"                # 密室、单个建筑
    building = "building"        # 大观园、学校
    district = "district"        # 城区、街道
    city = "city"                # 城市
    national = "national"        # 国家级（红楼梦）
    continental = "continental"  # 大陆级（西游记）
    planetary = "planetary"      # 星球
    cosmic = "cosmic"            # 多界（仙侠/玄幻）
    interstellar = "interstellar"  # 星际


class WorldRegion(BaseModel):
    name: str
    cardinal_direction: str | None = None
    region_type: str | None = None
    parent_region: str | None = None
    description: str = ""


class MapLayer(BaseModel):
    layer_id: str
    name: str
    layer_type: LayerType
    description: str = ""
    regions: list[WorldRegion] = []


class Portal(BaseModel):
    name: str
    source_layer: str
    source_location: str
    target_layer: str
    target_location: str
    is_bidirectional: bool = True
    first_chapter: int | None = None


class WorldBuildingSignal(BaseModel):
    signal_type: str
    chapter: int
    raw_text_excerpt: str = ""
    extracted_facts: list[str] = []
    confidence: str = "medium"


class WorldStructure(BaseModel):
    novel_id: str
    layers: list[MapLayer] = []
    portals: list[Portal] = []
    location_region_map: dict[str, str] = {}
    location_layer_map: dict[str, str] = {}
    novel_genre_hint: str | None = None  # fantasy/wuxia/historical/urban/unknown
    location_tiers: dict[str, str] = {}    # name → tier value
    location_icons: dict[str, str] = {}    # name → icon value
    spatial_scale: str | None = None        # SpatialScale value or None
    location_parents: dict[str, str] = {}  # authoritative parent: location_name → parent_name
    type_hierarchy: dict[str, str] = {}   # learned type hierarchy: child_type → parent_type
    geo_type: str | None = None           # "realistic" / "mixed" / "fantasy" — detected by GeoResolver
    completed_spatial_relations: list[dict] = []  # 补全的跨章节空间关系 (top 500)
    # Each dict: {source, target, relation_type, value, confidence, evidence_chapters}
    layer_spatial_scales: dict[str, str] = {}  # layer_id → SpatialScale value
    cached_skeleton: dict | None = None  # v0.63.0: cached successful skeleton result
    # 虚拟节点(2026-09-19,王贺 10.2 驱动的「语义根/工程根分离」):
    # 图层分组根(主世界/天界/冥界…)与无文本依据的 uber_root 是工程脚手架,
    # 不是小说的知识声明——不渲染为地点、不进标注导出、不进 gold。
    # 「天下」仅在文本确有此概念的小说(水浒/三国/封神)中才是真实节点。
    virtual_locations: set[str] = set()

    @classmethod
    def create_default(cls, novel_id: str) -> WorldStructure:
        """Return a default structure with a single overworld layer."""
        return cls(
            novel_id=novel_id,
            layers=[
                MapLayer(
                    layer_id="overworld",
                    name="主世界",
                    layer_type=LayerType.overworld,
                    description="小说主世界地表层",
                )
            ],
        )

    def is_virtual(self, name: str) -> bool:
        """该名称是否为虚拟节点(工程根/图层分组根),非小说知识声明。"""
        return name in self.virtual_locations

    def semantic_parent(self, name: str) -> str | None:
        """标注/导出用的语义父节点:沿父链跳过虚拟节点。

        红楼 贾政船上→主世界(虚拟)→天下(虚拟) → None(顶层浮动);
        水浒 山东→天下(真实) → 天下。用于标注导出与 gold 构建,使
        虚拟脚手架不再泄漏为知识声明(王贺 10.2,2026-09-19)。
        """
        node = self.location_parents.get(name)
        seen = {name}
        while node and node in self.virtual_locations and node not in seen:
            seen.add(node)
            node = self.location_parents.get(node)
        return node
