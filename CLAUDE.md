# CLAUDE.md - AI Reader V2

## Project Overview

AI Reader V2 is a Chinese novel analysis platform. Users upload TXT novels, the system splits them into chapters, optionally pre-scans for high-frequency entities (jieba + LLM classification), then uses an LLM to extract structured facts per chapter (ChapterFact), aggregating them into entity profiles, visualizations, and a Q&A system. Supports local Ollama and cloud OpenAI-compatible APIs. All data stays on the local machine.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + TypeScript 5.9 + Vite 7 |
| UI | Tailwind CSS 4 + shadcn/ui + Radix UI + Lucide icons |
| State | Zustand 5 (7 stores) |
| Routing | React Router 7 (lazy-loaded pages) |
| Visualization | react-force-graph-2d (graph/factions), react-leaflet + Leaflet (geographic map) |
| Backend | Python 3.9+ + FastAPI (async) |
| Database | SQLite (aiosqlite) — single file at `~/.ai-reader-v2/data.db` |
| Vector DB | ChromaDB + BAAI/bge-base-zh-v1.5 embeddings |
| LLM | Ollama (local, default qwen3:8b) or OpenAI-compatible API (cloud) |
| Chinese NLP | jieba (entity pre-scan word segmentation) |
| Package mgmt | npm (frontend), uv (backend) |

## Quick Start

```bash
# Backend
cd backend && uv sync && uv run uvicorn src.api.main:app --reload

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

- Frontend dev server: http://localhost:5173 (proxies `/api` and `/ws` to backend)
- Backend dev server: http://localhost:8000
- Ollama must be running locally on port 11434 (or configure `LLM_PROVIDER=openai` for cloud mode)

## Project Structure

```
AI-Reader-V2/
├── backend/
│   ├── pyproject.toml              # Python deps (uv)
│   └── src/
│       ├── api/
│       │   ├── main.py             # FastAPI app entry, CORS, lifespan
│       │   ├── routes/             # REST endpoints (15 routers)
│       │   └── websocket/          # WS handlers (analysis progress, chat streaming)
│       ├── services/               # Business logic (14 services)
│       ├── extraction/             # LLM fact extraction + entity pre-scan pipeline
│       │   ├── entity_pre_scanner.py  # jieba stats + LLM classification
│       │   └── prompts/            # System prompt + few-shot examples
│       ├── db/                     # SQLite + ChromaDB data access
│       ├── models/                 # Pydantic schemas / dataclasses
│       ├── infra/                  # Config, LLM clients, context budget auto-scaling
│       └── utils/                  # Text processing, chapter splitting
├── frontend/
│   ├── package.json
│   ├── vite.config.ts              # Vite + Tailwind + proxy config
│   └── src/
│       ├── app/                    # App entry, router, layout
│       ├── pages/                  # Page components (12 routed)
│       ├── components/
│       │   ├── ui/                 # shadcn/ui base components
│       │   ├── shared/             # Reusable components (UploadDialog, ScenePanel, etc.)
│       │   ├── entity-cards/       # Entity card drawer system
│       │   ├── visualization/      # Graph, map, timeline, geography panel components
│       │   └── chat/               # Chat UI components
│       ├── stores/                 # Zustand stores (7 stores)
│       ├── api/                    # REST client + types
│       ├── hooks/
│       └── lib/
└── scripts/                        # Utility scripts (demo export, data tools)
```

## Key Architecture Concepts

### ChapterFact — Core Data Model

The system's central concept. Each chapter produces one `ChapterFact` JSON containing: characters, relationships, locations, item_events, org_events, events, new_concepts. Stored as JSON text in `chapter_facts.fact_json`. Entity profiles (PersonProfile, LocationProfile, etc.) are **aggregated on-the-fly** from ChapterFacts — not persisted as separate tables.

### Entity Pre-Scan (Optional, Before Analysis)

`EntityPreScanner` — Phase 1: jieba word segmentation + n-gram frequency stats + dialogue attribution regex + suffix pattern matching + naming pattern extraction (regex for "叫作/名叫/绰号" patterns) → candidate list. Phase 2: LLM classifies candidates into entity types with aliases. Output: `entity_dictionary` table. The dictionary is injected into the extraction prompt to improve entity recognition quality.

Includes numeric-prefix name recovery (e.g., "二愣子", "三太子") via POS recovery and candidate merging. Naming-source entries bypass the frequency cutoff to ensure explicitly introduced names are always included.

### Analysis Pipeline

`AnalysisService` → per-chapter loop → `ContextSummaryBuilder` (prior chapter summary) → `ChapterFactExtractor` (LLM call, with entity dictionary injection if available) → `FactValidator` → write to DB → WebSocket progress push. Supports pause/resume/cancel. Concurrency controlled by asyncio semaphore (1 concurrent LLM call for single-GPU).

### Token Budget Auto-Scaling

`context_budget.py` (`TokenBudget` dataclass + `compute_budget()` + `get_budget()`) — all LLM budget parameters (chapter truncation length, context summary limits, num_ctx, timeouts) are derived from the model's context window size via linear interpolation: 8K context → conservative "local" values, 128K+ context → generous "cloud" values, intermediate models get proportional values.

**Detection**: At startup and on model/mode switches, `detect_and_update_context_window()` queries Ollama or uses cloud defaults. Local Ollama models are capped to prevent KV cache bloat on consumer hardware. **Consumers**: `ChapterFactExtractor`, `ContextSummaryBuilder`, `SceneLLMExtractor`, `WorldStructureAgent`, `LocationHierarchyReviewer`. `_is_cloud` flag (set via `isinstance(llm, (OpenAICompatibleClient, AnthropicClient))`) controls JSON schema injection and max_tokens budget.

### Entity Alias Resolution

`AliasResolver` (`alias_resolver.py`) — builds `alias → canonical_name` mapping using Union-Find to merge overlapping alias groups. Merges BOTH sources: `entity_dictionary.aliases` (pre-scan) and `ChapterFact.characters[].new_aliases` (per-chapter extraction). Canonical name selection picks the shortest name among high-frequency candidates. Consumed by `entity_aggregator`, `visualization_service`, and the entities API.

**Safety filtering**: Multi-tier system (`_alias_safety_level()`) prevents generic terms (kinship terms, titles, collective references like "妖精"/"那怪") from becoming Union-Find nodes and bridging unrelated character groups. Unsafe names pass through their safe aliases without registering as UF nodes.

### Fact Validation — Morphological Filtering

`FactValidator` (`fact_validator.py`) — post-LLM validation that filters out incorrectly extracted entities. Location validation uses `_is_generic_location()` with structural rules based on Chinese place name morphology (专名+通名 structure). Person validation uses `_is_generic_person()` to filter pure titles and generic references. Auto-created parent/region locations use `_infer_type_from_name()` to derive type from Chinese name suffix.

**Dictionary-driven name corrections**: Fixes LLM extraction errors where numeric-prefix names are truncated (e.g., "愣子" → "二愣子"). **Alias-based character merge**: When character A lists character B as an alias and B exists separately, B is merged into A. **Homonym location disambiguation**: Generic architectural names (夹道, 后门, etc.) are prepended with parent location using middle-dot separator. Syncs across all cross-references in the ChapterFact.

### Context Summary Builder — Coreference Resolution

`ContextSummaryBuilder` (`context_summary_builder.py`) — builds prior-chapter context for LLM extraction. Injects ALL known locations with coreference instructions, enabling the LLM to resolve anaphoric references (e.g., "小城" → "青牛镇"). Always injects entity dictionary and world structure even for early chapters with no preceding facts. Naming-source entities are displayed in a separate emphasized section.

**Macro hub anchoring**: `_build_macro_hub_section()` injects a top-down view of major geographic areas into the LLM prompt, solving the problem where the LLM doesn't know about macro regions and fails to assign correct intermediate parents.

### Location Parent Voting — Authoritative Hierarchy

`WorldStructureAgent` accumulates parent votes across all chapters for each location. Sources: `ChapterFact.locations[].parent`, `spatial_relationships[relation_type=="contains"]` (weighted by confidence), and chapter primary setting inference. The winner for each child is stored in `WorldStructure.location_parents`. Cycle detection (DFS) breaks the weakest link. User overrides take precedence.

**Suffix Rank System**: Chinese location name suffixes encode geographic scale (界>国>城>谷>洞>殿). `_resolve_parents()` uses suffix rank as the PRIMARY signal for parent-child direction validation, falling back to LLM-classified tiers only when both names lack recognizable suffixes.

**Sibling clustering**: Bidirectional conflict resolution detects sibling pairs (same suffix rank, close vote ratios) and reassigns both to a common parent via `_find_common_parent()`. Peer-aware detection uses explicit `LocationFact.peers` evidence. Same-tier sibling promotion catches single-direction same-scale pairs.

**Hierarchy consolidation**: `consolidate_hierarchy()` handles tier inversion fixes, noise root rescue, oscillation damping, and tiered catch-all orphan adoption (prefix matching → dominant intermediate node → tier-gated uber_root fallback). Micro-location pruning skips low-vote sub-locations.

**LLM-assisted quality**: `MacroSkeletonGenerator` pre-generates a 2-3 level geographic skeleton via LLM. `LocationHierarchyReviewer` provides self-reflection (validates suspicious pairs) and subtree partition validation (independent LLM validation per subtree). Synonym location detection merges aliases.

**Two-step hierarchy rebuild**: `POST /rebuild-hierarchy` streams SSE progress events and returns a diff without saving. `POST /apply-hierarchy-changes` applies user-selected changes and auto-clears map overrides for repositioning. Three layers of cycle detection defense (resolve, consolidate, save).

**Consumers**: `visualization_service.get_map_data()` overrides parents and recalculates levels; `entity_aggregator.aggregate_location()` overrides parent and children; `encyclopedia_service` injects virtual parent nodes (uber-roots like "天下" that exist only in `location_parents`).

### Relation Normalization and Classification

`relation_utils.py` — shared module consumed by `entity_aggregator`, `visualization_service`, and `encyclopedia_service`. `_RELATION_TYPE_NORM` (70+ entries) maps LLM-generated relation type variants to canonical forms via exact-match then substring-match. `classify_relation_category()` assigns each normalized type to one of 6 categories: family, intimate, hierarchical, social, hostile, other. Used for PersonCard relation grouping, graph edge coloring, and encyclopedia org relation badges.

### Entity Aggregation — Relations

`entity_aggregator.py` — when building `PersonProfile.relations`, relation types are normalized before stage merging. Each `RelationStage` collects multiple `evidences` (deduplicated). Each `RelationChain` gets a `category` assignment.

### Graph Edge Aggregation

`visualization_service.py` — graph edges use `Counter`-based type frequency tracking instead of "latest chapter wins". Each edge outputs `relation_type` (most frequent), `all_types` (sorted by frequency), and `category`. Organization attribution falls back to counting character visits to org-type locations when `org_events` don't provide membership. API response includes `category_counts` and `type_counts`.

### GeoResolver — Real-World Coordinate Matching

`GeoResolver` (`geo_resolver.py`) — matches novel location names to real-world GeoNames coordinates. Two datasets: `cn` (Chinese locations, ~140K entries) and `world` (global cities with pop > 5000, ~50K entries). Chinese alternate name index (`backend/data/zh_geonames.tsv`) provides CJK matching for world dataset.

**Auto-detection**: `detect_geo_scope()` determines dataset based on genre + CJK ratio → `detect_geo_type()` uses quality-weighted notable matching → returns `"realistic"`, `"mixed"`, or `"fantasy"`. Fantasy/xianxia genres skip geo resolution. CN dataset fallback to world dataset for translated foreign names. Cached `geo_type` prevents chapter-range oscillation.

**Name resolution** uses 4-level matching: curated supplement → Chinese alternate name index → exact GeoNames match → Chinese suffix stripping + disambiguation by population.

### Map Layout

`map_layout_service.py` — uses **sunflower seed distribution** (golden angle) for organic location placement instead of uniform circular placement. Adaptive spread radius scales with child count to prevent clustering. `ConstraintSolver` uses differential evolution with force-directed pre-layout seeding for primary layout computation.

### GeoMap — Leaflet Real-World Map

`GeoMap.tsx` — React-Leaflet component for geographic layout mode with CircleMarkers (size by mention_count, color by type), trajectory polylines, click-to-navigate from Geography panel, drag-to-reposition in edit mode, and auto fitBounds. `MapPage.tsx` switches between `GeoMap` (geographic) and `NovelMap` (all other modes).

### Graph Readability — Dense Network Optimization

`GraphPage.tsx` — relationship graph with readability features for complex novels (400+ characters):

- Edge weight filtering with backend-computed suggested minimum
- Smart auto-defaults for minChapters based on node count
- Label-inside-circle for large nodes, below-node labels for small nodes
- Force spacing scales with graph density
- Label collision detection (per-frame rect tracking)
- Dashed weak edges for weight <= 1
- Category filter chips (6 categories with colored toggle buttons)
- Dark mode adaptation via MutationObserver
- Interactive legend with clickable category entries

### Location Semantic Role & Peers

`LocationFact.role` (`"setting"` | `"referenced"` | `"boundary"` | `None`) distinguishes narrative function. Frontend renders referenced/boundary locations with reduced opacity. `LocationFact.peers` captures same-level adjacent entities (e.g., 宁国府 → peers: ["荣国府"]). Peers are used for parent vote suppression and sibling detection in hierarchy resolution.

### Map Features

**Conflict markers**: `conflict_detector.py` detects parent disagreements across chapters. Frontend renders animated red pulse rings on conflicting locations. Filters out homonym-prone names and requires multi-chapter minority evidence.

**Dense location optimization**: Two-stage filtering pipeline — mention count slider + tier collapse/expand (site/building tiers hidden by default with "+N" expand badges on parents).

**Terrain texture layer**: `terrainHints.ts` generates decorative SVG symbols (mountains, water, forest, desert, cave) around terrain-type locations. Tier-dependent sizing, deterministic placement, collision-aware.

**Label layout**: `computeLabelLayout()` tries 8 candidate anchor positions per label (multi-anchor collision detection). `labelAnnealing.ts` provides simulated annealing optimization for HD map export.

**River network**: `generate_rivers()` in backend uses gradient descent on OpenSimplex elevation field from water-type location sources. Frontend renders via d3 curveBasis.

**Whittaker biome matrix**: 5x5 elevation x moisture grid with bilinear interpolation for smooth terrain coloring. Lloyd relaxation for uniform Voronoi cells.

**Trajectory animation**: Dual-path progressive drawing with waypoint stay-duration scaling, pulse marker at current position, chapter labels, auto-pan following playback, adjustable speed.

**Hand-drawn style**: `roughjs` renders territories, rivers, and coastline with sketch aesthetics. Coastline uses convex hull + radial expansion + multi-octave noise. Area-based fill gating for large territories. Vignette and displacement map filter for region labels.

**Other map features**: Click-to-card (location click opens EntityCardDrawer), curved region labels via SVG textPath, override constraint locking (survives hierarchy changes).

### Entity Quality — Single-Character Filtering

Three-layer defense: (1) FactValidator minimum length for non-person entities, (2) entity_aggregator surname cross-reference for single-char persons, (3) frontend safety net filter.

### Encyclopedia

`encyclopedia_service.py` + `EncyclopediaPage.tsx`. Entry enrichment with chapter_count, tier/icon. Four entity card types (Person/Location/Item/Org) with cross-page navigation, scene index (`EntityScenes.tsx`), and novelId prop. LocationCard includes spatial relationships, LocationMiniMap (parent + siblings SVG), and scene index. Location conflict display in hierarchy tree. "世界观" world view tab shows WorldStructure data. Route order: `/location-conflicts` before `/{name}` wildcard.

### Scene Panel — Reading Page Integration

Scene/screenplay as right-side panel (`ScenePanel.tsx`) in ReadingPage. Toolbar toggle, paragraph-level rendering with colored scene borders, scene card click-to-scroll. Shared components exported from `components/shared/ScenePanel.tsx`.

### Analysis Timing & Quality Tracking

Per-chapter timing with real-time ETA via WebSocket. `ExtractionMeta` reports truncation and segment count. Live timing persists in memory (survives page navigation) and via REST. Auto-retry for failed chapters (skipping content_policy failures).

### Analysis Failure Resilience

**Error persistence**: `analysis_error` and `error_type` columns on chapters table. Five error types: timeout, parse_error, content_policy, http_error, unknown. **Task state recovery**: `recover_stale_tasks()` at startup resets stuck "running" tasks to "paused". **Post-analysis LLM timeout**: Hierarchy review wrapped with timeout, continues non-fatally on failure.

### Model Benchmark

`POST /model-benchmark` runs extraction against golden standard. Quality scoring via string matching (entity_recall * 0.6 + relation_recall * 0.4). Results saved to `benchmark_records` table. History view in SettingsPage.

### Timeline Data Aggregation

`get_timeline_data()` aggregates events from 6 sources: original events, character first appearances, item events, org events, relationship changes, and scene emotional tone linking. Noise filtering removes trivial item actions and one-time walk-on characters. Relationship change events detect new relations and type changes. Scene tone matching via participant overlap. Frontend: type filters, swimlane threshold, auto-collapse for low-importance chapters, emotional tone badges.

### Reading Page

`ReadingPage.tsx` — primary reading interface. Entity highlight control (toggle + per-type filter, `H` shortcut). Reading progress indicators (top bar + sidebar). Scene panel with error handling, cache, character/tone filters. Entity card drawer with profile cache. Keyboard shortcuts (arrows, Escape, S, H). Bookmark system (backend `bookmarks` table + 3 endpoints). Chapter preload via requestIdleCallback.

### Bookshelf

`BookshelfPage.tsx` — app landing page. Search & sort (recent/title/chapters/words). Card info density (analysis progress, reading progress, relative timestamps). Delete confirmation with associated data counts. Upload: drag-to-upload, upload progress via XHR, cache expiry handling. Keyboard shortcuts (N, /, Escape). Import/Export dropdown menu. DB transaction protection for imports.

### Export (Series Bible)

`ExportPage.tsx` — 4 formats (markdown/docx/pdf/xlsx). Relation chain dedup via `_compress_chain()`. Export format v3 (adds bookmarks, map overrides, world structure overrides). file_hash conflict detection for imports. Noise filters for entity dictionary (remove unknown type) and items (generic items). Timeline noise filter. Template selector for all formats. Chapter range selector. Batch conversation export. Dynamic filename with template + chapter range.

### Two Databases Only

- **SQLite**: novels, chapters, chapter_facts, entity_dictionary, conversations, messages, user_state, analysis_tasks, map_layouts, map_user_overrides, world_structures, layer_layouts, world_structure_overrides, benchmark_records, bookmarks (15 tables)
- **ChromaDB**: chapter embeddings + entity embeddings for semantic search

## Code Conventions

### Backend (Python)

- **Async everywhere**: all DB calls use `aiosqlite`, HTTP via `httpx`, LLM via async
- **File naming**: `snake_case.py`
- **Classes**: `PascalCase` (e.g., `AnalysisService`, `ChapterFactExtractor`)
- **Functions**: `snake_case` (e.g., `get_chapter_facts()`, `aggregate_person()`)
- **Constants**: `UPPER_SNAKE_CASE`
- **Router pattern**: `APIRouter(prefix="/api/novels", tags=["novels"])`
- **Error messages**: Chinese language
- **No ORM**: direct SQL + Pydantic models (ChapterFact is deeply nested JSON, ORM adds complexity)

### Frontend (React + TypeScript)

- **Components**: `PascalCase.tsx` (e.g., `BookshelfPage.tsx`, `EntityDrawer.tsx`)
- **Stores**: `camelCase` + `Store.ts` (e.g., `novelStore.ts`, `chatStore.ts`)
- **Hooks**: `use` prefix, `camelCase.ts` (e.g., `useEntity.ts`)
- **Types/Interfaces**: `PascalCase`, no `I` prefix
- **Path alias**: `@/` maps to `src/` (configured in tsconfig + vite)
- **State**: Zustand stores with pattern: `{ data, loading, error, fetchXxx, setXxx }`
- **Entity colors**: person=blue, location=green, item=orange, org=purple, concept=gray (consistent everywhere)

### API Conventions

- Routes: lowercase plural nouns, kebab-case (e.g., `/api/novels/{id}/chapter-facts`)
- Query params: `snake_case` (e.g., `?chapter_start=1&chapter_end=50`)
- All visualization endpoints accept `chapter_start` and `chapter_end` for range filtering

### WebSocket Protocols

- `/ws/analysis/{novel_id}` — message types: `progress`, `processing`, `chapter_done`, `task_status`
- `/ws/chat/{session_id}` — message types: `token`, `sources`, `done`, `error`

## Environment Variables

```
AI_READER_DATA_DIR    # Default: ~/.ai-reader-v2/
OLLAMA_BASE_URL       # Default: http://localhost:11434
OLLAMA_MODEL          # Default: qwen3:8b
EMBEDDING_MODEL       # Default: BAAI/bge-base-zh-v1.5

# Cloud LLM mode (set LLM_PROVIDER=openai to use)
LLM_PROVIDER          # "ollama" (default) or "openai"
LLM_API_KEY           # API key for the cloud provider
LLM_BASE_URL          # Base URL (e.g., https://api.deepseek.com/v1)
LLM_MODEL             # Model name (e.g., deepseek-chat)
LLM_MAX_TOKENS        # Default: 8192
# LLM_PROVIDER_FORMAT is set automatically by update_cloud_config() — not an env var.
# "openai" (default, all OpenAI-compatible providers) or "anthropic" (Claude API).
# Controls auth header (Bearer vs x-api-key) and endpoint (/chat/completions vs /v1/messages).
```

## Common Commands

```bash
# Frontend
npm run dev          # Vite dev server (localhost:5173)
npm run build        # TypeScript check + Vite production build
npm run lint         # ESLint

# Backend
uv sync              # Install/update Python dependencies
uv run uvicorn src.api.main:app --reload   # Dev server (localhost:8000)
```

## Database Schema (SQLite)

15 tables: `novels`, `chapters`, `chapter_facts`, `entity_dictionary`, `conversations`, `messages`, `user_state`, `analysis_tasks`, `map_layouts`, `map_user_overrides`, `world_structures`, `layer_layouts`, `world_structure_overrides`, `benchmark_records`, `bookmarks`.

## Important Notes

- **Language**: All UI text, error messages, LLM prompts, and extraction rules are in **Chinese**
- **Privacy**: Data stays local. Cloud mode only sends LLM requests to the configured API endpoint — no telemetry
- **Dual LLM backend**: `LLM_PROVIDER=ollama` (default, local) or `LLM_PROVIDER=openai` (cloud). Cloud supports 10 providers: DeepSeek, MiniMax, Qwen, Moonshot, Zhipu, SiliconFlow, Yi, OpenAI, Gemini, Anthropic. Anthropic uses a separate `AnthropicClient` (`anthropic_client.py`) with `x-api-key` auth and `/v1/messages` endpoint; all others use `OpenAICompatibleClient`. `LLM_PROVIDER_FORMAT` (`"openai"` | `"anthropic"`) is set automatically by `update_cloud_config()` and controls which client is instantiated by `get_llm_client()`.
- **Apple Silicon optimized**: Targets M1/M2/M3/M4 with MPS acceleration for embeddings
- **No tests yet**: Test infrastructure (pytest, vitest) is not set up yet
- **TypeScript strict mode**: `strict: true`, `noUnusedLocals`, `noUnusedParameters` enabled
- **Build chunking**: Vite manual chunks split vendor-react, vendor-graph, vendor-ui
