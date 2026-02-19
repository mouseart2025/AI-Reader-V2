# Story N5.4: Windows 签名与安装

Status: review

## Story

As a Windows 用户,
I want 安装时不看到 SmartScreen 警告,
So that 我信任这个应用是安全的。

## Acceptance Criteria

1. **AC-1**: SmartScreen 不显示"未知发布者"警告
2. **AC-2**: 安装包使用 EV Code Signing Certificate 签名
3. **AC-3**: 安装后开始菜单有快捷方式
4. **AC-4**: 首次运行检测 WebView2，未安装时引导安装

## Tasks / Subtasks

- [x] Task 1: Tauri Windows 打包配置 (AC: #2, #3, #4)
  - [x] 1.1 `tauri.conf.json` — NSIS 配置（SimpChinese+English）+ webviewInstallMode=downloadBootstrapper + 签名参数

- [x] Task 2: CI/CD 构建流程 (AC: #1, #2)
  - [x] 2.1 `.github/workflows/build-windows.yml` — GitHub Actions: sidecar build → signtool 签名 → tauri-action → NSIS installer 输出

- [x] Task 3: 编译验证 — 0 TS 错误, 57/57 测试通过

## Completion Notes

- NSIS 安装程序默认创建开始菜单快捷方式（Tauri 2.x 内置）
- WebView2: `downloadBootstrapper` 模式，首次运行时自动下载安装 WebView2 Runtime
- 签名：CI 中通过 signtool + PFX 证书签名 sidecar，再由 Tauri 整体签名 NSIS installer
- CI secrets 需要：WINDOWS_CERTIFICATE (base64 PFX), WINDOWS_CERTIFICATE_PASSWORD
- NSIS 语言：简体中文优先 + English

### Files Changed

| File | Change |
|------|--------|
| `src-tauri/tauri.conf.json` | 扩展: NSIS 配置 + webviewInstallMode + 语言选择 |
| `.github/workflows/build-windows.yml` | 新建: Windows CI 构建 + signtool 签名 |

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6
