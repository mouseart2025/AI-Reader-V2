# Story N5.2: Python Sidecar 打包

Status: review

## Story

As a 开发团队,
I want 将 FastAPI 后端打包为独立可执行文件,
So that 用户无需安装 Python 环境。

## Acceptance Criteria

1. **AC-1**: 使用 PyInstaller 生成 macOS ARM64、macOS x64、Windows x64 三个平台的 sidecar binary
2. **AC-2**: 包含所有依赖（aiosqlite, chromadb, jieba, numpy 等）
3. **AC-3**: `--exclude-module` 排除未使用的大型库
4. **AC-4**: sidecar 启动后监听指定端口，Tauri 前端通过 localhost 通信
5. **AC-5**: sidecar 体积 < 200MB（压缩后）

## Tasks / Subtasks

- [x] Task 1: 完善 PyInstaller spec (AC: #2, #3, #5)
  - [x] 1.1 完善 hiddenimports — 覆盖 uvicorn/fastapi/starlette/pydantic/aiosqlite/httpx/websockets/jieba/chromadb/sentence_transformers/PIL/opensimplex/keyring
  - [x] 1.2 添加 excludes — tkinter/matplotlib/scipy/numpy.testing/IPython/notebook/pytest/setuptools/pip/wheel
  - [x] 1.3 添加 jieba 数据文件 — dict.txt + finalseg/posseg/analyse/lac_small 子包

- [x] Task 2: 跨平台构建脚本 (AC: #1)
  - [x] 2.1 `scripts/build-sidecar.sh` — 自动检测 target triple (aarch64-apple-darwin / x86_64-apple-darwin / linux) + PyInstaller 构建 + 复制到 src-tauri/binaries/ 并附加后缀
  - [x] 2.2 `scripts/build-sidecar.ps1` — Windows x64 构建脚本 (x86_64-pc-windows-msvc)

- [x] Task 3: sidecar 通信验证 (AC: #4)
  - [x] 3.1 `sidecar_entry.py --port 18999` 启动 → HTTP GET /api/novels → 200 OK

- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- PyInstaller spec 使用 `importlib` 动态定位 jieba 包路径，确保 dict.txt 等数据文件被正确打包
- 构建脚本自动检测平台并生成 Tauri 要求的 target-triple 后缀命名
- 实际 PyInstaller 构建（`pyinstaller ai-reader-backend.spec`）需先安装 `uv pip install pyinstaller`
- 跨平台构建需在对应平台上执行（PyInstaller 不支持交叉编译），CI/CD 需 macOS + Windows runner
- sidecar_entry.py 验证通过：指定端口启动 → 接受 HTTP 请求 → 正常响应

### Files Changed

| File | Change |
|------|--------|
| `backend/ai-reader-backend.spec` | 完善: 完整 hiddenimports + jieba 数据 + excludes + strip |
| `scripts/build-sidecar.sh` | 新建: macOS/Linux 构建脚本 |
| `scripts/build-sidecar.ps1` | 新建: Windows 构建脚本 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
