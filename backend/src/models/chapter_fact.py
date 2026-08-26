"""ChapterFact Pydantic models for structured novel chapter analysis."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class AbilityGained(BaseModel):
    dimension: str = ""  # "境界" / "技能" / "身份"
    name: str = ""
    description: str = ""


class CharacterFact(BaseModel):
    name: str
    new_aliases: list[str] = []
    appearance: str | None = None
    abilities_gained: list[AbilityGained] = []
    locations_in_chapter: list[str] = []
    # FR-4.1: 来源标记("main" 首遍 / "recall_pass" 查漏补漏)。
    # 可选字段、默认 "main",旧 JSON 反序列化不变。
    source: str = "main"

    @field_validator("abilities_gained", mode="before")
    @classmethod
    def _coerce_abilities(cls, v: list) -> list:
        """LLM sometimes returns plain strings instead of AbilityGained dicts."""
        if not isinstance(v, list):
            return []
        result = []
        for item in v:
            if isinstance(item, str):
                result.append({"dimension": "技能", "name": item, "description": ""})
            elif isinstance(item, dict):
                result.append(item)
        return result


class RelationshipFact(BaseModel):
    person_a: str
    person_b: str
    relation_type: str
    is_new: bool = True
    previous_type: str | None = None
    evidence: str = ""
    # Dimension schema v1 (docs/analysis/relation-dimension-schema-v1.md, FR-1.1).
    # All optional with None default: legacy JSON without these fields deserializes unchanged.
    polarity: str | None = None  # "positive" / "negative" / "neutral"
    rel_subtype: str | None = None  # e.g. 结拜 / 师门-同门 / 辈分-亲属 / 主从 / 同盟 / 敌对 / 爱慕
    closeness: str | None = None  # "close" / "distant" / "unknown"
    # FR-1.3: self-consistency vote distribution for rel_subtype
    # (rel_subtype -> vote count, includes the main extraction pass as one vote).
    # None when voting did not run (switch off or single-sample config).
    subtype_vote: dict[str, int] | None = None
    # FR-4.1: 来源标记("main" 首遍 / "recall_pass" 查漏补漏),默认 "main"。
    source: str = "main"


class LocationFact(BaseModel):
    name: str
    type: str
    parent: str | None = None
    parent_evidence: str | None = None  # v0.63.0: evidence for parent assignment (≤30 chars)
    peers: list[str] | None = None  # same-level spatially adjacent/parallel entities
    description: str | None = None
    role: str | None = None  # "setting" | "referenced" | "boundary"


class ItemEventFact(BaseModel):
    item_name: str
    item_type: str
    action: str  # 出现/获得/使用/赠予/消耗/丢失/损毁
    actor: str | None = None
    recipient: str | None = None
    description: str | None = None


class OrgRelation(BaseModel):
    other_org: str
    type: str  # 盟友/敌对/从属/竞争


class OrgEventFact(BaseModel):
    org_name: str = ""
    org_type: str = ""
    member: str | None = None
    role: str | None = None
    action: str = "其他"  # 加入/离开/晋升/阵亡/叛出/逐出 (default for LLM omission tolerance)
    description: str | None = None
    org_relation: OrgRelation | None = None


class EventFact(BaseModel):
    summary: str
    type: str  # 战斗/成长/社交/旅行/其他
    importance: str = "medium"  # high/medium/low
    participants: list[str] = []
    location: str | None = None
    # FR-3.1: 原文证据 span(与 RelationshipFact.evidence 对齐)。
    # 可选字段、默认 "",旧 JSON 反序列化不变。
    evidence: str = ""
    # FR-4.1: 来源标记("main" 首遍 / "recall_pass" 查漏补漏),默认 "main"。
    source: str = "main"


class ConceptFact(BaseModel):
    name: str
    category: str  # 修炼体系/种族/货币/功法/...
    definition: str | None = ""
    related: list[str] = []


class SpatialRelationship(BaseModel):
    source: str
    target: str
    relation_type: str  # direction/distance/contains/adjacent/separated_by/terrain/in_between/travel_path/relative_scale/cluster
    value: str = ""  # e.g. "north_of", "三天路程（步行）", "河流", "on_coast"; empty for contains/adjacent
    confidence: str = "medium"  # high/medium/low
    narrative_evidence: str = ""
    distance_class: str | None = None  # "near"/"medium"/"far"/"very_far"
    confidence_score: float | None = None  # 0.0-1.0, numeric confidence for solver
    waypoints: list[str] | None = None  # travel_path only: intermediate locations

    @field_validator(
        "source", "target", "relation_type", "value", "confidence",
        "narrative_evidence", mode="before",
    )
    @classmethod
    def _coerce_none_to_empty(cls, v: object) -> object:
        """Weak/local LLMs emit null for these string fields (esp. `value` on
        contains/adjacent relations). Coerce None -> "" so one null field does
        not fail validation of the entire chapter. Downstream consumers already
        skip entries with empty source/target/value."""
        return "" if v is None else v


class WorldDeclaration(BaseModel):
    declaration_type: str  # region_division / layer_exists / portal / region_position
    content: dict  # type-specific structured content
    narrative_evidence: str = ""
    confidence: str = "medium"  # high / medium / low


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
    world_declarations: list[WorldDeclaration] = []
