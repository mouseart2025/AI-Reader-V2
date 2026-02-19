# Story N5.5: 自动更新与 CI/CD

Status: review

## Story

As a 用户,
I want 应用启动时自动检测更新,
So that 我始终使用最新版本。

## Acceptance Criteria

1. **AC-1**: 应用启动时检测新版本，显示更新提示（版本号 + 更新说明）
2. **AC-2**: 用户可选"立即更新""稍后提醒""跳过此版本"
3. **AC-3**: 更新包签名验证通过后安装并重启
4. **AC-4**: GitHub Actions 自动构建 macOS ARM64/x64 + Windows x64
5. **AC-5**: 构建产物发布到 GitHub Releases + 更新服务器 JSON manifest

## Tasks / Subtasks

- [x] Task 1: Tauri 自动更新配置 (AC: #1, #3)
  - [x] 1.1 `Cargo.toml` — 添加 tauri-plugin-updater = "2"
  - [x] 1.2 `tauri.conf.json` — updater endpoints (GitHub Releases latest.json) + pubkey 占位
  - [x] 1.3 `capabilities/default.json` — 添加 updater:default 权限
  - [x] 1.4 `main.rs` — 注册 updater 插件 + release 模式异步 check_for_updates()

- [x] Task 2: 统一 CI/CD Release 流程 (AC: #4, #5)
  - [x] 2.1 `.github/workflows/release.yml` — 完整 release pipeline: create draft → 3-platform matrix build (macOS ARM64/x64 + Windows x64) → sign → tauri-action (含 updater signing key) → publish

- [x] Task 3: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- 自动更新：tauri-plugin-updater + GitHub Releases 端点，release 模式启动时异步检查
- 前端 UX 交互（"立即更新/稍后/跳过"按钮）由 Tauri updater JS API 在前端实现（AC-2 将在前端 story 中细化）
- CI/CD：统一 release.yml 替代独立的 build-macos.yml / build-windows.yml
- 发布流程：push tag v* → create draft release → matrix build → tauri-action 上传 + 生成 latest.json → undraft publish
- Secrets 需要额外配置：TAURI_SIGNING_PRIVATE_KEY（updater 签名），TAURI_SIGNING_PRIVATE_KEY_PASSWORD
- updater pubkey 需在 tauri.conf.json 中填写（由 `tauri signer generate` 生成密钥对）

### Files Changed

| File | Change |
|------|--------|
| `src-tauri/Cargo.toml` | 添加 tauri-plugin-updater 依赖 |
| `src-tauri/tauri.conf.json` | 添加 updater 端点配置 |
| `src-tauri/capabilities/default.json` | 添加 updater:default 权限 |
| `src-tauri/src/main.rs` | 注册 updater 插件 + check_for_updates() |
| `.github/workflows/release.yml` | 新建: 统一跨平台 release pipeline |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
