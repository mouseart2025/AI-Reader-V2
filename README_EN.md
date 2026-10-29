# AI Reader V2 — Turn Novels into Explorable Knowledge

[![Tests](https://github.com/mouseart2025/AI-Reader-V2/actions/workflows/test.yml/badge.svg)](https://github.com/mouseart2025/AI-Reader-V2/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/version-0.85.1-blue)](https://github.com/mouseart2025/AI-Reader-V2/releases)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![GitHub Stars](https://img.shields.io/github/stars/mouseart2025/AI-Reader-V2?style=social)](https://github.com/mouseart2025/AI-Reader-V2)
[![Python](https://img.shields.io/badge/python-3.11+-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/node-22+-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![Tauri](https://img.shields.io/badge/tauri-2-FFC131?logo=tauri&logoColor=white)](https://v2.tauri.app/)

> **[Chinese Version / 中文版](./README.md)**

**Upload a novel. Get a character graph, world map, timeline, encyclopedia, and source-grounded Q&A.**

AI Reader V2 does more than chapter summaries: it turns a whole book into structured `ChapterFact` records — characters, relationships, location hierarchies, item flows, organization events, timelines — then builds an interactive knowledge base you can explore, correct, and export. Data stays on your machine by default.

> **Status (2026-10-30):** **v0.85.1 is released**, with macOS (Apple Silicon) and Windows x64 installers available. The project is under active development; verify analysis output against the source text before using it in formal research.

<p align="center">
  <a href="https://ai-reader.cc"><strong>Website</strong></a> ·
  <a href="https://ai-reader.cc/demo/honglou/graph?v=3"><strong>Live Demo</strong></a> ·
  <a href="#download"><strong>Download</strong></a> ·
  <a href="https://github.com/mouseart2025/AI-Reader-V2"><strong>Source</strong></a> ·
  <a href="./ROADMAP.md"><strong>Roadmap</strong></a>
</p>

## Contents

- [Status](#status)
- [What It Is](#what-it-is)
- [Features](#features)
- [Download](#download)
- [Run from Source](#run-from-source)
- [Tech Stack](#tech-stack)
- [Quality & Reproducibility](#quality--reproducibility)
- [Docs](#docs)
- [License](#license)

## Status

| Channel | Version | Notes |
|---------|---------|-------|
| Desktop release | **v0.85.1** | Recommended for most users; macOS / Windows |
| Latest source | **v0.85.1** | Security cleanup (65 -> 0 dependency advisories) + CodeQL |
| Live demo | Honglou / Journey to the West | No install required |

- Source & releases: <https://github.com/mouseart2025/AI-Reader-V2>
- v0.85.1 release: <https://github.com/mouseart2025/AI-Reader-V2/releases/tag/v0.85.1>
- Full history: [CHANGELOG.md](./CHANGELOG.md)

## What It Is

Most "AI reads a novel" tools stop at summaries. AI Reader V2 goes one step further and makes the result **computable**:

- **Structured** — every chapter yields characters, relationships, locations, items, organizations, events, and concepts; entities are aggregated across chapters.
- **Explainable** — relationships carry evidence spans; the map explains why it chose a real geographic view or a layered fantasy view; hierarchy rebuilds have snapshots and audits.
- **Correctable** — merge/split aliases, rename/hide/retype entities. Corrections layer on top of the automatic result without mutating the original facts.
- **Local-first** — SQLite + ChromaDB on your machine; use local Ollama or an OpenAI-compatible cloud API.

## Features

### 🕸️ Character Relationship Graph

Force-directed network with 70+ relation types, colored by six categories (family, intimate, hierarchical, social, hostile, other). Automatic alias merging (Sun Wukong = Monkey King = Pilgrim), path finding, category filters, and edge-weight controls.

<img src="./assets/screenshots/feature-graph.webp" width="720" alt="Character relationship graph generated from a novel" />

### 🗺️ Novel World Map

Automatically builds a multi-layer map: real-world geographic view (Leaflet + GeoNames) or layered fantasy maps (heaven, underworld, underwater, pocket realms). Procedural terrain, rivers, continental shelves, trajectory playback, and rough.js hand-drawn styling.

Since v0.84 the map no longer falls back silently: the API returns `layout_reason` / `geo_coverage`, and the UI offers **Auto / Geographic / Layered** with an explanation. An "underwater" layer in a realistic novel no longer turns the whole book into a fantasy map.

<img src="./assets/screenshots/feature-map.webp" width="720" alt="Novel world map generated from text" />

### ⏳ Multi-Lane Timeline

Aggregates events from six sources (character appearances, item transfers, relationship changes, organization events, original events, scene tone), with noise filtering, emotional-tone badges, chapter folding, and story-line swimlanes.

<img src="./assets/screenshots/feature-timeline.webp" width="720" alt="Multi-character novel timeline" />

### 📖 Novel Encyclopedia

Browse characters, locations, items, organizations, and concepts; inspect the location hierarchy and spatial relationships; jump from a scene index to the original text; view the world structure.

<img src="./assets/screenshots/feature-encyclopedia.webp" width="720" alt="Novel encyclopedia — characters, locations, items, organizations" />

### More

| Capability | What it does |
|------------|--------------|
| 🖥️ Desktop app | Tauri 2 client with a bundled Python sidecar |
| 📚 Bookshelf | Drag-and-drop `.txt`/`.md`, 50+ chapter formats, search, sort, import/export/backup |
| 🔍 Entity pre-scan | jieba segmentation + LLM classification to build an entity dictionary |
| 📖 Smart reading | Entity highlighting, alias resolution, bookmarks, annotations, scene/screenplay panel |
| ⚔️ Faction map | Organization structures and faction relationships |
| 💬 RAG Q&A | Source-grounded retrieval Q&A with streaming answers and citations |
| 📤 Series bible export | Markdown / Word / Excel / PDF, templates, chapter ranges |
| 🤖 Multi-LLM | Local Ollama + 10 cloud OpenAI-compatible providers |
| 🔁 Independent second pass | Optional independent re-read, chapter-level diff, separate cost accounting |
| 💰 Cost & time | Per-chapter tokens, cost, duration, totals, CSV export |
| 🛠️ Manual correction | Alias merge/split, rename/hide/retype, reversible |
| 🧪 Reproducible | 1764 backend / 112 frontend tests, golden fixtures, CI |

## Download

The desktop build bundles the Python backend; install [Ollama](https://ollama.com/) or configure a cloud API.

| Platform | Stable download | Architecture |
|----------|-----------------|--------------|
| macOS | [AI Reader 0.85.1 (dmg)](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.85.1/AI.Reader_0.85.1_aarch64.dmg) | Apple Silicon (M1/M2/M3/M4) |
| Windows | [AI Reader 0.85.1 (exe)](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.85.1/AI.Reader_0.85.1_x64-setup.exe) | x86_64 |

> **Installer note:** these builds are ad-hoc / unsigned (signing secrets have not been migrated to the public repo yet). On macOS, if the app is reported as damaged, run the command below and reopen it. On Windows, SmartScreen may show an unknown publisher — choose **More info → Run anyway**.
>
> Checksums: [SHA256SUMS.txt](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.85.1/SHA256SUMS.txt).

<details>
<summary>macOS says the app is “damaged”?</summary>

```bash
xattr -cr "/Applications/AI Reader.app"
```

Then reopen it.
</details>

More versions and pre-releases: [Releases](https://github.com/mouseart2025/AI-Reader-V2/releases).

## Run from Source

**Requirements:** Python 3.11+, Node.js 22+, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com/) (or a cloud API).

```bash
git clone https://github.com/mouseart2025/AI-Reader-V2.git
cd AI-Reader-V2

# Backend (use --all-extras for semantic-search dependencies)
cd backend
uv sync --all-extras
uv run uvicorn src.api.main:app --reload

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>, upload a TXT/Markdown novel, and start analysis.

- **Local model:** `ollama pull qwen3:8b && ollama serve`, then select Ollama in Settings.
- **Cloud model:** set API key / base URL / model in Settings (DeepSeek, MiniMax, Claude, OpenAI, Gemini, etc.).
- **No setup:** try the [live demo](https://ai-reader.cc/demo/honglou/graph?v=3).

See [CONTRIBUTING.md](./CONTRIBUTING.md) for desktop builds, tests, and development conventions.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 19 + TypeScript 5.9 + Vite 8 + Tailwind CSS 4 + shadcn/ui |
| Desktop | Tauri 2 (Rust) + Python sidecar (PyInstaller) |
| Visualization | react-force-graph-2d (graph) / Leaflet (geographic) / D3 + SVG (map, timeline) |
| State | Zustand 5 |
| Backend | Python + FastAPI (async) + aiosqlite |
| Data | SQLite (structured) + ChromaDB (vector search) |
| LLM | Ollama (local) or OpenAI-compatible cloud APIs (10 providers) |
| Chinese NLP | jieba + entity pre-scan |

## Quality & Reproducibility

- **Tests:** 1764 backend, 112 frontend; CI runs ruff, eslint, pytest, vitest, build, and repo-boundary checks.
- **Golden data:** location hierarchies and character review fixtures for Journey to the West, Dream of the Red Chamber, Water Margin, Romance of the Three Kingdoms, and Investiture of the Gods.
- **Map audit:** `backend/scripts/map_quality_audit.py` reports layout mode, fallback reason, and geo coverage per novel.
- **Quality loop:** `backend/scripts/quality_loop.py` aggregates Q0/judge/relation-dimension/smoke artifacts and fails on hard regressions.

```bash
cd backend && uv run pytest tests/ -q
cd frontend && npx vitest run && npm run build
```

See [CLAUDE.md](./CLAUDE.md) and [ROADMAP.md](./ROADMAP.md) for details.

## Docs

- [Contributing](./CONTRIBUTING.md)
- [Architecture](./CLAUDE.md)
- [Roadmap](./ROADMAP.md)
- [Changelog](./CHANGELOG.md)
- [Security](./SECURITY.md)
- [Commercial license](./LICENSE-COMMERCIAL.md)
- [Third-party notices](./NOTICE)
- [Development story (Chinese)](https://zhuanlan.zhihu.com/p/2016598051163218226)

## License

[GNU Affero General Public License v3.0](./LICENSE) (AGPL-3.0).

Free for personal, educational, and research use. For closed-source commercial deployment, see the [commercial license](./LICENSE-COMMERCIAL.md). The source is public — forks and PRs are welcome.

---

**Keywords:** novel analysis / literary analysis / AI reading / knowledge graph / character relationships / novel world map / timeline visualization / NLP / LLM / Ollama / Chinese novels / RAG / local-first
