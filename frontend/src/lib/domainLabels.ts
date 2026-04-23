import type { TranslationKey, TranslationParams } from "@/i18n"

type TFunc = (key: TranslationKey, params?: TranslationParams) => string

function firstValue(id: string | null | undefined, raw: string | null | undefined): string {
  return (id || raw || "").trim()
}

function normalizeId(
  id: string | null | undefined,
  raw: string | null | undefined,
  legacy: Record<string, string>,
): string {
  if (id) return id
  const value = (raw || "").trim()
  return legacy[value] ?? value
}

function labelFromKey(
  t: TFunc,
  keys: Record<string, TranslationKey>,
  id: string | null | undefined,
  raw: string | null | undefined,
): string {
  const value = firstValue(id, raw)
  const key = value ? keys[value] : undefined
  return key ? t(key) : (raw || value)
}

const TIMELINE_EVENT_LEGACY_IDS: Record<string, string> = {
  "战斗": "battle",
  "成长": "growth",
  "社交": "social",
  "旅行": "travel",
  "角色登场": "character_appearance",
  "物品交接": "item_transfer",
  "组织变动": "org_change",
  "关系变化": "relation_change",
  "其他": "other",
}

const TIMELINE_EVENT_LABEL_KEYS: Record<string, TranslationKey> = {
  battle: "timeline.eventType.battle",
  growth: "timeline.eventType.growth",
  social: "timeline.eventType.social",
  travel: "timeline.eventType.travel",
  character_appearance: "timeline.eventType.characterIntro",
  item_transfer: "timeline.eventType.itemTransfer",
  org_change: "timeline.eventType.orgChange",
  relation_change: "timeline.eventType.relationChange",
  other: "timeline.eventType.other",
}

const TIMELINE_EVENT_COLORS: Record<string, string> = {
  battle: "#ef4444",
  growth: "#3b82f6",
  social: "#10b981",
  travel: "#f97316",
  character_appearance: "#8b5cf6",
  item_transfer: "#eab308",
  org_change: "#ec4899",
  relation_change: "#06b6d4",
  other: "#6b7280",
}

export const TIMELINE_EVENT_TYPE_IDS = [
  "battle",
  "growth",
  "social",
  "travel",
  "relation_change",
  "character_appearance",
  "item_transfer",
  "org_change",
  "other",
] as const

export function timelineEventTypeId(id?: string | null, raw?: string | null): string {
  return normalizeId(id, raw, TIMELINE_EVENT_LEGACY_IDS) || "other"
}

export function timelineEventTypeLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, TIMELINE_EVENT_LABEL_KEYS, timelineEventTypeId(id, raw), raw)
}

export function timelineEventColor(id?: string | null, raw?: string | null): string {
  return TIMELINE_EVENT_COLORS[timelineEventTypeId(id, raw)] ?? "#6b7280"
}

const SCENE_EVENT_LEGACY_IDS: Record<string, string> = {
  "对话": "dialogue",
  "战斗": "battle",
  "旅行": "travel",
  "描写": "description",
  "回忆": "flashback",
  "推理": "deduction",
  "调查": "investigation",
}

const SCENE_EVENT_LABEL_KEYS: Record<string, TranslationKey> = {
  dialogue: "shared.scenePanel.eventType.dialogue",
  battle: "shared.scenePanel.eventType.battle",
  travel: "shared.scenePanel.eventType.travel",
  description: "shared.scenePanel.eventType.description",
  flashback: "shared.scenePanel.eventType.flashback",
  deduction: "shared.scenePanel.eventType.deduction",
  investigation: "shared.scenePanel.eventType.investigation",
}

const SCENE_EVENT_STYLES: Record<string, string> = {
  dialogue: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  battle: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  travel: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  description: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",
  flashback: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
  deduction: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300",
  investigation: "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300",
}

export function sceneEventTypeId(id?: string | null, raw?: string | null): string {
  return normalizeId(id, raw, SCENE_EVENT_LEGACY_IDS) || "description"
}

export function sceneEventTypeLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, SCENE_EVENT_LABEL_KEYS, sceneEventTypeId(id, raw), raw)
}

export function sceneEventTypeStyle(id?: string | null, raw?: string | null): string {
  return SCENE_EVENT_STYLES[sceneEventTypeId(id, raw)] ?? "bg-muted text-muted-foreground"
}

const SCENE_TONE_LEGACY_IDS: Record<string, string> = {
  "战斗": "battle",
  "紧张": "tense",
  "悲伤": "sad",
  "欢乐": "joyful",
  "平静": "calm",
  "推理": "deduction",
  "恐怖": "fear",
  "恐惧": "fear",
  "温馨": "warm",
  "感动": "warm",
  "愤怒": "angry",
  "神秘": "mysterious",
  "搞笑": "funny",
}

const SCENE_TONE_LABEL_KEYS: Record<string, TranslationKey> = {
  battle: "shared.scenePanel.tone.battle",
  tense: "shared.scenePanel.tone.tense",
  sad: "shared.scenePanel.tone.sad",
  joyful: "shared.scenePanel.tone.joyful",
  calm: "shared.scenePanel.tone.calm",
  deduction: "shared.scenePanel.tone.deduction",
  fear: "shared.scenePanel.tone.fear",
  warm: "shared.scenePanel.tone.warm",
  angry: "shared.scenePanel.tone.angry",
  mysterious: "shared.scenePanel.tone.mysterious",
  funny: "shared.scenePanel.tone.funny",
}

const SCENE_TONE_STYLES: Record<string, string> = {
  battle: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  tense: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
  sad: "bg-slate-100 text-slate-700 dark:bg-slate-800/50 dark:text-slate-300",
  joyful: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
  calm: "bg-sky-50 text-sky-600 dark:bg-sky-900/30 dark:text-sky-300",
  deduction: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300",
  fear: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
  warm: "bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300",
  angry: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
  mysterious: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
  funny: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
}

export const SCENE_TONE_FILTERS = [
  "battle",
  "tense",
  "sad",
  "joyful",
  "calm",
] as const

export function sceneToneId(id?: string | null, raw?: string | null): string {
  return normalizeId(id, raw, SCENE_TONE_LEGACY_IDS) || "calm"
}

export function sceneToneLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, SCENE_TONE_LABEL_KEYS, sceneToneId(id, raw), raw)
}

export function sceneToneStyle(id?: string | null, raw?: string | null): string {
  return SCENE_TONE_STYLES[sceneToneId(id, raw)] ?? "bg-muted text-muted-foreground"
}

export function shouldDisplaySceneTone(id?: string | null, raw?: string | null): boolean {
  return sceneToneId(id, raw) !== "calm"
}

const SCENE_ROLE_LEGACY_IDS: Record<string, string> = {
  "提及": "mentioned",
  "场所": "setting",
  "主": "lead",
  "配": "supporting",
  "出场": "appearance",
}

const SCENE_ROLE_LABEL_KEYS: Record<string, TranslationKey> = {
  mentioned: "entity.scenes.role.mentioned",
  setting: "entity.scenes.role.setting",
  lead: "entity.scenes.role.lead",
  supporting: "entity.scenes.role.supporting",
  appearance: "entity.scenes.role.appearance",
}

export function sceneRoleId(id?: string | null, raw?: string | null): string {
  return normalizeId(id, raw, SCENE_ROLE_LEGACY_IDS) || "mentioned"
}

export function sceneRoleLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, SCENE_ROLE_LABEL_KEYS, sceneRoleId(id, raw), raw)
}

export function shouldDisplaySceneRole(id?: string | null, raw?: string | null): boolean {
  return sceneRoleId(id, raw) !== "mentioned"
}

const TIME_OF_DAY_LABEL_KEYS: Record<string, TranslationKey> = {
  morning: "shared.scenePanel.time.morning",
  noon: "shared.scenePanel.time.noon",
  dusk: "shared.scenePanel.time.dusk",
  night: "shared.scenePanel.time.night",
}

const TIME_OF_DAY_LEGACY_IDS: Record<string, string> = {
  "早": "morning",
  "晨": "morning",
  "午": "noon",
  "晚": "dusk",
  "暮": "dusk",
  "夜": "night",
}

export function sceneTimeOfDayLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  const normalized = normalizeId(id, raw, TIME_OF_DAY_LEGACY_IDS)
  return labelFromKey(t, TIME_OF_DAY_LABEL_KEYS, normalized, raw)
}

const LOCATION_TYPE_LABEL_KEYS: Record<string, TranslationKey> = {
  river: "domain.locationType.river",
  ferry: "domain.locationType.ferry",
  city: "domain.locationType.city",
  town: "domain.locationType.town",
  village: "domain.locationType.village",
  mountain: "domain.locationType.mountain",
  mountain_range: "domain.locationType.mountainRange",
  temple: "domain.locationType.temple",
  residence: "domain.locationType.residence",
  lake: "domain.locationType.lake",
  stream: "domain.locationType.stream",
  location: "domain.locationType.location",
  region: "domain.locationType.region",
}

const LOCATION_TYPE_LEGACY_IDS: Record<string, string> = {
  "河流": "river",
  "江": "river",
  "渡口": "ferry",
  "城市": "city",
  "城镇": "town",
  "村庄": "village",
  "山": "mountain",
  "山脉": "mountain_range",
  "寺庙": "temple",
  "府": "residence",
  "湖泊": "lake",
  "溪流": "stream",
  "地点": "location",
  "区域": "region",
}

export function locationTypeLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  const normalized = normalizeId(id, raw, LOCATION_TYPE_LEGACY_IDS)
  return labelFromKey(t, LOCATION_TYPE_LABEL_KEYS, normalized, raw)
}

const ORG_TYPE_LABEL_KEYS: Record<string, TranslationKey> = {
  army: "factions.orgType.army",
  court: "domain.orgType.court",
  sect: "factions.orgType.sect",
  gang: "factions.orgType.gang",
  clan: "factions.orgType.family",
  state: "factions.orgType.country",
  organization: "domain.orgType.organization",
}

const ORG_TYPE_LEGACY_IDS: Record<string, string> = {
  "军队": "army",
  "朝廷": "court",
  "门派": "sect",
  "宗门": "sect",
  "帮派": "gang",
  "家族": "clan",
  "国家": "state",
  "组织": "organization",
}

const ORG_TYPE_COLORS: Record<string, string> = {
  army: "#6366f1",
  court: "#0f766e",
  sect: "#8b5cf6",
  gang: "#ef4444",
  clan: "#f59e0b",
  state: "#3b82f6",
  organization: "#6b7280",
}

export function orgTypeId(id?: string | null, raw?: string | null): string {
  return normalizeId(id, raw, ORG_TYPE_LEGACY_IDS) || "organization"
}

export function orgTypeLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, ORG_TYPE_LABEL_KEYS, orgTypeId(id, raw), raw)
}

export function orgTypeColor(id?: string | null, raw?: string | null): string {
  const normalized = orgTypeId(id, raw)
  return ORG_TYPE_COLORS[normalized] ?? "#6b7280"
}

const RELATION_TYPE_LABEL_KEYS: Record<string, TranslationKey> = {
  "family.parent_child": "domain.relation.parentChild",
  "family.sibling": "domain.relation.sibling",
  "social.sworn_sibling": "domain.relation.swornSibling",
  "family.extended": "domain.relation.extendedFamily",
  "family.grandparent": "domain.relation.grandparent",
  "family.cousin": "domain.relation.cousin",
  "family.relative": "domain.relation.relative",
  "family.clan": "domain.relation.clan",
  "intimate.spouse": "domain.relation.spouse",
  "intimate.lover": "domain.relation.lover",
  "hostile.love_rival": "domain.relation.loveRival",
  "social.courtship": "domain.relation.courtship",
  "hostile.forced_marriage": "domain.relation.forcedMarriage",
  "social.admiration": "domain.relation.admiration",
  "hierarchical.master_servant": "domain.relation.masterServant",
  "hierarchical.teacher_student": "domain.relation.teacherStudent",
  "hierarchical.ruler_subject": "domain.relation.rulerSubject",
  "hierarchical.superior_subordinate": "domain.relation.superiorSubordinate",
  "social.friend": "domain.relation.friend",
  "social.same_sect": "domain.relation.sameSect",
  "social.fellow_disciple": "domain.relation.fellowDisciple",
  "social.classmate": "domain.relation.classmate",
  "social.colleague": "domain.relation.colleague",
  "social.neighbor": "domain.relation.neighbor",
  "social.partner": "domain.relation.partner",
  "social.ally": "domain.relation.ally",
  "social.family_friend": "domain.relation.familyFriend",
  "social.benefactor": "domain.relation.benefactor",
  "social.encounter": "domain.relation.encounter",
  "hostile.enemy": "domain.relation.enemy",
  other: "domain.relation.other",
}

export function relationTypeLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, RELATION_TYPE_LABEL_KEYS, id, raw)
}

const CONCEPT_CATEGORY_LEGACY_IDS: Record<string, string> = {
  "书": "book",
  "书籍": "book",
  book: "book",
  "功法": "cultivation_method",
  "部功法": "cultivation_method",
  "本功法": "cultivation_method",
  "bộ công pháp": "cultivation_method",
  "công pháp": "cultivation_method",
  cultivation_method: "cultivation_method",
  "cultivation method": "cultivation_method",
  "cultivation technique": "cultivation_method",
  "技能": "skill",
  "武技": "skill",
  "武学知识": "skill",
  "道法": "skill",
  "kỹ năng": "skill",
  skill: "skill",
  spell: "skill",
  "martial art": "skill",
  social_status: "social_status",
  "身份": "social_status",
  "身分": "social_status",
  "职务": "social_status",
  "职位": "social_status",
  "人物称号": "social_status",
  title: "social_status",
  status: "social_status",
  role: "social_status",
  "vai trò": "social_status",
  "thân phận": "social_status",
  "chức vụ": "social_status",
  "组织": "organization",
  "tổ chức": "organization",
  organization: "organization",
  "组织部门": "organization_department",
  organization_department: "organization_department",
  "组织制度": "organization_structure",
  "组织结构": "organization_structure",
  organization_structure: "organization_structure",
  "cấu trúc tổ chức": "organization_structure",
  "地点": "location",
  "地點": "location",
  "địa điểm": "location",
  location: "location",
  "建筑": "architecture",
  "kiến trúc": "architecture",
  architecture: "architecture",
  "机制": "mechanism",
  mechanism: "mechanism",
  "cơ chế": "mechanism",
  "药物": "medicine",
  "丹药": "medicine",
  "thuốc": "medicine",
  medicine: "medicine",
  "毒药": "poison",
  "độc dược": "poison",
  poison: "poison",
  "植物": "plant_fruit",
  "果实": "plant_fruit",
  "thực vật/quả": "plant_fruit",
  plant: "plant_fruit",
  fruit: "plant_fruit",
  "材料": "material",
  material: "material",
  "武器": "weapon",
  "vũ khí": "weapon",
  weapon: "weapon",
  "物品": "item",
  "vật phẩm": "item",
  item: "item",
  "惩罚": "punishment",
  punishment: "punishment",
}

const CONCEPT_CATEGORY_LABEL_KEYS: Record<string, TranslationKey> = {
  book: "domain.conceptCategory.book",
  cultivation_method: "domain.conceptCategory.cultivationMethod",
  skill: "domain.conceptCategory.skill",
  social_status: "domain.conceptCategory.socialStatus",
  organization: "domain.conceptCategory.organization",
  organization_department: "domain.conceptCategory.organizationDepartment",
  organization_structure: "domain.conceptCategory.organizationStructure",
  location: "domain.conceptCategory.location",
  architecture: "domain.conceptCategory.architecture",
  mechanism: "domain.conceptCategory.mechanism",
  medicine: "domain.conceptCategory.medicine",
  poison: "domain.conceptCategory.poison",
  plant_fruit: "domain.conceptCategory.plantFruit",
  material: "domain.conceptCategory.material",
  weapon: "domain.conceptCategory.weapon",
  item: "domain.conceptCategory.item",
  punishment: "domain.conceptCategory.punishment",
}

export function conceptCategoryId(id?: string | null, raw?: string | null): string {
  const normalized = normalizeId(id, raw, CONCEPT_CATEGORY_LEGACY_IDS)
  return normalized || firstValue(id, raw)
}

export function conceptCategoryLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, CONCEPT_CATEGORY_LABEL_KEYS, conceptCategoryId(id, raw), raw)
}

const ITEM_TYPE_LABEL_KEYS: Record<string, TranslationKey> = {
  strategy_book: "domain.itemType.strategyBook",
  weapon: "domain.itemType.weapon",
  artifact: "domain.itemType.artifact",
  medicine: "domain.itemType.medicine",
  letter: "domain.itemType.letter",
  item: "domain.itemType.item",
}

const ITEM_ACTION_LABEL_KEYS: Record<string, TranslationKey> = {
  appear: "domain.itemAction.appear",
  obtain: "domain.itemAction.obtain",
  use: "domain.itemAction.use",
  give: "domain.itemAction.give",
  consume: "domain.itemAction.consume",
  lose: "domain.itemAction.lose",
  destroy: "domain.itemAction.destroy",
}

export function itemTypeLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, ITEM_TYPE_LABEL_KEYS, id, raw)
}

export function itemActionLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, ITEM_ACTION_LABEL_KEYS, id, raw)
}

const ORG_ACTION_LABEL_KEYS: Record<string, TranslationKey> = {
  join: "domain.orgAction.join",
  leave: "domain.orgAction.leave",
  promote: "domain.orgAction.promote",
  killed: "domain.orgAction.killed",
  defect: "domain.orgAction.defect",
  expel: "domain.orgAction.expel",
  appear: "domain.orgAction.appear",
  create: "domain.orgAction.create",
  found: "domain.orgAction.found",
}

export function orgActionLabel(t: TFunc, id?: string | null, raw?: string | null): string {
  return labelFromKey(t, ORG_ACTION_LABEL_KEYS, id, raw)
}

export function isLeavingOrgAction(id?: string | null, raw?: string | null): boolean {
  const value = id || raw || ""
  return new Set(["leave", "killed", "defect", "expel", "离开", "阵亡", "叛出", "逐出", "退出", "离去", "战死"]).has(value)
}
