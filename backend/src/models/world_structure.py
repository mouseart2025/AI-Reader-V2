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


class LocationTier(str, Enum):
    world = "world"           # 整个世界 — 仅容器，不显示为点
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
    cosmic = "cosmic"            # 多界（仙侠/玄幻）
    continental = "continental"  # 大陆级（西游记）
    national = "national"        # 国家级（红楼梦）
    urban = "urban"              # 城市级
    local = "local"              # 局部


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
