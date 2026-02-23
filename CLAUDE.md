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
├── PRD.md                          # Product requirements (Chinese, 60KB)
├── _bmad-output/architecture.md    # Architecture decisions document
└── interaction-design/             # Excalidraw UI design specs
```

## Key Architecture Concepts

### ChapterFact — Core Data Model

The system's central concept. Each chapter produces one `ChapterFact` JSON containing: characters, relationships, locations, item_events, org_events, events, new_concepts. Stored as JSON text in `chapter_facts.fact_json`. Entity profiles (PersonProfile, LocationProfile, etc.) are **aggregated on-the-fly** from ChapterFacts — not persisted as separate tables.

### Entity Pre-Scan (Optional, Before Analysis)

`EntityPreScanner` — Phase 1: jieba word segmentation + n-gram frequency stats + dialogue attribution regex + suffix pattern matching + **naming pattern extraction** (regex for "叫作/名叫/绰号" patterns) → candidate list. Phase 2: LLM classifies candidates into entity types with aliases. Output: `entity_dictionary` table. The dictionary is injected into the extraction prompt to improve entity recognition quality.

**Numeric-prefix name recovery**: jieba often misclassifies Chinese nicknames starting with numerals (e.g., "二愣子", "三太子") as verbs. `_scan_word_freq()` includes a POS recovery path that keeps words with `_NUM_PREFIXES` ("一二三四五六七八九十") regardless of POS tag. `_merge_candidates()` detects when both short form ("愣子") and long form ("二愣子") exist, removes the short form and transfers its frequency to the long form. Naming-source entries bypass the top-500 candidate cutoff to ensure explicitly introduced names are always included.

### Analysis Pipeline

`AnalysisService` → per-chapter loop → `ContextSummaryBuilder` (prior chapter summary) → `ChapterFactExtractor` (LLM call, with entity dictionary injection if available) → `FactValidator` → write to DB → WebSocket progress push. Supports pause/resume/cancel. Concurrency controlled by asyncio semaphore (1 concurrent LLM call for single-GPU).

### Token Budget Auto-Scaling

`context_budget.py` (`TokenBudget` dataclass + `compute_budget()` + `get_budget()`) — all LLM budget parameters (chapter truncation length, context summary limits, num_ctx, timeouts) are derived from the model's context window size via linear interpolation: 8K context → conservative "local" values, 128K+ context → generous "cloud" values, intermediate models get proportional values. Replaces the old binary `LLM_PROVIDER == "openai"` budget switching.

**Detection**: At startup and on model/mode switches, `detect_and_update_context_window()` queries Ollama `POST /api/show` → `model_info.*.context_length`. Cloud mode defaults to 131072. Failure falls back to 8192. Result stored in `config.CONTEXT_WINDOW_SIZE`.

**Consumers**: `ChapterFactExtractor` (max_chapter_len, retry_len, segment_enabled, extraction_num_ctx), `ContextSummaryBuilder` (context_max_chars, char/rel/loc/item limits, hierarchy chains, world summary chars), `SceneLLMExtractor` (scene_max_chapter_len), `WorldStructureAgent` (ws_max_tokens, ws_timeout), `LocationHierarchyReviewer` (hierarchy_timeout). Non-budget `LLM_PROVIDER` checks (schema injection, cost tracking, client factory) are preserved unchanged.

### Entity Alias Resolution

`AliasResolver` (`alias_resolver.py`) — builds `alias → canonical_name` mapping using Union-Find to merge overlapping alias groups. Merges BOTH sources: `entity_dictionary.aliases` (pre-scan) and `ChapterFact.characters[].new_aliases` (per-chapter extraction). Canonical name selection uses `_pick_canonical()`: among candidates with frequency >= 50% of max, picks the shortest name (formal Chinese names are typically 2-3 chars, shorter than nicknames). The mapping is consumed by `entity_aggregator`, `visualization_service`, and the entities API.

**Three-tier alias safety filtering**: `_alias_safety_level()` returns 0 (hard-block), 1 (soft-block), or 2 (safe). Level 0: kinship terms (大哥/妈妈), possessive phrases (的), trailing kinship suffixes. Level 1: generic person refs (老人/少年/妖精/那怪), pure titles (堂主/长老), length > 8, collective markers (众/群/们). Level 2: safe to use as Union-Find keys. **Passthrough logic**: when an entity_dictionary name or ChapterFact character name is unsafe, it is NOT registered as a UF node (preventing bridge pollution), but its safe aliases are still unioned among themselves (preserving legitimate groups). This prevents generic terms like "妖精"/"那怪" from bridging unrelated character groups (e.g., merging 孙悟空 with 猪八戒 through shared "妖精" references).

### Fact Validation — Morphological Filtering

`FactValidator` (`fact_validator.py`) — post-LLM validation that filters out incorrectly extracted entities. Location validation uses `_is_generic_location()` with 16 structural rules based on Chinese place name morphology (专名+通名 structure) instead of exhaustive blocklists, including descriptive adjective + generic tail patterns (Rule 16, e.g., "偏僻地方"、"荒凉之地"). Person validation uses `_is_generic_person()` to filter pure titles and generic references. Auto-created parent/region locations use `_infer_type_from_name()` to derive type from Chinese name suffix (e.g., "越国"→"国", "乱星海"→"海") instead of hardcoded "区域". See `_bmad-output/spatial-entity-quality-research.md` for the research basis.

**Dictionary-driven name corrections**: `FactValidator` accepts a `name_corrections` mapping (set by `AnalysisService` at analysis start) that fixes LLM extraction errors where numeric-prefix names are truncated (e.g., "愣子" → "二愣子"). Built from entity dictionary: for each person entity starting with a Chinese numeral, if the short form (without prefix) is not itself a legitimate dictionary entity, a correction rule is created. Applied in `_validate_characters()` before deduplication.

**Alias-based character merge**: After name deduplication, when character A explicitly lists character B as an alias and B exists as a separate character entry, B is merged into A (combining aliases, locations, abilities). This handles cases where the LLM correctly identifies alias relationships but also extracts both names as separate characters (e.g., 韩立 listing "二愣子" as alias while "二愣子" also exists as independent character).

### Context Summary Builder — Coreference Resolution

`ContextSummaryBuilder` (`context_summary_builder.py`) — builds prior-chapter context for LLM extraction. Injects ALL known locations (sorted by mention frequency, not just recent window) with an explicit coreference instruction, enabling the LLM to resolve anaphoric references like "小城" → "青牛镇" instead of extracting them as separate locations. **Always injects entity dictionary and world structure** even for early chapters (chapter 1-2) with no preceding facts — the `build()` method no longer returns early when preceding chapter facts are empty, ensuring pre-scan results are available from the first chapter. Naming-source entities (from explicit "叫作/名叫" patterns) are displayed in a separate emphasized section at the top of the dictionary injection, ahead of frequency-sorted entries, to maximize visibility for small local models.

### Location Parent Voting — Authoritative Hierarchy

`WorldStructureAgent` accumulates parent votes across all chapters for each location. Sources: `ChapterFact.locations[].parent` (+1 per mention), `spatial_relationships[relation_type=="contains"]` (weighted by confidence: high=2, medium=1, low=1), and **chapter primary setting inference** (weight=2 per co-occurrence). The winner for each child is stored in `WorldStructure.location_parents` (a `dict[str, str]`). Cycle detection (DFS) breaks the weakest link. User overrides (`location_parent` type) take precedence.

**Chapter Primary Setting Inference**: Each chapter identifies a "primary setting" — the `role="setting"` location with the highest tier rank (largest geographic scale). Co-occurring orphan locations (no parent, not referenced/boundary, not bigger than the primary setting) receive a parent vote (weight=2) pointing to the primary setting. Across many chapters, these votes accumulate (e.g., "百药园" appearing with "七玄门" in 10 chapters → 20 votes). Fallback for old data without `role`: uses the first non-generic location in the chapter. Applied in both `_apply_heuristic_updates()` (live analysis) and `_rebuild_parent_votes()` (hierarchy rebuild).

**Suffix Rank System** (`_get_suffix_rank()`, `_NAME_SUFFIX_TIER`): Chinese location name suffixes encode geographic scale (界>国>城>谷>洞>殿). `_resolve_parents()` uses suffix rank as the PRIMARY signal for parent-child direction validation, falling back to LLM-classified `location_tiers` only when both names lack recognizable suffixes. This fixes ~35% of parent-child inversions caused by LLM extraction confusion (e.g., 元化国 incorrectly placed under 黄枫谷 → flipped because 国 rank 2 > 谷 rank 3). The same suffix rank is used in contains-relationship direction validation during vote accumulation. `_NAME_SUFFIX_TIER` (101 entries) also serves `_classify_tier()` Layer 1, providing reliable tier classification from name morphology for administrative, fantasy, natural feature, and building suffixes. v0.25.0 added 14 micro-scale suffixes (沟/街/巷/墓/陵/桥/坝/堡/哨/弄/码头/渡口/胡同/居).

**Rebuild stability**: `_rebuild_parent_votes()` injects existing `location_parents` as baseline votes (weight=2) before adding chapter_fact evidence, preventing parent wipeout when chapter_facts are sparse. `consolidate_hierarchy()` runs tier inversion fixes (Step 2b) and noise root rescue (Step 2c) for ALL genres (previously skipped for fantasy/urban). Oscillation damping detects direction flips between input and output parents; reverts flips not justified by clear suffix rank or tier difference.

**Cycle detection**: Three layers of defense against cycles in `location_parents`: (1) `_resolve_parents()` walks each parent chain, breaks cycles at the weakest-voted edge; (2) `consolidate_hierarchy()` Step 0 breaks any pre-existing cycles before processing; (3) `world_structure_store.save()` runs `_break_cycles()` as a safety net before persisting. Frontend `WorldStructureEditor.tsx` tree building also detects and breaks cycles in a copy of the parents dict, preventing orphan nodes from appearing as flat depth-0 items.

**Tiered catch-all & micro-scale filtering** (`_tiered_catchall` in `hierarchy_consolidator.py`): Orphan adoption uses a 3-step cascade: (1) prefix matching (orphan name starts with a known node), (2) **dominant intermediate node matching** — site/building orphans (rank ≥ 5) are adopted by the uber_root's direct child with the most descendants (≥3 required), preventing micro-locations from becoming direct children of 天下, (3) **tier-gated uber_root fallback** — only city-level and above (rank ≤ 4) are adopted by uber_root; site/building orphans with no match remain as independent roots rather than polluting 天下's children. `_classify_tier()` Layer 4 fallback defaults to `site` (not `city`) for truly unclassifiable names, since all recognizable city patterns are caught by earlier layers.

**Two-step hierarchy rebuild**: `POST /rebuild-hierarchy` streams SSE progress events (genre re-detection → vote rebuild → scene transition analysis → LLM review → consolidation) and returns a diff of `old_parent → new_parent` changes without saving. Each change includes `auto_select` (default checked or unchecked based on heuristics: removals default off, name-containment relationships default off, non-location parents default off). `POST /apply-hierarchy-changes` applies user-selected changes and auto-clears `map_user_overrides` for affected locations so they get repositioned by the constraint solver.

Consumers: `visualization_service.get_map_data()` overrides `loc["parent"]` and recalculates levels; `entity_aggregator.aggregate_location()` overrides parent and children; `encyclopedia_service` uses it for hierarchy sort and injects virtual parent nodes (uber-roots like "天下"/"地球" that exist only in `location_parents` values but not in ChapterFact extractions) so the encyclopedia hierarchy tree matches WorldStructureEditor. Virtual entries are marked with `virtual: True`. This replaces the old "first-to-arrive wins" strategy that caused duplicate location placements on the map.

### Relation Normalization and Classification

`relation_utils.py` — shared module consumed by `entity_aggregator` and `visualization_service`. `normalize_relation_type()` maps LLM-generated relation type variants to canonical forms (e.g., "师生"→"师徒", "情侣"→"恋人", "仇人"→"敌对") via exact-match then substring-match. `classify_relation_category()` assigns each normalized type to one of 6 categories: family, intimate, hierarchical, social, hostile, other. Used for PersonCard relation grouping and graph edge coloring.

### Entity Aggregation — Relations

`entity_aggregator.py` — when building `PersonProfile.relations`, relation types are normalized before stage merging. Each `RelationStage` collects multiple `evidences` (deduplicated) instead of keeping only the longest one. Each `RelationChain` gets a `category` assignment. `RelationStage.evidence` (str) is preserved as a Pydantic `computed_field` for backward compatibility.

### Graph Edge Aggregation

`visualization_service.py` — graph edges use `Counter`-based type frequency tracking instead of "latest chapter wins". Each edge outputs `relation_type` (most frequent normalized type) and `all_types` (all types sorted by frequency). Edge colors in the frontend match on exact normalized types with keyword fallback. Hierarchical relations (师徒/主仆/君臣) get a distinct purple color.

### GeoResolver — Real-World Coordinate Matching

`GeoResolver` (`geo_resolver.py`) — matches novel location names to real-world GeoNames coordinates for realistic geographic map layouts. Supports multiple datasets via `GeoDatasetConfig` registry:

- **`cn`**: GeoNames CN.zip — comprehensive Chinese locations (~140K entries), used for historical/wuxia novels
- **`world`**: GeoNames cities5000.zip — global cities with pop > 5000 (~50K entries), used for international novels

**Chinese alternate name index** (`_zh_alias_index`): Pre-built from GeoNames `alternateNamesV2` Chinese entries joined with `cities5000` coordinates. Stored as `backend/data/zh_geonames.tsv` (~26K entries, 1.3MB). Lazy-loaded on first world-dataset resolve. Includes both traditional and simplified Chinese variants (via opencc t2s conversion at build time). Built by `scripts/build_zh_geonames.py`.

**Auto-detection pipeline** (`auto_resolve()`): `detect_geo_scope()` determines dataset based on genre_hint + location name CJK ratio → loads appropriate dataset → `detect_geo_type()` uses quality-weighted notable matching → returns `"realistic"` (≥20%), `"mixed"` (≥5%), or `"fantasy"` (<5%). **Fallback logic**: if CN dataset matches poorly (e.g., translated foreign place names like 伦敦/巴黎), automatically retries with world dataset. Accepts optional `known_geo_type` parameter to skip detection and only resolve coordinates (used when geo_type is already cached). `_FANTASY_GENRES = {"fantasy", "xianxia"}` — xianxia novels have entirely fictional geography (灵界、修真界) and skip geo resolution entirely.

**Quality-weighted detection** (`_count_notable_matches()`): Only counts matches to places with population ≥ 5000 or county-level+ administrative codes (`_NOTABLE_FEATURE_CODES`: ADM1-3, PPLA-PPLA3, PPLC). Curated supplement entries and zh alias index entries always count as notable. Exact match only (no suffix stripping) for detection. This prevents false positives from tiny villages (pop=0) that share names with common Chinese words — e.g., 红楼梦's 上房/后门/角门/稻香村 all match real villages in GeoNames CN, but none are notable enough to count toward detection.

**Name resolution** uses 4-level matching: curated supplement → Chinese alternate name index (world dataset only, with parent-proximity disambiguation) → exact GeoNames match → Chinese suffix stripping (城/府/州/县/镇/村/山/河/湖 etc.) + disambiguation by population + admin feature codes. Two-pass parent-proximity validation discards suffix-stripped matches >1000km from parent.

**Integration**: `visualization_service.get_map_data()` uses cached `ws.geo_type` when available, skipping re-detection to prevent oscillation when chapter range or hierarchy changes alter the location subset (small subsets can accidentally exceed the 20% realistic threshold). Only runs full detection when `ws.geo_type is None` (first access after analysis). For realistic/mixed novels, still calls `auto_resolve(known_geo_type=...)` to resolve coordinates without re-detecting type. `apply-hierarchy-changes` does NOT reset `geo_type` — geographic nature is a property of the novel, not of its hierarchy structure. Result cached as `layout_mode="geographic"`.

`WorldStructure.geo_type` caches the detection result to avoid redundant computation and chapter-range/hierarchy oscillation. Once set, it persists until the novel is re-analyzed or manually reset.

### Map Layout — Sunflower Seed Distribution

`map_layout_service.py` — for locations exceeding `MAX_SOLVER_LOCATIONS=40`, overflow locations are placed by `_place_remaining()`, `_place_children()`, and `_hierarchy_layout()`. These methods use **sunflower seed distribution** (golden angle ≈137.5° + radius `r = base × (0.3 + 0.7 × √(i/n))`) instead of uniform circular placement (`angle = 2π*i/n, r = constant`), which caused visible ring patterns. The golden angle ensures successive points are maximally spread, while the sqrt-scaled radius fills the circular area organically from center outward.

### GeoMap — Leaflet Real-World Map

`GeoMap.tsx` — React-Leaflet component for geographic layout mode. Renders location markers on a real-world tile map (CartoDB Positron). Features:

- **CircleMarker** with size scaled by mention_count, color by location type
- **Trajectory polylines** showing character travel routes
- **Click-to-navigate**: Geography panel clicks fly to + highlight the location (persistent tooltip)
- **Drag-to-reposition**: Edit mode with crosshair DivIcon marker for manual lat/lng adjustment, saved to `map_user_overrides` (lat/lng columns)
- **Auto fitBounds** to all markers on load

`MapPage.tsx` switches between `GeoMap` (layout_mode="geographic") and `NovelMap` (all other modes).

### Graph Readability — Dense Network Optimization

`GraphPage.tsx` — relationship graph with readability features for complex novels (400+ characters):

- **Edge weight filtering**: `minEdgeWeight` slider with backend-computed `suggested_min_edge_weight` (auto-raises for >500 edges)
- **Smart auto-defaults**: `minChapters` auto-set to 3 for >200 nodes, 2 for >100 nodes
- **Label-inside-circle**: Large nodes render names centered inside (white text + dark stroke) when circle diameter > text width at current zoom; smaller nodes keep below-node labels with background pill
- **Force spacing**: Charge strength and link distance scale with graph density (stronger repulsion for dense graphs)
- **Collision detection**: Label rects tracked per frame, only non-overlapping labels rendered
- **Dashed weak edges**: `linkLineDash` for weight ≤ 1

### Force-Directed Pre-Layout Seeding

`ConstraintSolver._force_directed_seed()` generates a physics-simulated initial population for `differential_evolution`, replacing random initialization. Uses `_hierarchy_layout()` as starting positions, then runs 80 iterations of spring-force simulation (constraint attraction + pairwise repulsion). User-overridden positions are fixed during simulation. Row 0 of the seed population = force-directed result; remaining rows = random (preserving DE diversity). Energy comparison logged: `Force-directed seed energy=X.XX, random sample energy=Y.YY`.

### Location Semantic Role

`LocationFact.role` (optional field: `"setting"` | `"referenced"` | `"boundary"` | `None`) distinguishes narrative function of each location in a chapter. `setting` = character physically present; `referenced` = mentioned in dialogue/narration; `boundary` = directional landmark. Default `None` for backward compatibility. Frontend renders `referenced` locations at 50% opacity and 70% icon scale; `boundary` locations at 60% opacity with dashed border ring.

### Map Conflict Markers

`get_map_data()` calls `_detect_location_conflicts()` (from `conflict_detector.py`) on loaded facts and includes `location_conflicts` array in the API response. Frontend `NovelMap.tsx` builds a `conflictIndex` (location name → descriptions), renders animated red dashed pulse rings on conflicting locations, and shows conflict details in the location popup.

### Terrain Semantic Texture Layer

`terrainHints.ts` (`generateTerrainHints()`) — generates decorative SVG symbols around terrain-type locations to fill empty parchment space with hand-drawn map ambiance. Pure frontend, no backend changes.

**Icon → terrain mapping**: `mountain`→mountain, `water`/`island`→water, `forest`→forest, `desert`→desert, `cave`→cave. All other icons (city, temple, palace, etc.) produce no terrain hints.

**Symbol definitions**: 3 variants per major category (mountain/water/forest), 2 for desert/cave. Includes "cluster" variants: mountain ridge (3 overlapping peaks), tree cluster (3 trees), triple wave (3 parallel wavy lines). Cluster variants appear ~35% of the time.

**Tier-dependent sizing**: Each tier has independent count, spread radius, and base symbol size (continent: 22 symbols, 160px radius, 38px base; building: 3 symbols, 24px radius, 12px base). Size varies ×0.5–1.0 per symbol. Global cap `MAX_HINTS=900` with proportional scale-down.

**Placement**: Deterministic sin-hash pseudo-random, sqrt-distributed polar coordinates (golden-angle-like spread avoiding center clustering). Collision filter skips symbols within 18px of any location center. Canvas boundary clipping at 20px margin.

**Rendering** (in `NovelMap.tsx`): SVG `<symbol>` definitions in `<defs>`, `<use>` elements in existing `#terrain` group (z-order: below regions/territories, above parchment). `pointer-events: none`. Water symbols use `stroke` + `fill="none"`; others use `fill`. Colors: warm earthy tones (light bg) / brighter variants (dark bg). Opacity range ~0.11–0.22.

### Map Location Click-to-Card

Clicking a location on `NovelMap.tsx` directly opens the `EntityCardDrawer` via `onLocationClick` → `openEntityCard(name, "location")`, without showing a popup tooltip. Each location `<g>` has a transparent hit-area circle (`class="loc-hitarea"`, `fill="transparent"`, `r >= 14px`) as the first child to ensure reliable pointer detection regardless of icon SVG shape/size. The d3-drag behavior uses `.clickDistance(5)` and a `hasDragged` guard so that clicks (< 5px movement) fire the click handler while actual drags (≥ 5px) trigger position save via `onDragEndRef`. Conflict markers retain their own popup click handlers independently.

### Region Label Curved Text

Region labels (`#region-labels` group) render text along SVG `<textPath>` arcs when `regionBoundaries` data is available (from `WorldStructure.layers[].regions` → Voronoi boundaries). Arc paths are defined in `<defs>` as quadratic Bezier curves (`M...Q...`), with bend direction based on vertical position (top-half bends down, bottom-half bends up). `href` is set via both `setAttributeNS(xlink namespace)` and `setAttribute("href")` for browser compatibility. Territory labels (`#territory-labels`) use the same technique for level 0-1 territories.

### Override Constraint Locking

`map_user_overrides` table extended with `constraint_type` (`"position"` default | `"locked"`) and `locked_parent` columns. When `constraint_type='locked'`, the override survives `apply-hierarchy-changes` (not deleted). `locked_parent` overrides voted parent with highest priority in `get_map_data()`. Locked locations display a lock indicator on the map.

### Entity Quality — Single-Character Filtering

Three-layer defense against common single-character nouns extracted as entities (书/饭/茶/龙):

1. **FactValidator** (`fact_validator.py`): `_NAME_MIN_LEN_OTHER = 2` for items/concepts/orgs/locations (persons keep min=1 for valid single-char names like 薛)
2. **entity_aggregator**: Single-char person names kept only if a multi-char person name starting with that character exists (surname cross-reference)
3. **ReadingPage**: Frontend safety net `entities.filter(e => e.name.length >= 2)`

### Scene Panel — Reading Page Integration

Scene/screenplay functionality is integrated into ReadingPage as a right-side panel (`ScenePanel.tsx`), not a standalone page. The toolbar "剧本" toggle button opens/closes the panel. When open: scenes are fetched via `fetchChapterScenes()`, the text switches from whole-block to paragraph-level rendering with colored left borders (`border-l-3 + SCENE_BORDER_COLORS[sceneIdx]`) marking scene boundaries, and the active scene's paragraphs get `bg-accent/30` highlighting. Clicking a SceneCard scrolls the text to the corresponding paragraph. Shared components (`SceneCard`, `SCENE_BORDER_COLORS`, `TONE_STYLES`, `EVENT_TYPE_STYLES`) are exported from `components/shared/ScenePanel.tsx`.

### Two Databases Only

- **SQLite**: novels, chapters, chapter_facts, entity_dictionary, conversations, messages, user_state, analysis_tasks, map_layouts, map_user_overrides, world_structures, layer_layouts, world_structure_overrides (13 tables)
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
LLM_API_KEY           # API key for OpenAI-compatible provider
LLM_BASE_URL          # Base URL (e.g., https://api.deepseek.com/v1)
LLM_MODEL             # Model name (e.g., deepseek-chat)
LLM_MAX_TOKENS        # Default: 8192
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

13 tables: `novels`, `chapters`, `chapter_facts`, `entity_dictionary`, `conversations`, `messages`, `user_state`, `analysis_tasks`, `map_layouts`, `map_user_overrides`, `world_structures`, `layer_layouts`, `world_structure_overrides`. See `_bmad-output/architecture.md` section 5.1 for core DDL.

## Important Notes

- **Language**: All UI text, error messages, LLM prompts, and extraction rules are in **Chinese**
- **Privacy**: Data stays local. Cloud mode only sends LLM requests to the configured API endpoint — no telemetry
- **Dual LLM backend**: `LLM_PROVIDER=ollama` (default, local) or `LLM_PROVIDER=openai` (cloud, any OpenAI-compatible API)
- **Apple Silicon optimized**: Targets M1/M2/M3/M4 with MPS acceleration for embeddings
- **No tests yet**: Test infrastructure (pytest, vitest) is not set up yet
- **TypeScript strict mode**: `strict: true`, `noUnusedLocals`, `noUnusedParameters` enabled
- **Build chunking**: Vite manual chunks split vendor-react, vendor-graph, vendor-ui
