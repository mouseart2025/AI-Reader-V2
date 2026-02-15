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
| Visualization | react-force-graph-2d (graph/factions) |
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
│       ├── services/               # Business logic (13 services)
│       ├── extraction/             # LLM fact extraction + entity pre-scan pipeline
│       │   ├── entity_pre_scanner.py  # jieba stats + LLM classification
│       │   └── prompts/            # System prompt + few-shot examples
│       ├── db/                     # SQLite + ChromaDB data access
│       ├── models/                 # Pydantic schemas / dataclasses
│       ├── infra/                  # Config, LLM clients (Ollama + OpenAI-compatible)
│       └── utils/                  # Text processing, chapter splitting
├── frontend/
│   ├── package.json
│   ├── vite.config.ts              # Vite + Tailwind + proxy config
│   └── src/
│       ├── app/                    # App entry, router, layout
│       ├── pages/                  # 10 page components
│       ├── components/
│       │   ├── ui/                 # shadcn/ui base components
│       │   ├── shared/             # Reusable components (UploadDialog, etc.)
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

`EntityPreScanner` — Phase 1: jieba word segmentation + n-gram frequency stats + dialogue attribution regex + suffix pattern matching → candidate list. Phase 2: LLM classifies candidates into entity types with aliases. Output: `entity_dictionary` table. The dictionary is injected into the extraction prompt to improve entity recognition quality.

### Analysis Pipeline

`AnalysisService` → per-chapter loop → `ContextSummaryBuilder` (prior chapter summary) → `ChapterFactExtractor` (LLM call, with entity dictionary injection if available) → `FactValidator` → write to DB → WebSocket progress push. Supports pause/resume/cancel. Concurrency controlled by asyncio semaphore (1 concurrent LLM call for single-GPU).

### Entity Alias Resolution

`AliasResolver` (`alias_resolver.py`) — builds `alias → canonical_name` mapping using Union-Find to merge overlapping alias groups. Merges BOTH sources: `entity_dictionary.aliases` (pre-scan) and `ChapterFact.characters[].new_aliases` (per-chapter extraction). Canonical name selection uses `_pick_canonical()`: among candidates with frequency >= 50% of max, picks the shortest name (formal Chinese names are typically 2-3 chars, shorter than nicknames). The mapping is consumed by `entity_aggregator`, `visualization_service`, and the entities API.

**Three-tier alias safety filtering**: `_alias_safety_level()` returns 0 (hard-block), 1 (soft-block), or 2 (safe). Level 0: kinship terms (大哥/妈妈), possessive phrases (的), trailing kinship suffixes. Level 1: generic person refs (老人/少年/妖精/那怪), pure titles (堂主/长老), length > 8, collective markers (众/群/们). Level 2: safe to use as Union-Find keys. **Passthrough logic**: when an entity_dictionary name or ChapterFact character name is unsafe, it is NOT registered as a UF node (preventing bridge pollution), but its safe aliases are still unioned among themselves (preserving legitimate groups). This prevents generic terms like "妖精"/"那怪" from bridging unrelated character groups (e.g., merging 孙悟空 with 猪八戒 through shared "妖精" references).

### Fact Validation — Morphological Filtering

`FactValidator` (`fact_validator.py`) — post-LLM validation that filters out incorrectly extracted entities. Location validation uses `_is_generic_location()` with 10 structural rules based on Chinese place name morphology (专名+通名 structure) instead of exhaustive blocklists. Person validation uses `_is_generic_person()` to filter pure titles and generic references. See `_bmad-output/spatial-entity-quality-research.md` for the research basis.

### Context Summary Builder — Coreference Resolution

`ContextSummaryBuilder` (`context_summary_builder.py`) — builds prior-chapter context for LLM extraction. Injects ALL known locations (sorted by mention frequency, not just recent window) with an explicit coreference instruction, enabling the LLM to resolve anaphoric references like "小城" → "青牛镇" instead of extracting them as separate locations.

### Location Parent Voting — Authoritative Hierarchy

`WorldStructureAgent` accumulates parent votes across all chapters for each location. Sources: `ChapterFact.locations[].parent` (+1 per mention) and `spatial_relationships[relation_type=="contains"]` (weighted by confidence: high=3, medium=2, low=1). The winner for each child is stored in `WorldStructure.location_parents` (a `dict[str, str]`). Cycle detection (DFS) breaks the weakest link. User overrides (`location_parent` type) take precedence.

Consumers: `visualization_service.get_map_data()` overrides `loc["parent"]` and recalculates levels; `entity_aggregator.aggregate_location()` overrides parent and children; `encyclopedia_service` uses it for hierarchy sort. This replaces the old "first-to-arrive wins" strategy that caused duplicate location placements on the map.

### Relation Normalization and Classification

`relation_utils.py` — shared module consumed by `entity_aggregator` and `visualization_service`. `normalize_relation_type()` maps LLM-generated relation type variants to canonical forms (e.g., "师生"→"师徒", "情侣"→"恋人", "仇人"→"敌对") via exact-match then substring-match. `classify_relation_category()` assigns each normalized type to one of 6 categories: family, intimate, hierarchical, social, hostile, other. Used for PersonCard relation grouping and graph edge coloring.

### Entity Aggregation — Relations

`entity_aggregator.py` — when building `PersonProfile.relations`, relation types are normalized before stage merging. Each `RelationStage` collects multiple `evidences` (deduplicated) instead of keeping only the longest one. Each `RelationChain` gets a `category` assignment. `RelationStage.evidence` (str) is preserved as a Pydantic `computed_field` for backward compatibility.

### Graph Edge Aggregation

`visualization_service.py` — graph edges use `Counter`-based type frequency tracking instead of "latest chapter wins". Each edge outputs `relation_type` (most frequent normalized type) and `all_types` (all types sorted by frequency). Edge colors in the frontend match on exact normalized types with keyword fallback. Hierarchical relations (师徒/主仆/君臣) get a distinct purple color.

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
