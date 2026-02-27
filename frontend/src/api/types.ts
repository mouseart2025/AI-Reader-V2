export interface Novel {
  id: string
  title: string
  author: string | null
  total_chapters: number
  total_words: number
  created_at: string
  updated_at: string
  is_sample: boolean
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

export interface OllamaModel {
  name: string
  size: number
  modified_at?: string
}

export interface HardwareInfo {
  total_ram_gb: number
  platform: string
  arch: string
}

export interface ModelRecommendation {
  name: string
  display_name: string
  size_gb: number
  min_ram_gb: number
  description: string
  recommended: boolean
  installed: boolean
}

export interface CloudProvider {
  id: string
  name: string
  base_url: string
  default_model: string
  models?: string[]       // 该供应商支持的模型预设列表
  api_format?: string     // "openai" (默认) | "anthropic"
}

export interface CloudConfig {
  provider: string
  base_url: string
  model: string
  has_api_key: boolean
  api_key_masked: string
}

export interface EnvironmentCheck {
  llm_provider: string
  llm_model: string
  // Ollama mode fields
  ollama_running?: boolean
  ollama_status?: "not_installed" | "installed_not_running" | "running"
  ollama_url?: string
  required_model?: string
  model_available?: boolean
  available_models?: (OllamaModel | string)[]
  // Cloud mode fields
  llm_base_url?: string
  api_available?: boolean
  error?: string
}

export interface BenchmarkResult {
  model: string
  provider: string
  context_window: number
  benchmark: {
    elapsed_ms: number
    input_tokens: number
    output_tokens: number
    tokens_per_second: number
    estimated_chapter_time_s: number
    estimated_chapter_chars: number
  }
  quality: {
    overall_score: number
    entity_recall: number
    relation_recall: number
    notes: string[]
  }
}

export interface BenchmarkRecord {
  id: number
  model: string
  provider: string
  context_window: number
  elapsed_ms: number
  tokens_per_second: number
  estimated_chapter_time_s: number
  quality_score: number | null
  created_at: string
}

// ── Analysis ──────────────────────────────────

export interface AnalyzeRequest {
  chapter_start?: number | null
  chapter_end?: number | null
  force?: boolean
}

export interface CostEstimate {
  is_cloud: boolean
  novel_title: string
  chapter_range: [number, number]
  chapter_count: number
  total_words: number
  provider: string
  model: string
  estimated_input_tokens: number
  estimated_output_tokens: number
  estimated_total_tokens: number
  estimated_cost_usd: number
  estimated_cost_cny: number
  includes_prescan: boolean
  input_price_per_1m: number
  output_price_per_1m: number
  monthly_budget_cny: number
  monthly_used_cny: number
}

export interface AnalysisTimingStats {
  last_chapter_ms: number
  avg_chapter_ms: number
  elapsed_total_ms: number
  eta_ms: number
}

export interface AnalysisTimingSummary {
  total_ms: number
  avg_chapter_ms: number
  min_chapter_ms: number
  max_chapter_ms: number
  chapters_processed: number
}

export interface AnalysisQualitySummary {
  truncated_chapters: number
  segmented_chapters: number
  total_segments: number
}

export interface FailedChapter {
  chapter_num: number
  title: string
  analysis_error: string | null
  error_type: "timeout" | "content_policy" | "http_error" | "parse_error" | "unknown" | null
}

export interface AnalysisTask {
  id: string
  novel_id: string
  status: "pending" | "running" | "paused" | "completed" | "cancelled"
  chapter_start: number
  chapter_end: number
  current_chapter: number
  timing_summary?: AnalysisTimingSummary | null
  created_at: string
  updated_at: string
}

export interface AnalysisStats {
  entities: number
  relations: number
  events: number
}

export interface AnalysisCostStats {
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  total_cost_cny: number
  estimated_remaining_usd: number
  estimated_remaining_cny: number
  is_cloud: boolean
  monthly_used_cny: number
  monthly_budget_cny: number
}

export interface BudgetInfo {
  monthly_budget_cny: number
  monthly_used_cny: number
  monthly_used_usd: number
  monthly_input_tokens: number
  monthly_output_tokens: number
}

/** All WS messages carry novel_id for cross-novel filtering */
interface WsBase {
  novel_id?: string
}

export interface WsProgress extends WsBase {
  type: "progress"
  chapter: number
  total: number
  done: number
  stats: AnalysisStats
  cost?: AnalysisCostStats
  timing?: AnalysisTimingStats
}

export interface WsChapterDone extends WsBase {
  type: "chapter_done"
  chapter: number
  status: "completed" | "failed" | "retry_success"
  error?: string
}

export interface WsTaskStatus extends WsBase {
  type: "task_status"
  status: string
  stats?: AnalysisStats
  cost?: AnalysisCostStats
}

export interface WsProcessing extends WsBase {
  type: "processing"
  chapter: number
  total: number
  timing?: AnalysisTimingStats
}

export interface WsStage extends WsBase {
  type: "stage"
  chapter: number
  stage_label: string
  llm_model?: string
  llm_provider?: string // "ollama" | "openai"
}

export type AnalysisWsMessage = WsProgress | WsProcessing | WsChapterDone | WsTaskStatus | WsStage

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
  siblings?: string[]
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
  role?: string | null  // "setting" | "referenced" | "boundary"
  locked?: boolean
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

export interface LocationConflict {
  type: string
  severity: string
  description: string
  chapters: number[]
  entity: string
  details: Record<string, unknown>
}

export interface MapData {
  locations: MapLocation[]
  trajectories: Record<string, TrajectoryPoint[]>
  spatial_constraints: SpatialConstraint[]
  layout: MapLayoutItem[]
  layout_mode: "constraint" | "hierarchy" | "layered" | "geographic"
  terrain_url: string | null
  rivers?: { points: number[][]; width: number }[]
  analyzed_range: [number, number]
  region_boundaries?: RegionBoundary[]
  portals?: PortalInfo[]
  revealed_location_names?: string[]
  world_structure?: { layers: MapLayerInfo[] }
  layer_layouts?: Record<string, MapLayoutItem[]>
  spatial_scale?: string
  canvas_size?: { width: number; height: number }
  geography_context?: GeographyChapter[]
  geo_coords?: Record<string, { lat: number; lng: number }>
  location_conflicts?: LocationConflict[]
  max_mention_count?: number
  suggested_min_mentions?: number
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

export interface HierarchyChange {
  location: string
  old_parent: string | null
  new_parent: string | null
  change_type: "added" | "changed" | "removed"
  auto_select: boolean
  reason: string
}

export interface HierarchyRebuildResult {
  changes: HierarchyChange[]
  location_tiers: Record<string, string>
  summary: {
    added: number
    changed: number
    removed: number
    total: number
    old_root_count: number
    new_root_count: number
    scene_analysis_used: boolean
    llm_review_used: boolean
  }
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

// ── Cost Detail ──────────────────────────────

export interface ChapterCostDetail {
  chapter_id: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  cost_cny: number
  entity_count: number
  extraction_ms: number
  extracted_at: string | null
  llm_model: string
}

export interface CostDetailSummary {
  total_chapters: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  total_cost_cny: number
  total_entities: number
}

export interface CostDetailResponse {
  novel_id: string
  novel_title: string
  chapters: ChapterCostDetail[]
  summary: CostDetailSummary
  model: string
  started_at: string | null
  completed_at: string | null
}

export interface AnalysisRecord {
  task_id: string
  novel_id: string
  novel_title: string
  status: string
  chapter_range: [number, number]
  chapter_count: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  total_cost_cny: number
  started_at: string
  completed_at: string
}

// ── Series Bible ──────────────────────────────

export interface SeriesBibleRequest {
  modules?: string[]
  template?: string
  format?: string
  chapter_start?: number
  chapter_end?: number
}

export interface SeriesBibleTemplate {
  id: string
  name: string
  description: string
}

export const SERIES_BIBLE_MODULES = [
  { id: "characters", label: "人物档案" },
  { id: "relations", label: "关系网络" },
  { id: "locations", label: "地点百科" },
  { id: "items", label: "物品道具" },
  { id: "orgs", label: "组织势力" },
  { id: "timeline", label: "时间线" },
] as const

export const SERIES_BIBLE_TEMPLATES = [
  { id: "complete", name: "通用模板", description: "完整世界观文档，含全部模块" },
  { id: "author", name: "网文作者套件", description: "人物设定卡 + 势力分布 + 时间线大纲" },
] as const

// ── Scenes (Screenplay Mode) ─────────────────────

export interface SceneCharacterRole {
  name: string
  role: "主" | "配" | "提及"
}

export interface Scene {
  index: number
  chapter: number
  title: string
  location: string
  characters: string[]
  description: string
  dialogue_count: number
  paragraph_range?: [number, number]
  events?: { summary: string; type: string }[]
  // Rich metadata from multi-signal scene extractor
  heading?: string
  time_of_day?: string        // "早" | "午" | "晚" | "夜" | ""
  emotional_tone?: string     // "战斗" | "紧张" | "悲伤" | "欢乐" | "平静" | ""
  key_dialogue?: string[]     // 1-2 key dialogue lines
  character_roles?: SceneCharacterRole[]
  event_type?: string         // "对话" | "战斗" | "旅行" | "描写" | "回忆"
  summary?: string            // LLM-generated 20-50 char scene summary
}

export interface ChapterScenesResponse {
  chapter: number
  scenes: Scene[]
  scene_count: number
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

// ── Backup (full data) ──────────────────────────

export interface BackupNovelPreview {
  id: string
  title: string
  total_chapters: number
  conflict: boolean
  existing_id: string | null
}

export interface BackupPreview {
  backup_format_version: number
  exported_at: string
  novel_count: number
  novels: BackupNovelPreview[]
  conflict_count: number
  zip_size_bytes: number
}

export interface BackupImportResult {
  total: number
  imported: number
  skipped: number
  overwritten: number
  errors: string[]
}
