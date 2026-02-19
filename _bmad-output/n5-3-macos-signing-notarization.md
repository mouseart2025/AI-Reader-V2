# Story N5.3: macOS 签名与公证

Status: review

## Story

As a macOS 用户,
I want 下载的应用通过 Gatekeeper 验证,
So that 我可以正常安装而不需手动允许。

## Acceptance Criteria

1. **AC-1**: Gatekeeper 不弹出"未知开发者"警告
2. **AC-2**: 应用通过 Apple notarytool 公证
3. **AC-3**: sidecar binary 已单独 ad-hoc 签名后再由 Tauri 整体签名
4. **AC-4**: 使用 Apple Developer Program 证书（$99/年）

## Tasks / Subtasks

- [x] Task 1: macOS 签名脚本 (AC: #3)
  - [x] 1.1 `scripts/sign-macos.sh` — codesign --options runtime + entitlements + timestamp，支持 ad-hoc（开发）和证书（生产）

- [x] Task 2: Tauri macOS 打包配置 (AC: #1, #4)
  - [x] 2.1 `src-tauri/tauri.conf.json` — macOS bundle: entitlements, minimumSystemVersion=12.0, DMG 布局
  - [x] 2.2 `src-tauri/entitlements.plist` — network.client/server + files.user-selected + files.downloads

- [x] Task 3: CI/CD 构建流程 (AC: #2)
  - [x] 3.1 `.github/workflows/build-macos.yml` — GitHub Actions: macOS ARM64 + x64 matrix, sidecar build → sign → tauri-action (含 notarization secrets)

- [x] Task 4: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- 签名流程：build-sidecar.sh → sign-macos.sh → cargo tauri build（tauri-action 处理 notarization）
- CI secrets 需要配置：APPLE_CERTIFICATE, APPLE_CERTIFICATE_PASSWORD, APPLE_SIGNING_IDENTITY, APPLE_ID, APPLE_PASSWORD, APPLE_TEAM_ID
- entitlements 开放网络（LLM API + localhost 后端）和文件访问（上传小说 + 导出）
- DMG 布局：app 居左 (180,170) + Applications 快捷方式居右 (480,170)
- 同时配置了 Windows 签名基础（digestAlgorithm=sha256, timestampUrl=digicert）

### Files Changed

| File | Change |
|------|--------|
| `src-tauri/tauri.conf.json` | 扩展: macOS bundle (entitlements + DMG) + Windows 签名基础 |
| `src-tauri/entitlements.plist` | 新建: macOS 沙盒 entitlements |
| `scripts/sign-macos.sh` | 新建: sidecar codesign 脚本 |
| `.github/workflows/build-macos.yml` | 新建: macOS CI 构建 + 签名 + 公证 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
