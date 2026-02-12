# CLAUDE.md - AI Reader V2

## Project Overview

AI Reader V2 is a **fully local** Chinese novel analysis platform. Users upload TXT novels, the system splits them into chapters, uses a local LLM (Ollama) to extract structured facts per chapter (ChapterFact), then aggregates those facts into entity profiles, visualizations, and a Q&A system. All data and models run locally — no network dependencies, no telemetry.

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
| LLM | Ollama (local) — default model: qwen3:8b |
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
- Ollama must be running locally on port 11434

## Project Structure

```
AI-Reader-V2/
├── backend/
│   ├── pyproject.toml              # Python deps (uv)
│   └── src/
│       ├── api/
│       │   ├── main.py             # FastAPI app entry, CORS, lifespan
│       │   ├── routes/             # REST endpoints (12 routers)
│       │   └── websocket/          # WS handlers (analysis progress, chat streaming)
│       ├── services/               # Business logic (8 services)
│       ├── extraction/             # LLM fact extraction pipeline
│       │   └── prompts/            # System prompt + few-shot examples
│       ├── db/                     # SQLite + ChromaDB data access
│       ├── models/                 # Pydantic schemas / dataclasses
│       ├── infra/                  # Config, LLM client
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
│       │   ├── visualization/      # Graph, map, timeline components
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

### Analysis Pipeline

`AnalysisService` → per-chapter loop → `ContextSummaryBuilder` (prior chapter summary) → `ChapterFactExtractor` (LLM call) → `FactValidator` → write to DB → WebSocket progress push. Supports pause/resume/cancel. Concurrency controlled by asyncio semaphore (1 concurrent LLM call for single-GPU).

### Two Databases Only

- **SQLite**: novels, chapters, chapter_facts, conversations, messages, user_state, analysis_tasks
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

7 tables: `novels`, `chapters`, `chapter_facts`, `conversations`, `messages`, `user_state`, `analysis_tasks`. See `_bmad-output/architecture.md` section 5.1 for full DDL.

## Important Notes

- **Language**: All UI text, error messages, LLM prompts, and extraction rules are in **Chinese**
- **Privacy**: No external network requests, no telemetry — everything runs locally
- **Apple Silicon optimized**: Targets M1/M2/M3/M4 with MPS acceleration for embeddings
- **No tests yet**: Test infrastructure (pytest, vitest) is not set up yet
- **TypeScript strict mode**: `strict: true`, `noUnusedLocals`, `noUnusedParameters` enabled
- **Build chunking**: Vite manual chunks split vendor-react, vendor-graph, vendor-ui
