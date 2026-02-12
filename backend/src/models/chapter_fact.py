"""ChapterFact Pydantic models for structured novel chapter analysis."""

from __future__ import annotations

from pydantic import BaseModel


class AbilityGained(BaseModel):
    dimension: str  # "境界" / "技能" / "身份"
    name: str
    description: str = ""


class CharacterFact(BaseModel):
    name: str
    new_aliases: list[str] = []
    appearance: str | None = None
    abilities_gained: list[AbilityGained] = []
    locations_in_chapter: list[str] = []


class RelationshipFact(BaseModel):
    person_a: str
    person_b: str
    relation_type: str
    is_new: bool = True
    previous_type: str | None = None
    evidence: str = ""


class LocationFact(BaseModel):
    name: str
    type: str
    parent: str | None = None
    description: str | None = None


class ItemEventFact(BaseModel):
    item_name: str
    item_type: str
    action: str  # 出现/获得/使用/赠予/消耗/丢失/损毁
    actor: str
    recipient: str | None = None
    description: str | None = None


class OrgRelation(BaseModel):
    other_org: str
    type: str  # 盟友/敌对/从属/竞争


class OrgEventFact(BaseModel):
    org_name: str
    org_type: str
    member: str | None = None
    role: str | None = None
    action: str  # 加入/离开/晋升/阵亡/叛出/逐出
    description: str | None = None
    org_relation: OrgRelation | None = None


class EventFact(BaseModel):
    summary: str
    type: str  # 战斗/成长/社交/旅行/其他
    importance: str = "medium"  # high/medium/low
    participants: list[str] = []
    location: str | None = None


class ConceptFact(BaseModel):
    name: str
    category: str  # 修炼体系/种族/货币/功法/...
    definition: str = ""
    related: list[str] = []


class SpatialRelationship(BaseModel):
    source: str
    target: str
    relation_type: str  # direction/distance/contains/adjacent/separated_by/terrain
    value: str  # e.g. "north_of", "三天路程（步行）", "河流", "on_coast"
    confidence: str = "medium"  # high/medium/low
    narrative_evidence: str = ""


class ChapterFact(BaseModel):
    chapter_id: int
    novel_id: str
    characters: list[CharacterFact] = []
    relationships: list[RelationshipFact] = []
    locations: list[LocationFact] = []
    spatial_relationships: list[SpatialRelationship] = []
    item_events: list[ItemEventFact] = []
    org_events: list[OrgEventFact] = []
    events: list[EventFact] = []
    new_concepts: list[ConceptFact] = []
