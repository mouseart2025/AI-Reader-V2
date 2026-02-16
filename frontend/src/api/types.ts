export interface Novel {
  id: string
  title: string
  author: string | null
  total_chapters: number
  total_words: number
  created_at: string
  updated_at: string
  analysis_progress: number
  reading_progress: number
  last_opened: string | null
}

export interface Chapter {
  id: number
  novel_id: string
  chapter_num: number
  volume_num: number | null
  volume_title: string | null
  title: string
  word_count: number
  analysis_status: string
  analyzed_at: string | null
  is_excluded?: number
}

export interface ChapterContent extends Chapter {
  content: string
}

export interface ChapterEntity {
  name: string
  type: "person" | "location" | "item" | "org" | "concept"
}

export interface UserState {
  novel_id: string
  last_chapter: number | null
  scroll_position: number
  chapter_range?: string | null
  updated_at?: string
}

export interface EntitySummary {
  name: string
  type: string
  chapter_count: number
  first_chapter: number
}

export interface NovelsListResponse {
  novels: Novel[]
}

export interface ChapterPreview {
  chapter_num: number
  title: string
  word_count: number
  is_suspect?: boolean
  content_preview?: string
}

export interface SplitDiagnosis {
  tag: string
  message: string
  suggestion?: string
}

export interface SuspectLine {
  line_num: number
  content: string
  category: string
  confidence: number
}

export interface HygieneReport {
  total_suspect_lines: number
  by_category: Record<string, number>
  samples: SuspectLine[]
}

export interface UploadPreviewResponse {
  title: string
  author: string | null
  file_hash: string
  total_chapters: number
  total_words: number
  chapters: ChapterPreview[]
  warnings: string[]
  duplicate_novel_id: string | null
  diagnosis?: SplitDiagnosis | null
  hygiene_report?: HygieneReport | null
  matched_mode?: string | null
}

export interface ConfirmImportRequest {
  file_hash: string
  title: string
  author?: string | null
  excluded_chapters?: number[]
}

export interface ReSplitRequest {
  file_hash: string
  mode?: string | null
  custom_regex?: string | null
}

export interface CleanAndReSplitRequest {
  file_hash: string
  clean_mode?: string
}

export interface SplitModesResponse {
  modes: string[]
}

export interface HealthResponse {
  status: string
}

export interface EnvironmentCheck {
  llm_provider: string
  llm_model: string
  // Ollama mode fields
  ollama_running?: boolean
  ollama_url?: string
  required_model?: string
  model_available?: boolean
  available_models?: string[]
  // Cloud mode fields
  llm_base_url?: string
  api_available?: boolean
  error?: string
}

// ── Analysis ──────────────────────────────────

export interface AnalyzeRequest {
  chapter_start?: number | null
  chapter_end?: number | null
  force?: boolean
}

export interface AnalysisTask {
  id: string
  novel_id: string
  status: "pending" | "running" | "paused" | "completed" | "cancelled"
  chapter_start: number
  chapter_end: number
  current_chapter: number
  created_at: string
  updated_at: string
}

export interface AnalysisStats {
  entities: number
  relations: number
  events: number
}

export interface WsProgress {
  type: "progress"
  chapter: number
  total: number
  done: number
  stats: AnalysisStats
}

export interface WsChapterDone {
  type: "chapter_done"
  chapter: number
  status: "completed" | "failed"
  error?: string
}

export interface WsTaskStatus {
  type: "task_status"
  status: string
  stats?: AnalysisStats
}

export interface WsProcessing {
  type: "processing"
  chapter: number
  total: number
}

export type AnalysisWsMessage = WsProgress | WsProcessing | WsChapterDone | WsTaskStatus

// ── Entity Profiles ──────────────────────────────

export type EntityType = "person" | "location" | "item" | "org" | "concept"

export interface PersonProfile {
  name: string
  type: "person"
  aliases: { name: string; first_chapter: number }[]
  appearances: { chapters: number[]; description: string }[]
  abilities: { chapter: number; dimension: string; name: string; description: string }[]
  relations: {
    other_person: string
    stages: {
      chapters: number[]
      relation_type: string
      evidences: string[]
      evidence: string
    }[]
    category: string
  }[]
  items: { chapter: number; item_name: string; item_type: string; action: string; description: string }[]
  experiences: { chapter: number; summary: string; type: string; location: string | null }[]
  stats: Record<string, number>
}

export interface LocationProfile {
  name: string
  type: "location"
  location_type: string
  parent: string | null
  children: string[]
  descriptions: { chapter: number; description: string }[]
  visitors: { name: string; chapters: number[]; is_resident: boolean }[]
  events: { chapter: number; summary: string; type: string }[]
  stats: Record<string, number>
}

export interface ItemProfile {
  name: string
  type: "item"
  item_type: string
  flow: { chapter: number; action: string; actor: string; recipient: string | null; description: string }[]
  related_items: string[]
  stats: Record<string, number>
}

export interface OrgProfile {
  name: string
  type: "org"
  org_type: string
  member_events: { chapter: number; member: string; role: string | null; action: string; description: string }[]
  org_relations: { chapter: number; other_org: string; relation_type: string }[]
  stats: Record<string, number>
}

export type EntityProfile = PersonProfile | LocationProfile | ItemProfile | OrgProfile

// ── Map ──────────────────────────────────────

export type LayerType = "overworld" | "underground" | "sky" | "sea" | "pocket" | "spirit"

export interface MapLayerInfo {
  layer_id: string
  name: string
  layer_type: LayerType
  location_count: number
  region_count: number
  merged?: boolean
}

export interface PortalInfo {
  name: string
  source_layer: string
  source_location: string
  target_layer: string
  target_layer_name: string
  target_location: string
  is_bidirectional: boolean
}

export interface RegionBoundary {
  region_name: string
  color: string
  polygon: [number, number][]
  center: [number, number]
}

export interface MapLocation {
  id: string
  name: string
  type: string
  parent: string | null
  level: number
  mention_count: number
  tier?: string    // "world" | "continent" | "kingdom" | "region" | "city" | "site" | "building"
  icon?: string    // "city" | "mountain" | "cave" | "temple" | "generic" | ...
}

export interface MapLayoutItem {
  name: string
  x: number
  y: number
  radius?: number
  is_portal?: boolean
  source_layer?: string
  target_layer?: string
}

export interface SpatialConstraint {
  source: string
  target: string
  relation_type: string
  value: string
  confidence: string
  narrative_evidence: string
}

export interface TrajectoryPoint {
  location: string
  chapter: number
}

export interface MapData {
  locations: MapLocation[]
  trajectories: Record<string, TrajectoryPoint[]>
  spatial_constraints: SpatialConstraint[]
  layout: MapLayoutItem[]
  layout_mode: "constraint" | "hierarchy" | "layered" | "geographic"
  terrain_url: string | null
  analyzed_range: [number, number]
  region_boundaries?: RegionBoundary[]
  portals?: PortalInfo[]
  revealed_location_names?: string[]
  world_structure?: { layers: MapLayerInfo[] }
  layer_layouts?: Record<string, MapLayoutItem[]>
  spatial_scale?: string
  canvas_size?: { width: number; height: number }
  geography_context?: GeographyChapter[]
}

export interface GeographyEntry {
  type: "location" | "spatial"
  name: string
  text: string
}

export interface GeographyChapter {
  chapter: number
  entries: GeographyEntry[]
}

// ── World Structure Overrides ─────────────────

export type OverrideType = "location_region" | "location_layer" | "location_parent" | "location_tier" | "add_portal" | "delete_portal"

export interface WorldStructureOverride {
  id: number
  override_type: OverrideType
  override_key: string
  override_json: Record<string, unknown>
  created_at: string
}

export interface WorldStructureRegion {
  name: string
  cardinal_direction: string | null
  region_type: string | null
  parent_region: string | null
  description: string
}

export interface WorldStructureLayer {
  layer_id: string
  name: string
  layer_type: LayerType
  description: string
  regions: WorldStructureRegion[]
}

export interface WorldStructurePortal {
  name: string
  source_layer: string
  source_location: string
  target_layer: string
  target_location: string
  is_bidirectional: boolean
  first_chapter: number | null
}

export interface WorldStructureData {
  novel_id: string
  layers: WorldStructureLayer[]
  portals: WorldStructurePortal[]
  location_region_map: Record<string, string>
  location_layer_map: Record<string, string>
  location_parents: Record<string, string>
  location_tiers: Record<string, string>
  location_icons: Record<string, string>
  novel_genre_hint: string | null
  spatial_scale: string | null
}

// ── Chat ──────────────────────────────────────

export interface Conversation {
  id: string
  novel_id: string
  title: string
  created_at: string
  updated_at: string
  message_count?: number
}

export interface ChatMessage {
  id: number
  conversation_id: string
  role: "user" | "assistant"
  content: string
  sources: number[]
  created_at: string
}

export interface ChatWsOutgoing {
  novel_id: string
  question: string
  conversation_id: string | null
}

export type ChatWsIncoming =
  | { type: "token"; content: string }
  | { type: "sources"; chapters: number[] }
  | { type: "done" }
  | { type: "error"; message: string }

// ── Prescan Dictionary ──────────────────────────
export type PrescanStatus = "pending" | "running" | "completed" | "failed"

export interface PrescanStatusResponse {
  status: PrescanStatus
  entity_count: number
  created_at: string | null
}

export interface EntityDictItem {
  name: string
  entity_type: string
  frequency: number
  confidence: string
  aliases: string[]
  source: string
  sample_context: string | null
}

export interface EntityDictionaryResponse {
  data: EntityDictItem[]
  total: number
}

// ── Export / Import ──────────────────────────────

export interface ImportPreview {
  title: string
  author: string | null
  total_chapters: number
  total_words: number
  analyzed_chapters: number
  facts_count: number
  has_user_state: boolean
  data_size_bytes: number
  existing_novel_id: string | null
}
