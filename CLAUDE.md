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

**Detection**: At startup and on model/mode switches, `detect_and_update_context_window()` queries Ollama `POST /api/show` → `model_info.*.context_length`. Cloud OpenAI-compatible mode defaults to 131072; Anthropic (`LLM_PROVIDER_FORMAT == "anthropic"`) defaults to 200000 (all Claude 3.x/4.x have 200K context). Failure falls back to 8192. Result stored in `config.CONTEXT_WINDOW_SIZE`. Local Ollama models are capped at `_OLLAMA_CTX_CAP = 16384` to prevent KV cache bloat on consumer hardware (4B models on 8-16GB machines drop from ~10 tok/s to ~2-3 tok/s at 32K context, causing timeouts).

**Consumers**: `ChapterFactExtractor` (max_chapter_len, retry_len, segment_enabled, extraction_num_ctx, **few-shot example count** — only 1 example for context_window ≤ 16K, 2 examples above), `ContextSummaryBuilder` (context_max_chars, char/rel/loc/item limits, hierarchy chains, world summary chars), `SceneLLMExtractor` (scene_max_chapter_len), `WorldStructureAgent` (ws_max_tokens, ws_timeout), `LocationHierarchyReviewer` (hierarchy_timeout). `ChapterFactExtractor._is_cloud` and `SceneLLMExtractor._is_cloud` are set via `isinstance(llm, (OpenAICompatibleClient, AnthropicClient))` — controls JSON schema injection into system prompt and max_tokens budget.

### Entity Alias Resolution

`AliasResolver` (`alias_resolver.py`) — builds `alias → canonical_name` mapping using Union-Find to merge overlapping alias groups. Merges BOTH sources: `entity_dictionary.aliases` (pre-scan) and `ChapterFact.characters[].new_aliases` (per-chapter extraction). Canonical name selection uses `_pick_canonical()`: among candidates with frequency >= 50% of max, picks the shortest name (formal Chinese names are typically 2-3 chars, shorter than nicknames). The mapping is consumed by `entity_aggregator`, `visualization_service`, and the entities API.

**Three-tier alias safety filtering**: `_alias_safety_level()` returns 0 (hard-block), 1 (soft-block), or 2 (safe). Level 0: kinship terms (大哥/妈妈), possessive phrases (的), trailing kinship suffixes. Level 1: generic person refs (老人/少年/妖精/那怪), pure titles (堂主/长老), length > 8, collective markers (众/群/们). Level 2: safe to use as Union-Find keys. **Passthrough logic**: when an entity_dictionary name or ChapterFact character name is unsafe, it is NOT registered as a UF node (preventing bridge pollution), but its safe aliases are still unioned among themselves (preserving legitimate groups). This prevents generic terms like "妖精"/"那怪" from bridging unrelated character groups (e.g., merging 孙悟空 with 猪八戒 through shared "妖精" references).

### Fact Validation — Morphological Filtering

`FactValidator` (`fact_validator.py`) — post-LLM validation that filters out incorrectly extracted entities. Location validation uses `_is_generic_location()` with 18 structural rules based on Chinese place name morphology (专名+通名 structure) instead of exhaustive blocklists, including descriptive adjective + generic tail patterns (Rule 16, e.g., "偏僻地方"、"荒凉之地"), furniture/object exact-match filtering (Rule 17, e.g., "炕桌"、"抽屉"、"火盆"), and character room suffix filtering (Rule 18, e.g., "宝玉屋内"、"贾母房中" — 4+ char names ending with 屋内/房中/室内 etc.). Person validation uses `_is_generic_person()` to filter pure titles and generic references. Auto-created parent/region locations use `_infer_type_from_name()` to derive type from Chinese name suffix (e.g., "越国"→"国", "乱星海"→"海") instead of hardcoded "区域". See `_bmad-output/spatial-entity-quality-research.md` for the research basis.

**Dictionary-driven name corrections**: `FactValidator` accepts a `name_corrections` mapping (set by `AnalysisService` at analysis start) that fixes LLM extraction errors where numeric-prefix names are truncated (e.g., "愣子" → "二愣子"). Built from entity dictionary: for each person entity starting with a Chinese numeral, if the short form (without prefix) is not itself a legitimate dictionary entity, a correction rule is created. Applied in `_validate_characters()` before deduplication.

**Alias-based character merge**: After name deduplication, when character A explicitly lists character B as an alias and B exists as a separate character entry, B is merged into A (combining aliases, locations, abilities). This handles cases where the LLM correctly identifies alias relationships but also extracts both names as separate characters (e.g., 韩立 listing "二愣子" as alias while "二愣子" also exists as independent character).

**Homonym location disambiguation** (N29.3): `_disambiguate_homonym_locations()` renames generic architectural names (夹道, 后门, 角门, etc.) by prepending the parent location with a middle-dot separator: `"夹道"` → `"大观园·夹道"`. Only applies to names in `HOMONYM_PRONE_NAMES` (shared set in `utils/location_names.py`, also used by `conflict_detector.py`) that have a non-empty parent. Runs as the final post-processing step in `validate()`, after all other validation is complete and parent fields are finalized. Also syncs the rename across `characters[].locations_in_chapter`, `events[].location`, `spatial_relationships[].source/target`, and `locations[].parent`. Only affects new analyses; existing data is not retroactively modified. Frontend substring search (`.includes()`) naturally matches original names within disambiguated names (searching "夹道" finds "大观园·夹道").

### Context Summary Builder — Coreference Resolution

`ContextSummaryBuilder` (`context_summary_builder.py`) — builds prior-chapter context for LLM extraction. Injects ALL known locations (sorted by mention frequency, not just recent window) with an explicit coreference instruction, enabling the LLM to resolve anaphoric references like "小城" → "青牛镇" instead of extracting them as separate locations. **Always injects entity dictionary and world structure** even for early chapters (chapter 1-2) with no preceding facts — the `build()` method no longer returns early when preceding chapter facts are empty, ensuring pre-scan results are available from the first chapter. Naming-source entities (from explicit "叫作/名叫" patterns) are displayed in a separate emphasized section at the top of the dictionary injection, ahead of frequency-sorted entries, to maximize visibility for small local models.

**Macro hub anchoring** (N32): `_build_macro_hub_section()` injects a top-down view of major geographic areas into the LLM prompt, solving the "都中隐身" problem where the LLM doesn't know about macro regions and fails to assign correct intermediate parents. Identifies the uber-root, collects its direct children with ≥3 descendants, displays top 8 hubs with tier info and up to 5 sub-children. Inserted between scene focus and hierarchy chains. `build()` accepts an optional `location_tiers` parameter for hub display. Callers in `AnalysisService` (3 call sites: main loop, auto-retry, manual retry) pass `location_tiers` from `WorldStructureAgent.structure`.

### Location Parent Voting — Authoritative Hierarchy

`WorldStructureAgent` accumulates parent votes across all chapters for each location. Sources: `ChapterFact.locations[].parent` (+1 per mention), `spatial_relationships[relation_type=="contains"]` (weighted by confidence: high=2, medium=1, low=1), and **chapter primary setting inference** (weight=2 per co-occurrence). The winner for each child is stored in `WorldStructure.location_parents` (a `dict[str, str]`). Cycle detection (DFS) breaks the weakest link. User overrides (`location_parent` type) take precedence.

**Chapter Primary Setting Inference**: Each chapter identifies a "primary setting" — the `role="setting"` location with the highest tier rank (largest geographic scale). Co-occurring orphan locations (no parent, not referenced/boundary, not bigger than the primary setting) receive a parent vote (weight=2) pointing to the primary setting. Across many chapters, these votes accumulate (e.g., "百药园" appearing with "七玄门" in 10 chapters → 20 votes). Fallback for old data without `role`: uses the first non-generic location in the chapter. Applied in both `_apply_heuristic_updates()` (live analysis) and `_rebuild_parent_votes()` (hierarchy rebuild).

**Suffix Rank System** (`_get_suffix_rank()`, `_NAME_SUFFIX_TIER`): Chinese location name suffixes encode geographic scale (界>国>城>谷>洞>殿). `_resolve_parents()` uses suffix rank as the PRIMARY signal for parent-child direction validation, falling back to LLM-classified `location_tiers` only when both names lack recognizable suffixes. This fixes ~35% of parent-child inversions caused by LLM extraction confusion (e.g., 元化国 incorrectly placed under 黄枫谷 → flipped because 国 rank 2 > 谷 rank 3). The same suffix rank is used in contains-relationship direction validation during vote accumulation. `_NAME_SUFFIX_TIER` (101 entries) also serves `_classify_tier()` Layer 1, providing reliable tier classification from name morphology for administrative, fantasy, natural feature, and building suffixes. v0.25.0 added 14 micro-scale suffixes (沟/街/巷/墓/陵/桥/坝/堡/哨/弄/码头/渡口/胡同/居).

**Rebuild stability**: `_rebuild_parent_votes()` injects existing `location_parents` as baseline votes (weight=2) before adding chapter_fact evidence, preventing parent wipeout when chapter_facts are sparse. `consolidate_hierarchy()` runs tier inversion fixes (Step 2b) and noise root rescue (Step 2c) for ALL genres (previously skipped for fantasy/urban). Oscillation damping detects direction flips between input and output parents; reverts flips not justified by clear suffix rank or tier difference.

**Micro-location pruning** (N32): `_resolve_parents()` Phase 3 skips sub-locations (matching `_is_sub_location_name()` patterns like 门外/墙下/粪窖边) with total vote count < `_MIN_MICRO_VOTES` (3). These noise locations with 1-2 mentions across the entire novel are excluded from parent resolution, direction validation, and cycle detection. Pruning happens at resolution stage (not collection), preserving vote data integrity. Reduces `_resolve_parents()` workload by ~10-15% for novels like 红楼梦 and prevents micro-locations from becoming direct children of uber-root.

**Sibling clustering** (N32): `_resolve_parents()` bidirectional conflict resolution detects sibling pairs — when A→B and B→A both exist with same suffix rank (or both unknown) and vote ratio < 2:1, they are identified as siblings rather than parent-child. `_find_common_parent(a, b, parent_votes, known_locs)` searches both locations' vote candidates for a shared third-party parent (priority) or highest-voted non-sibling parent (fallback). If found, both locations are assigned to the common parent; if not, falls through to existing suffix rank / alphabetical tiebreak. Example: 宁国府↔荣国府 (same "府" suffix, close votes) → sibling → common parent "都中". **Same-suffix sibling promotion** (N32 P3): a post-direction-validation scan catches single-direction same-suffix pairs (e.g., 宁国府→荣国府 where only one direction exists, not triggering bidirectional detection). Only triggers for notable suffixes (`_SIBLING_CANDIDATE_SUFFIXES`: 府/城/寨/庄/镇/村/国/州). Reuses `_find_common_parent()` — if no common parent found, keeps original assignment.

**Cycle detection**: Three layers of defense against cycles in `location_parents`: (1) `_resolve_parents()` walks each parent chain, breaks cycles at the weakest-voted edge; (2) `consolidate_hierarchy()` Step 0 breaks any pre-existing cycles before processing; (3) `world_structure_store.save()` runs `_break_cycles()` as a safety net before persisting. Frontend `WorldStructureEditor.tsx` tree building also detects and breaks cycles in a copy of the parents dict, preventing orphan nodes from appearing as flat depth-0 items.

**Tiered catch-all & micro-scale filtering** (`_tiered_catchall` in `hierarchy_consolidator.py`): Orphan adoption uses a 3-step cascade: (1) prefix matching (orphan name starts with a known node), (2) **dominant intermediate node matching** — site/building orphans (rank ≥ 5) are adopted by the uber_root's direct child with the most descendants (≥3 required), preventing micro-locations from becoming direct children of 天下, (3) **tier-gated uber_root fallback** — only city-level and above (rank ≤ 4) are adopted by uber_root; site/building orphans with no match remain as independent roots rather than polluting 天下's children. `_classify_tier()` Layer 4 fallback defaults to `site` (not `city`) for truly unclassifiable names, since all recognizable city patterns are caught by earlier layers.

**Subtree partition validation** (N32): `validate_hierarchy()` in `LocationHierarchyReviewer` splits the hierarchy into subtrees rooted at each uber-root direct child via BFS. Subtrees ≥ `_SUBTREE_MIN_SIZE` (5) nodes get independent LLM validation calls; smaller subtrees are batched together. Cloud mode (`isinstance(llm, (OpenAICompatibleClient, AnthropicClient))`) runs subtrees concurrently via `asyncio.gather()`; local mode runs sequentially. Each subtree has independent `_SUBTREE_TIMEOUT` (45s) — one timeout doesn't affect others. `_validate_subtree()` handles per-slice prompt construction and result parsing, limited to `_SUBTREE_MAX_DETAIL` (30) detail lines per slice.

**Macro-skeleton pre-generation** (N32): `MacroSkeletonGenerator` (`macro_skeleton_generator.py`) generates a 2-3 level core geographic skeleton via LLM before scene analysis, providing top-down structural anchoring for the bottom-up per-chapter extraction system. Constructs a prompt with novel title, genre, uber-root children, tier-grouped locations (city-level+), and orphan list. LLM returns `{child, parent, confidence}` tuples filtered against known locations (no hallucinated names). Confidence weights: `high` → 5, `medium` → 3. Injected via `agent.inject_external_votes()`. Timeout 45s, graceful fallback on failure. Also detects **synonym locations** (e.g., 神京/都中 referring to the same city) via `synonyms` array in LLM response; synonym pairs are passed to `consolidate_hierarchy()` Step 0.5 which merges alias into canonical (transfers children, removes alias from tiers).

**Two-step hierarchy rebuild**: `POST /rebuild-hierarchy` streams SSE progress events (genre re-detection → vote rebuild → **macro skeleton** → scene transition analysis → LLM review → LLM validation → consolidation) and returns a diff of `old_parent → new_parent` changes without saving. Each change includes `auto_select` (default checked or unchecked based on heuristics: removals default off, name-containment relationships default off, non-location parents default off). `POST /apply-hierarchy-changes` applies user-selected changes and auto-clears `map_user_overrides` for affected locations so they get repositioned by the constraint solver. The LLM review step is wrapped with `asyncio.wait_for(timeout=90)` to prevent slow cloud API responses from blocking the SSE stream indefinitely.

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

`get_map_data()` calls `_detect_location_conflicts()` (from `conflict_detector.py`) on loaded facts and includes `location_conflicts` array in the API response. Frontend `NovelMap.tsx` builds a `conflictIndex` (location name → descriptions), renders animated red dashed pulse rings on conflicting locations, and shows conflict details in the location popup. Conflict display is toggled via `showConflicts` state in `MapPage.tsx` (default off). `conflict_detector.py` filters out homonym-prone location names (`is_homonym_prone()` from shared `utils/location_names.py`) and requires minority parent to appear in ≥2 chapters (`_MIN_MINORITY_CHAPTERS`), reducing false-positive conflicts by ~85%.

### Map Readability — Dense Location Optimization (N29)

`MapPage.tsx` implements a two-stage filtering pipeline for maps with many locations (e.g., 红楼梦 760 locations):

- **Mention count filter** (N29.1): `minMentions` slider filters locations by `mention_count`. Backend provides `max_mention_count` and `suggested_min_mentions` (3 for >300 locations, 2 for >150, 1 otherwise). 150ms debounce prevents excessive re-renders. Effect: 760→107 at threshold 3.
- **Tier collapse/expand** (N29.2): `COLLAPSED_TIERS = {site, building}` hides lower-tier locations within their parent nodes by default. `expandedNodes` Set tracks which parents have been expanded. `collapsedChildCount` Map provides badge counts. `NovelMap.tsx` renders blue "+N" badges on parent nodes; double-click toggles expand/collapse. Expand All / Collapse All buttons in controls. Effect: 107→19 visible + 88 collapsed.
- **Combined pipeline**: `useMemo` chains mention filter → tier collapse before passing `filteredLocations` + `filteredLayout` to NovelMap/GeoMap.

### Terrain Semantic Texture Layer

`terrainHints.ts` (`generateTerrainHints()`) — generates decorative SVG symbols around terrain-type locations to fill empty parchment space with hand-drawn map ambiance. Pure frontend, no backend changes.

**Icon → terrain mapping**: `mountain`→mountain, `water`/`island`→water, `forest`→forest, `desert`→desert, `cave`→cave. All other icons (city, temple, palace, etc.) produce no terrain hints.

**Symbol definitions**: 3 variants per major category (mountain/water/forest), 2 for desert/cave. Includes "cluster" variants: mountain ridge (3 overlapping peaks), tree cluster (3 trees), triple wave (3 parallel wavy lines). Cluster variants appear ~35% of the time.

**Tier-dependent sizing**: Each tier has independent count, spread radius, and base symbol size (continent: 22 symbols, 160px radius, 38px base; building: 3 symbols, 24px radius, 12px base). Size varies ×0.5–1.0 per symbol. Global cap `MAX_HINTS=900` with proportional scale-down.

**Placement**: Deterministic sin-hash pseudo-random, sqrt-distributed polar coordinates (golden-angle-like spread avoiding center clustering). Collision filter skips symbols within 18px of any location center. Canvas boundary clipping at 20px margin.

**Rendering** (in `NovelMap.tsx`): SVG `<symbol>` definitions in `<defs>`, `<use>` elements in existing `#terrain` group (z-order: below regions/territories, above parchment). `pointer-events: none`. Water symbols use `stroke` + `fill="none"`; others use `fill`. Colors: warm earthy tones (light bg) / brighter variants (dark bg). Opacity range ~0.11–0.22.

### Label Multi-Anchor Collision Detection (N30.1)

`computeLabelLayout()` in `NovelMap.tsx` replaces the previous `computeLabelCollisions()` hide-on-collision strategy with a multi-anchor try system. Returns `Map<string, LabelPlacement>` instead of `Set<string>`. For each label (sorted by priority), tries 8 candidate positions: bottom → right → top-right → top → top-left → left → bottom-left → bottom-right. Each anchor has specific `textAnchor` ("middle"/"start"/"end") and offset calculations. Only hides if all 8 positions collide. Grid spatial index (cell size 60px) provides O(1) collision checks. Zoom callback applies `offsetX/k, offsetY/k` to convert screen-space offsets to world-space (counter-scaled labels).

### Simulated Annealing Label Export (N30.2)

`labelAnnealing.ts` — standalone SA optimizer for HD map export. 8 anchor definitions matching NovelMap's candidates. `annealLabels()` async function: greedy warm start → 5000 SA iterations with incremental delta energy computation. Energy: `W_OVERLAP=10` (label-label overlap area), `W_OCCLUSION=5` (label-icon overlap), `W_OFFSET=0.5` (distance from default anchor). Chunked async (500 iterations/chunk) with setTimeout(0) for UI responsiveness. Export pipeline in MapPage: clone SVG → reset transforms → remove counter-scale → show all tiers → extract AnnealItems → run annealing → apply positions → set viewBox 3x → watermark → SVG→PNG download.

### River Network Generation (N30.3)

`generate_rivers()` in `map_layout_service.py` — gradient descent on OpenSimplex elevation field. Water-type locations serve as river sources. `_trace_river()` follows negative elevation gradient with lateral noise perturbation (±15° wiggle), terminating at canvas edge or elevation minimum. River width tapers from source (3-5px) to mouth. Returns `[{points: [[x,y],...], width: float}]`. Frontend renders in `#rivers` SVG group (after terrain, before regions) using `d3Shape.curveBasis` for smooth curves, blue color (#6b9bc3 light / #7eb8d8 dark), opacity 0.6.

### Whittaker Biome Matrix (N30.4)

`_WHITTAKER_GRID` — 5×5 grid (elevation × moisture) of RGB biome colors. `_biome_color_at()` uses bilinear interpolation for smooth transitions between biomes (no hard boundaries). `_elevation_at_img()` and `_moisture_at_img()` compute environmental fields influenced by mountain/water location proximity. `_lloyd_relax()` performs Voronoi centroid iteration with clamped movement (±30px max total displacement) for location seed points. `generate_terrain()` refactored to use Whittaker matrix instead of per-type biome colors, with Lloyd relaxation for more uniform cell shapes.

### Trajectory Animation Enhancement (N30.5)

NovelMap trajectory rendering uses dual-path progressive drawing: background dashed path (full trajectory, opacity 0.2) + foreground solid path (visible slice, opacity 0.85). Waypoint circle radius scales with stay duration: `r = min(4 + stay * 1.5, 12)`. Current playback position shows pulse marker (SVG `<animate>` for radius 10→18→10 and opacity 0.6→0.1→0.6, 1.5s loop). Chapter labels ("Ch.N") at first occurrence of each location. Counter-scale in zoom callback: stroke-width `3/k`, circle radius `baseR/k`, label font-size `9/k`. Auto-pan follows playback when current point is within 20% of viewport edge, using `d3Zoom.translateTo` with 300ms transition. MapPage adds `playSpeed` state (1200/800/400ms) with three-speed toggle buttons (×0.5/×1/×2).

### Rough.js Hand-Drawn Map Style (N31.2)

`NovelMap.tsx` uses `roughjs` for hand-drawn aesthetic rendering of territories, rivers, and coastline. `roughCanvasRef` holds a `RoughSVG` instance initialized during SVG setup. **Territories**: `rc.path()` with hachure fill (roughness 1.2, bowing 1.0, per-territory seed from `hashString(name)`, hachure angle/gap vary by level). Replaces previous `distortPolygonEdges` + dashed stroke. **Rivers**: quadratic Bezier path built manually (`Q` commands between waypoints), rendered via `rc.path()` (roughness 0.8, bowing 2.0, stroke-only). **Coastline** (`coastlineGenerator.ts`): Graham scan convex hull of all locations → radial expansion (8% of canvas min dimension) → multi-octave sinusoidal noise (3 frequencies: 7×, 13×, 23× angle) → 4× subdivision with sub-noise → closed SVG path. Ocean fill uses evenodd compound path (viewport rect + coastline cutout). Coastline border rendered via rough.js (roughness 1.5). **Vignette**: SVG radial gradient (`#vignette`) with transparent center → edge darkening, applied as a `<rect>` outside viewport (zoom-independent). Region and territory labels use `filter="url(#hand-drawn)"` SVG displacement map for subtle tremor.

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

### Analysis Timing & Quality Tracking

`AnalysisService` tracks per-chapter timing and quality metrics during analysis. `_chapter_times` accumulates elapsed_ms per chapter, enabling real-time ETA via WebSocket (`timing.eta_ms = avg_ms * remaining`). `ExtractionMeta` (from `ChapterFactExtractor`) reports `is_truncated` (chapter exceeded budget.max_chapter_len) and `segment_count` (number of segments for long chapters). These are persisted to `chapter_facts` columns and surfaced in the analysis progress UI. On completion, a `timing_summary` JSON (total_ms, avg/min/max_chapter_ms, chapters_processed) is saved to `analysis_tasks.timing_summary`.

**Live timing persistence**: `AnalysisService._live_timing` (dict keyed by novel_id) stores the latest timing snapshot in memory, surviving page navigation. Updated after every chapter (success or failure). The `processing` WS message includes `timing` so the frontend receives timing even before the first `progress` message. REST `GET /novels/{id}/analysis/latest` returns `timing` when the task is running/paused. Frontend `analysisStore._pollTaskStatus()`, `AnalysisPage` mount load, and `visibilitychange` handler all restore `timingStats` from REST. Cleaned up on task completion/cancel; preserved on pause.

**Auto-retry**: After the main loop, failed chapters get one automatic retry attempt. Success broadcasts `"retry_success"` status via WebSocket. `content_policy` chapters are **skipped** during retry (same content will always be rejected by the provider's safety filter).

### Analysis Failure Resilience (N28)

**Error persistence** (`chapters` table): `analysis_error TEXT` and `error_type TEXT` columns store the failure reason per chapter. `_classify_error(exc)` maps exceptions to five types: `timeout` (LLMTimeoutError), `parse_error` (LLMParseError / ExtractionError), `content_policy` (LLMError with safety keywords like "content_filter"/"违规"/"审核"), `http_error` (other LLMError), `unknown`. Frontend `AnalysisPage` shows a colored badge per failed chapter (orange=内容审核, yellow=超时, red=其他) with hover tooltip.

**Task state recovery** (`recover_stale_tasks()`): Called at server startup (`lifespan` in `main.py`). Any task stuck in `"running"` is auto-reset to `"paused"` so the user can resume. Prevents permanently-stuck tasks caused by `uvicorn --reload` killing asyncio tasks mid-execution.

**content_policy skip** (N28.3): `_failed_in_run` entries carry `error_type`; retry loop skips chapters with `error_type == "content_policy"`. Saves time and tokens on content that will always be rejected.

**Post-analysis LLM timeout** (N28.4): `LocationHierarchyReviewer.review()` is wrapped with `asyncio.wait_for(timeout=60.0)`. On `asyncio.TimeoutError`: logs a warning, broadcasts stage message "地点层级优化超时，已跳过", sets `review_votes = None`, and continues non-fatally to task completion.

### Model Benchmark — Quality Evaluation & History

`POST /model-benchmark` runs a fixed extraction prompt against the current LLM and evaluates output quality against a golden standard (`_GOLDEN_STANDARD` in `settings.py`). Quality scoring uses pure string matching (no extra LLM call): `entity_recall` = fraction of golden entities found in output text, `relation_recall` = fraction of golden relations where both endpoints appear. `overall_score = entity_recall * 0.6 + relation_recall * 0.4` (0-100). Results auto-save to `benchmark_records` table. The response includes `benchmark.estimated_chapter_chars` (default 3000) so the frontend can display what the time estimate is based on.

`GET /model-benchmark/history` returns the last 50 records. `DELETE /model-benchmark/history/{id}` removes a record. Frontend `SettingsPage.tsx` shows a collapsible history table with time/model/speed/estimated chapter time/quality score, with color coding (green ≥80, yellow ≥60, red <60).

### Two Databases Only

- **SQLite**: novels, chapters, chapter_facts, entity_dictionary, conversations, messages, user_state, analysis_tasks, map_layouts, map_user_overrides, world_structures, layer_layouts, world_structure_overrides, benchmark_records (14 tables)
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

14 tables: `novels`, `chapters`, `chapter_facts`, `entity_dictionary`, `conversations`, `messages`, `user_state`, `analysis_tasks`, `map_layouts`, `map_user_overrides`, `world_structures`, `layer_layouts`, `world_structure_overrides`, `benchmark_records`. See `_bmad-output/architecture.md` section 5.1 for core DDL.

## Important Notes

- **Language**: All UI text, error messages, LLM prompts, and extraction rules are in **Chinese**
- **Privacy**: Data stays local. Cloud mode only sends LLM requests to the configured API endpoint — no telemetry
- **Dual LLM backend**: `LLM_PROVIDER=ollama` (default, local) or `LLM_PROVIDER=openai` (cloud). Cloud supports 10 providers: DeepSeek, MiniMax, Qwen, Moonshot, Zhipu, SiliconFlow, Yi, OpenAI, Gemini, Anthropic. Anthropic uses a separate `AnthropicClient` (`anthropic_client.py`) with `x-api-key` auth and `/v1/messages` endpoint; all others use `OpenAICompatibleClient`. `LLM_PROVIDER_FORMAT` (`"openai"` | `"anthropic"`) is set automatically by `update_cloud_config()` and controls which client is instantiated by `get_llm_client()`.
- **Apple Silicon optimized**: Targets M1/M2/M3/M4 with MPS acceleration for embeddings
- **No tests yet**: Test infrastructure (pytest, vitest) is not set up yet
- **TypeScript strict mode**: `strict: true`, `noUnusedLocals`, `noUnusedParameters` enabled
- **Build chunking**: Vite manual chunks split vendor-react, vendor-graph, vendor-ui
