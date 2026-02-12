"""Aggregated entity profile models — produced by EntityAggregator from ChapterFacts."""

from __future__ import annotations

from pydantic import BaseModel


# ── Person ────────────────────────────────────────


class AliasEntry(BaseModel):
    name: str
    first_chapter: int


class AppearanceEntry(BaseModel):
    chapters: list[int]
    description: str


class AbilityEntry(BaseModel):
    chapter: int
    dimension: str
    name: str
    description: str


class RelationStage(BaseModel):
    chapter: int
    relation_type: str
    evidence: str = ""


class RelationChain(BaseModel):
    other_person: str
    stages: list[RelationStage] = []


class ItemAssociation(BaseModel):
    chapter: int
    item_name: str
    item_type: str
    action: str
    description: str = ""


class PersonExperience(BaseModel):
    chapter: int
    summary: str
    type: str
    location: str | None = None


class PersonProfile(BaseModel):
    name: str
    type: str = "person"
    aliases: list[AliasEntry] = []
    appearances: list[AppearanceEntry] = []
    abilities: list[AbilityEntry] = []
    relations: list[RelationChain] = []
    items: list[ItemAssociation] = []
    experiences: list[PersonExperience] = []
    stats: dict = {}


# ── Location ──────────────────────────────────────


class LocationDescription(BaseModel):
    chapter: int
    description: str


class LocationVisitor(BaseModel):
    name: str
    chapters: list[int] = []
    is_resident: bool = False  # appeared in N+ chapters


class LocationEvent(BaseModel):
    chapter: int
    summary: str
    type: str


class LocationProfile(BaseModel):
    name: str
    type: str = "location"
    location_type: str = ""
    parent: str | None = None
    children: list[str] = []
    descriptions: list[LocationDescription] = []
    visitors: list[LocationVisitor] = []
    events: list[LocationEvent] = []
    stats: dict = {}


# ── Item ──────────────────────────────────────────


class ItemFlowEntry(BaseModel):
    chapter: int
    action: str
    actor: str
    recipient: str | None = None
    description: str = ""


class ItemProfile(BaseModel):
    name: str
    type: str = "item"
    item_type: str = ""
    flow: list[ItemFlowEntry] = []
    related_items: list[str] = []
    stats: dict = {}


# ── Organization ──────────────────────────────────


class OrgMemberEvent(BaseModel):
    chapter: int
    member: str
    role: str | None = None
    action: str
    description: str = ""


class OrgRelationEntry(BaseModel):
    chapter: int
    other_org: str
    relation_type: str


class OrgProfile(BaseModel):
    name: str
    type: str = "org"
    org_type: str = ""
    member_events: list[OrgMemberEvent] = []
    org_relations: list[OrgRelationEntry] = []
    stats: dict = {}


# ── Entity summary (for listing) ─────────────────


class EntitySummary(BaseModel):
    name: str
    type: str  # person / location / item / org / concept
    chapter_count: int = 0
    first_chapter: int = 0
