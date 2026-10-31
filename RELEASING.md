# 发版流程（Release Runbook）

> 适用版本：v0.85.1 起。最近一次实走：2026-10-30 的 v0.85.1。
> 目标：在**没有自动发版**的现状下，用 30–40 分钟手工走完一次可复现的发布。

## 0. 先读这一节：为什么是手工流程

仓库里有两个 workflow 处于 **`disabled_manually`** 状态，**不会**因为推 tag 而运行：

| Workflow | 原设计 | 现状 |
|---|---|---|
| `Release` | 推 `v*` tag → 建 draft release → 构建 macOS/Windows → 上传资产 → 取消 draft | **禁用**，在本仓库从未运行过（0 次） |
| `Mirror Release Downloads to R2` | 把某 tag 的安装包镜像到 Cloudflare R2，官网 `/dl/<tag>/<file>` 302 到 R2 | **禁用**，且仓库无 `CLOUDFLARE_R2_API_TOKEN` 一类 secret |

因此**推 tag 只会创建一个指向该 commit 的 tag，不会出包**。真实路径是：

```
版本同步 → 测试 → dispatch 两个构建 → 收 artifact → 组装 release → 冒烟
```

签名现状：仓库没有 Apple Developer ID / Windows 代码签名证书，构建产物是 **ad-hoc / 未签名**。
macOS 首次打开会被 Gatekeeper 拦（README 给了绕过命令），Windows 会弹 SmartScreen。

若将来要把 `Release` 恢复成 tag 自动发版，前置条件是：补上签名证书与 R2 凭据，并确认 `build-desktop.yml` 的 `upload: release` 分支可用。

## 1. 版本同步

```bash
cd AI-Reader-V2
./scripts/bump-version.sh 0.85.1       # 不带 v 前缀
```

脚本会改 8 处并自检（任一处不匹配会直接报错退出）：

| 文件 | 字段 |
|---|---|
| `backend/pyproject.toml` | `version` |
| `backend/src/infra/version.py` | `BACKEND_VERSION`（sidecar 握手用） |
| `frontend/package.json` | `version` |
| `frontend/src-tauri/tauri.conf.json` | `version` |
| `frontend/src-tauri/Cargo.toml` + `Cargo.lock` | 只改本 crate 的 `[[package]] ai-reader` |
| `backend/uv.lock` | 本包版本（PEP 440 拼写） |
| `frontend/package-lock.json` | 两处 `version` |
| `README.md` / `README_EN.md` | 徽章 + `releases/download/` 行 |

> 脚本**不会**改 CHANGELOG、ROADMAP、`ROADMAP` 里提到的状态行，这些要手工补。

## 2. 变更记录

- `CHANGELOG.md`：在 `## Unreleased` 下写条目；发版时把它挪成「版本记录」表的一行（含日期、要点、测试数）。
- `ROADMAP.md`：若本版改变了方向或版本号顺序，同步「近期 / 中期」。
- `README.md`：状态行与下载区（`bump-version.sh` 只改链接，不改文案）。

## 3. 测试门禁（全绿才能继续）

```bash
cd backend
UV_CACHE_DIR=/tmp/uvcache uv run pytest tests/ -q      # 期望 1764 passed
UV_CACHE_DIR=/tmp/uvcache uv run ruff check            # 期望 0 告警

cd ../frontend
npx tsc -b && npx vitest run && npm run build          # 112 tests + 构建
```

## 4. 提交与推送

```bash
git add -A && git commit -m "release: v0.85.1"
git push origin main
```

推完等 `Tests` / `Repo Hygiene` / `CodeQL` 三个 workflow 变绿（`gh run list --branch main`）。**测试没绿不要打 tag。**

## 5. 触发两端构建

```bash
gh workflow run build-macos.yml   --ref main
gh workflow run build-windows.yml --ref main

gh run list --workflow "Build macOS"   --limit 1
gh run list --workflow "Build Windows" --limit 1
```

- macOS ≈ 12–18 分钟（Rust 编译最久），Windows ≈ 15–20 分钟。
- 两个 workflow 都用 `upload: artifact`（不是 `release`），因此产物落在 **workflow artifact**，不在 GitHub Release 上。
- artifact 名称固定：`ai-reader-v2-aarch64-apple-darwin` / `ai-reader-v2-x86_64-pc-windows-msvc`。
- 名称规则坑：NSIS 产物在 artifact 里叫 **`AI Reader_<ver>_x64-setup.exe`（带空格）**，发布时要重命名成 `AI.Reader_<ver>_x64-setup.exe`。

## 6. 收产物、统一命名、算校验和

```bash
mkdir -p /tmp/dist && cd /tmp/dist
gh run download <macos_run_id>   -D /tmp/dist
gh run download <windows_run_id> -D /tmp/dist

mkdir -p /tmp/rel && cd /tmp/rel
cp /tmp/dist/ai-reader-v2-aarch64-apple-darwin/AI.Reader_<ver>_aarch64.dmg .
cp "/tmp/dist/ai-reader-v2-x86_64-pc-windows-msvc/AI Reader_<ver>_x64-setup.exe" "./AI.Reader_<ver>_x64-setup.exe"

shasum -a 256 AI.Reader_<ver>_aarch64.dmg AI.Reader_<ver>_x64-setup.exe > SHA256SUMS.txt
```

`SHA256SUMS.txt` 格式（两空格分隔，与 v0.85.0/v0.85.1 一致）：

```
<sha256>␣␣AI.Reader_<ver>_aarch64.dmg
<sha256>␣␣AI.Reader_<ver>_x64-setup.exe
```

## 7. 打 tag、建 release

```bash
git tag -a v0.85.1 -m "v0.85.1 — <一句话>"
git push origin v0.85.1

gh release create v0.85.1 \
  --title "AI Reader V2 v0.85.1" \
  --notes-file /tmp/release-notes.md \
  --latest \
  /tmp/rel/AI.Reader_0.85.1_aarch64.dmg \
  "/tmp/rel/AI.Reader_0.85.1_x64-setup.exe" \
  /tmp/rel/SHA256SUMS.txt
```

注意：

- 先推 tag 再 `gh release create` 是合法的（Release 会用已存在的 tag）；反过来也行。
- `--latest` 会把本版设为 Releases 页的 Latest。
- Release notes 建议写：变更摘要、兼容性（Python 下限、是否重装）、安装包是否签名、验证方式。

## 8. 发版冒烟（必做）

```bash
# 1) DMG 完整性
hdiutil verify /tmp/rel/AI.Reader_<ver>_aarch64.dmg

# 2) App 版本号真的是新版（不是旧包改名）
hdiutil attach -nobrowse -readonly /tmp/rel/AI.Reader_<ver>_aarch64.dmg
/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
  "/Volumes/AI Reader/AI Reader.app/Contents/Info.plist"
hdiutil detach "/Volumes/AI Reader"
codesign -dv "/Volumes/AI Reader/AI Reader.app" 2>&1 | head -3   # 期望 Signature=adhoc

# 3) 三个下载 URL 均 200
for f in AI.Reader_<ver>_aarch64.dmg AI.Reader_<ver>_x64-setup.exe SHA256SUMS.txt; do
  curl -sL -o /dev/null -w "%{http_code}  $f\n" --max-time 60 \
    "https://github.com/mouseart2025/AI-Reader-V2/releases/download/v<ver>/$f"
done

# 4) latest 指向本版
gh release view --repo mouseart2025/AI-Reader-V2 --json tagName,isDraft,isPrerelease
```

## 9. 官网落地页（ai-reader-internal，可选但建议）

`ai-reader-internal/landing/` 由 Cloudflare Pages 部署，下载链接是**直连 GitHub release**（不走 R2）。发版后需要同步：

- `index.html` / `en.html` / `zh/index.html`：两个下载链接 + 版本号文案 + 更新摘要；
- `docs/desktop-app.html`：下载表格里的文件名与链接。

`landing/_worker.js` 里的 `/dl/<tag>/<file>` → R2 302 是另一条可选通道；R2 未镜像时该路径不可用，不影响直连 GitHub 的页面。

## 10. 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 推 tag 后什么都没发生 | `Release` workflow 被禁用 | 按本文第 5 步手工 dispatch |
| `gh release create` 报 tag 已存在 | 正常 | 不用 `--target`，直接指向现有 tag |
| Windows 资产名带空格 | NSIS 用 productName 拼接 | 上传前重命名 |
| 下载 404 | 把 `AI.Reader_` 写成了 `AI Reader_`，或版本号不一致 | 以 release 页实际资产名为准 |
| 安装后版本没变 | 用户装了旧 DMG | 用 `CFBundleShortVersionString` 核对，必要时先卸载旧版 |
| 大文件下载超时 | GitHub release 走 objects.githubusercontent.com，国内网络可能慢 | 重试；或走 R2 镜像（需先补 token） |
