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
