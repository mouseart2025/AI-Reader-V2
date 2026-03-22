# AI Reader V2 — AI-Powered Novel Analysis & Visualization

[![Version](https://img.shields.io/badge/version-0.58.0-blue)](https://github.com/mouseart2025/AI-Reader-V2)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![GitHub Stars](https://img.shields.io/github/stars/mouseart2025/AI-Reader-V2?style=social)](https://github.com/mouseart2025/AI-Reader-V2)
[![Python](https://img.shields.io/badge/python-≥3.9-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js](https://img.shields.io/badge/node-≥22-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![React](https://img.shields.io/badge/react-19-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/typescript-5.9-3178c6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Ollama](https://img.shields.io/badge/ollama-supported-FF6B35)](https://ollama.com/)
[![Tauri](https://img.shields.io/badge/tauri-2-FFC131?logo=tauri&logoColor=white)](https://v2.tauri.app/)

> **[Chinese Version / 中文版](./README.md)**

**Open-source AI novel analysis tool** — Upload any TXT/Markdown novel and let AI automatically extract character relationships, location hierarchies, and event timelines, generating interactive knowledge graphs, world maps, timelines, and more. Supports local Ollama and cloud LLMs. 100% local data storage, no telemetry.

<p align="center">
  <a href="https://ai-reader.cc"><strong>Website</strong></a> ·
  <a href="https://ai-reader.cc/demo/honglou/graph?v=3"><strong>Live Demo</strong></a> ·
  <a href="#quick-start"><strong>Quick Start</strong></a> ·
  <a href="#desktop-download"><strong>Desktop Download</strong></a>
</p>

## Key Features

### 🕸️ Character Relationship Knowledge Graph

Force-directed relationship network with 70+ relation types automatically detected, colored by 6 categories (family, intimate, hierarchical, social, hostile, other). Smart entity alias merging, path finding, category filtering, and edge weight adjustment.

<img src="https://ai-reader.cc/assets/feature-graph.png" width="720" alt="Character Relationship Knowledge Graph" />

### 🗺️ Auto-Generated World Map

Fully automated multi-layer interactive world map built from novel text. Supports multiple spatial layers (celestial/underworld/underwater/pocket dimensions), portal connections, procedural terrain (biomes, rivers, roads, continental shelves), character trajectory animation, and rough.js hand-drawn style rendering. **v0.58: Cross-chapter spatial completion + adaptive spatial scale (9 canvas levels) + smart redraw.**

<img src="https://ai-reader.cc/assets/feature-map.png" width="720" alt="Novel World Map - Auto-generated Fiction Map" />

### ⏳ Multi-Lane Timeline / Storyline View

Multi-source event aggregation (character appearances, item transfers, relationship changes, organization events), intelligent noise filtering, emotional tone badges, auto-collapsing chapters. Storyline swimlane view tracks parallel character narratives.

<img src="https://ai-reader.cc/assets/feature-timeline.png" width="720" alt="Novel Timeline - Multi-character Narrative Timeline" />

### 📖 Novel Encyclopedia

Five entity types with categorized browsing (characters, locations, items, organizations, concepts), location hierarchy tree with spatial relationship panel, scene index for source text lookup, world structure overview.

<img src="https://ai-reader.cc/assets/feature-encyclopedia.png" width="720" alt="Novel Encyclopedia - Characters, Locations, Items" />

### More Features

- 🖥️ **Desktop App** — Tauri 2 native desktop client, download and run, fully offline
- 📚 **Bookshelf** — Drag-and-drop upload .txt/.md, smart chapter splitting (50+ formats), search, sort, import/export/backup
- 🔍 **Entity Pre-scan** — jieba Chinese word segmentation + LLM classification for improved extraction quality
- 📖 **Smart Reading** — Entity highlighting (5 color types), alias resolution, bookmarks, scene/screenplay panel
- ⚔️ **Faction Map** — Organization structure and faction relationship network
- 💬 **RAG Q&A** — Retrieval-augmented Q&A based on source text, streaming chat, source attribution
- 📤 **Series Bible Export** — Markdown / Word / Excel / PDF with template selection
- 🤖 **Multi-LLM Support** — Local Ollama (qwen3:8b etc.) + 10 cloud providers (DeepSeek, MiniMax, Claude, OpenAI, Gemini, etc.)
- 📊 **Full Analysis Pipeline** — Entity pre-scan → per-chapter extraction → aggregation → visualization, async execution, pause/resume, auto-retry, token budget auto-scaling

## Use Cases

| Scenario | Description |
|----------|-------------|
| Web Novel / Fiction Analysis | Automatically organize character relationships, location hierarchies, faction distributions |
| Literary Research | Character relationship network analysis, narrative structure visualization |
| Writing Aid | Series bible export, world-building consistency check |
| Fan Fiction Reference | Quickly understand source material's character relationships and world structure |
| Reading Notes | Highlight entities, bookmark, scene index during reading |
| Teaching & Presentation | Visual demonstration of novel structure |

## Desktop Download

No development environment needed — download and run. Built-in Python backend, just install [Ollama](https://ollama.com/) or configure a cloud API.

| Platform | Download | Architecture |
|----------|----------|-------------|
| macOS | [AI Reader_0.58.0_aarch64.dmg](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.58.0/AI.Reader_0.58.0_aarch64.dmg) | Apple Silicon (M1/M2/M3/M4) |
| Windows | [AI Reader_0.58.0_x64-setup.exe](https://github.com/mouseart2025/AI-Reader-V2/releases/download/v0.58.0/AI.Reader_0.58.0_x64-setup.exe) | x86_64 |

> **macOS says "damaged"?** Run in Terminal: `xattr -cr "/Applications/AI Reader.app"`, then reopen.
>
> See [Releases](https://github.com/mouseart2025/AI-Reader-V2/releases) for all versions.

## Quick Start

**Requirements:** Python 3.9+ / Node.js 22+ / [uv](https://docs.astral.sh/uv/) / [Ollama](https://ollama.com/) (or cloud API)

```bash
# 1. Start Ollama (local LLM)
ollama pull qwen3:8b && ollama serve

# 2. Start backend
cd backend && uv sync && uv run uvicorn src.api.main:app --reload

# 3. Start frontend (new terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Upload a TXT novel → Analyze → View visualizations.

> Don't want to set up locally? Try the [Live Demo](https://ai-reader.cc/demo/honglou/graph?v=3) with pre-analyzed classic Chinese novels.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + TypeScript 5.9 + Vite 7 + Tailwind CSS 4 + shadcn/ui |
| Desktop | Tauri 2 (Rust) + Python sidecar (PyInstaller) |
| Visualization | D3.js + SVG (map) / react-force-graph-2d (graph) / react-leaflet (geographic) |
| State | Zustand 5 |
| Backend | Python + FastAPI (async) + aiosqlite |
| Database | SQLite (structured) + ChromaDB (vector search) |
| LLM | Ollama (local) or OpenAI-compatible API (cloud, 10 providers) |
| Chinese NLP | jieba segmentation + entity pre-scanning |

## Documentation

- 📋 [Contributing](./CONTRIBUTING.md) — Development setup, code conventions, PR process
- 🏗️ [Architecture](./CLAUDE.md) — Full architecture design, code conventions, data models
- 💼 [Commercial License](./LICENSE-COMMERCIAL.md) — Commercial usage terms

## License

[GNU Affero General Public License v3.0](./LICENSE) (AGPL-3.0)

Free for personal, educational, and research use. For commercial closed-source deployment, see [Commercial License](./LICENSE-COMMERCIAL.md).

---

**Keywords:** novel analysis tool / fiction analysis / AI reader / knowledge graph generator / character relationship graph / novel world map / timeline visualization / NLP text analysis / LLM application / Ollama / Chinese novel / web novel tool / character relationship mapping / world-building / story analysis / book visualization
