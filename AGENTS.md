# Repository Guidelines

## Project Structure & Module Organization

- `backend/`: FastAPI sidecar. Core code is in `src/api`, `src/services`, `src/db`, `src/extraction`, `src/models`, and `src/utils`. Tests live in `backend/tests/` plus `backend/test_scene_split.py`.
- `frontend/`: Vite + React + TypeScript app. Key folders are `src/pages`, `src/components`, `src/api`, `src/stores`, `src/lib`, and `src/providers`; static assets are in `public/`.
- `frontend/src-tauri/`: current desktop packaging and Rust shell used by `npm run build:desktop`. Root `src-tauri/` contains alternate Tauri config and bundled binaries.
- `scripts/` and `demo/`: evaluation utilities, visual test outputs, and demo assets.

## Build, Test, and Development Commands

- `cd backend && uv sync`: install Python dependencies.
- `cd backend && uv run uvicorn src.api.main:app --reload`: run the API on `localhost:8000`.
- `cd frontend && npm install`: install Node dependencies.
- `cd frontend && npm run dev`: run Vite on `localhost:5173`; `/api` and `/ws` proxy to the backend.
- `cd frontend && npm run build`: type-check and create `dist/`.
- `cd frontend && npm run lint`: run ESLint.
- `cd frontend && npx vitest run`: run frontend unit tests.
- `cd backend && uv run pytest`: run backend tests.
- `cd frontend && npm run build:desktop`: build the Tauri desktop app.

## Coding Style & Naming Conventions

Python uses 4-space indentation, `snake_case.py` files/functions, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Prefer `async/await` for DB, HTTP, and LLM calls; use direct SQL plus Pydantic models.

TypeScript uses strict compiler settings and the `@/` path alias. Use `PascalCase.tsx` for components, `camelCaseStore.ts` for Zustand stores, `useX.ts` for hooks, and `PascalCase` type names without an `I` prefix.

## Testing Guidelines

Backend tests use `pytest` and `pytest-asyncio`; name files `test_*.py`. Mark sample-corpus regression tests with `@pytest.mark.regression` when they require `EBOOK_SAMPLE_DIR`.

Frontend tests use Vitest and are colocated as `*.test.ts`. Cover web and Tauri-mode path behavior when code branches by platform.

## Commit & Pull Request Guidelines

Use Conventional Commits, usually with Chinese descriptions, for example `fix: 修复地图标签重叠问题` or `chore(deps-dev): Bump vite`. Branch names follow `feat/...`, `fix/...`, `docs/...`, or `refactor/...`.

PRs should include a concise summary, linked issue when applicable, screenshots for UI changes, and the exact validation commands run. Sign commits with DCO using `git commit -s`.

## Security & Configuration Tips

Do not commit API keys, local model credentials, generated databases, or private corpora. Keep LLM/Ollama/cloud settings local, and review `SECURITY.md` before changing auth, storage, or sidecar behavior.
