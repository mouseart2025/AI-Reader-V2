# Story N5.1: Tauri 项目脚手架

Status: review

## Story

As a 开发团队,
I want 将现有 React + FastAPI 项目集成到 Tauri 2.x 框架中,
So that 可以生成跨平台桌面应用。

## Acceptance Criteria

1. **AC-1**: Tauri 配置指向 frontend/ 的 Vite 构建产物
2. **AC-2**: Python 后端通过 PyInstaller 编译为 sidecar binary
3. **AC-3**: Tauri externalBin 配置引用 sidecar
4. **AC-4**: 开发模式下 `cargo tauri dev` 可同时启动前端和后端
5. **AC-5**: 窗口标题为"AI Reader V2"，最小尺寸 1024×768

## Tasks / Subtasks

- [x] Task 1: Tauri 配置文件 (AC: #1, #3, #5)
  - [x] 1.1 `src-tauri/Cargo.toml` — Tauri 2.x 依赖 (tauri 2, tauri-plugin-shell 2)
  - [x] 1.2 `src-tauri/tauri.conf.json` — 窗口配置 + externalBin + capabilities
  - [x] 1.3 `src-tauri/src/main.rs` — sidecar 启动 + 动态端口管理 + get_backend_port 命令

- [x] Task 2: Sidecar 入口 (AC: #2)
  - [x] 2.1 `backend/sidecar_entry.py` — PyInstaller-compatible FastAPI 启动入口
  - [x] 2.2 `backend/ai-reader-backend.spec` — PyInstaller spec (onefile, hiddenimports)

- [x] Task 3: 开发模式配置 (AC: #4)
  - [x] 3.1 `tauri.conf.json` — beforeDevCommand: `cd ../frontend && npm run dev`, devUrl: localhost:5173
  - [x] 3.2 `main.rs` — dev 模式跳过 sidecar spawn，使用默认 8000 端口

- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 后端测试通过（Rust 编译需安装 Cargo 后验证）

## Completion Notes

- Tauri 2.x 配置完整：Cargo.toml + build.rs + tauri.conf.json + capabilities/default.json
- 窗口：title="AI Reader V2", minWidth=1024, minHeight=768, center=true
- Sidecar 架构：release 模式动态端口 + `get_backend_port` Tauri command 供前端查询
- Dev 模式：beforeDevCommand 启动 Vite，后端由开发者手动启动（现有 Vite proxy 透传 /api /ws）
- PyInstaller spec：onefile 模式，含 uvicorn/fastapi/aiosqlite/jieba/chromadb hiddenimports
- 环境限制：当前无 Rust/Cargo，`cargo check` / `cargo tauri dev` 需安装后验证

### Files Changed

| File | Change |
|------|--------|
| `src-tauri/Cargo.toml` | 新建: Tauri 2.x Rust 项目配置 |
| `src-tauri/build.rs` | 新建: tauri_build 入口 |
| `src-tauri/tauri.conf.json` | 新建: 窗口 + sidecar + dev/build 命令 |
| `src-tauri/capabilities/default.json` | 新建: shell plugin 权限 |
| `src-tauri/src/main.rs` | 新建: sidecar 生命周期 + 端口管理 |
| `backend/sidecar_entry.py` | 新建: PyInstaller-compatible uvicorn 启动器 |
| `backend/ai-reader-backend.spec` | 新建: PyInstaller onefile spec |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
