#!/usr/bin/env bash
# bump-version.sh — 一键同步所有版本文件到指定版本号
#
# 用法: ./scripts/bump-version.sh 0.52.1
#
# 管理的文件清单（新增版本文件时请同步更新此列表）:
#   1. backend/pyproject.toml          — version = "X.Y.Z"
#   2. frontend/package.json           — "version": "X.Y.Z"
#   3. frontend/src-tauri/tauri.conf.json — "version": "X.Y.Z"
#   4. frontend/src-tauri/Cargo.toml   — version = "X.Y.Z" ([package] section)
#   5. frontend/package-lock.json      — auto via npm install
#   6. README.md                       — badge + download links (not changelog)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Input validation ──────────────────────────────────
VERSION="${1:-}"

if [ -z "$VERSION" ]; then
  echo "❌ 用法: $0 <VERSION>"
  echo "   示例: $0 0.52.1"
  exit 1
fi

if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "❌ 版本号格式错误: $VERSION"
  echo "   要求格式: X.Y.Z (不带 v 前缀)"
  exit 1
fi

# ── Detect old version from pyproject.toml ────────────
OLD_VERSION=$(python3 -c "
import re
with open('$PROJECT_ROOT/backend/pyproject.toml') as f:
    for line in f:
        m = re.match(r'version\s*=\s*\"(.+?)\"', line)
        if m:
            print(m.group(1))
            break
")

if [ -z "$OLD_VERSION" ]; then
  echo "❌ 无法从 pyproject.toml 读取当前版本"
  exit 1
fi

echo "📦 版本更新: $OLD_VERSION → $VERSION"
echo ""

# ── Helper: cross-platform sed (BSD + GNU) ────────────
_sed_i() {
  sed -i.bak "$@" && rm -f "${@: -1}.bak"
}

# ── 1. backend/pyproject.toml ─────────────────────────
FILE="$PROJECT_ROOT/backend/pyproject.toml"
_sed_i "s/^version = \"$OLD_VERSION\"/version = \"$VERSION\"/" "$FILE"
echo "  ✅ backend/pyproject.toml"

# ── 2. frontend/package.json (first match only) ──────
FILE="$PROJECT_ROOT/frontend/package.json"
# Use python for precise first-match replacement
python3 -c "
import json
with open('$FILE') as f:
    data = json.load(f)
data['version'] = '$VERSION'
with open('$FILE', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
"
echo "  ✅ frontend/package.json"

# ── 3. frontend/src-tauri/tauri.conf.json ─────────────
FILE="$PROJECT_ROOT/frontend/src-tauri/tauri.conf.json"
python3 -c "
import json
with open('$FILE') as f:
    data = json.load(f)
data['version'] = '$VERSION'
with open('$FILE', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
"
echo "  ✅ frontend/src-tauri/tauri.conf.json"

# ── 4. frontend/src-tauri/Cargo.toml ([package] only) ─
FILE="$PROJECT_ROOT/frontend/src-tauri/Cargo.toml"
_sed_i "s/^version = \"[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\"/version = \"$VERSION\"/" "$FILE"
echo "  ✅ frontend/src-tauri/Cargo.toml"

# ── 5. README.md badge + download links ──────────────
FILE="$PROJECT_ROOT/README.md"
# Badge: version-X.Y.Z-blue
_sed_i "s/version-$OLD_VERSION-blue/version-$VERSION-blue/g" "$FILE"
# Download links: only lines containing releases/download/
_sed_i "/releases\/download\//s/$OLD_VERSION/$VERSION/g" "$FILE"
echo "  ✅ README.md (badge + download links)"

# ── 6. frontend/package-lock.json via npm ─────────────
echo ""
echo "🔄 更新 package-lock.json..."
(cd "$PROJECT_ROOT/frontend" && npm install --package-lock-only --silent 2>/dev/null)
echo "  ✅ frontend/package-lock.json"

# ── Post-execution verification ───────────────────────
echo ""
echo "🔍 校验版本号..."
ERRORS=0

check_version() {
  local file="$1"
  local label="$2"
  if ! grep -q "$VERSION" "$PROJECT_ROOT/$file"; then
    echo "  ❌ $label ($file) 未包含 $VERSION"
    ERRORS=$((ERRORS + 1))
  else
    echo "  ✓ $label"
  fi
}

check_version "backend/pyproject.toml" "pyproject.toml"
check_version "frontend/package.json" "package.json"
check_version "frontend/src-tauri/tauri.conf.json" "tauri.conf.json"
check_version "frontend/src-tauri/Cargo.toml" "Cargo.toml"
check_version "frontend/package-lock.json" "package-lock.json"
check_version "README.md" "README.md badge"

if [ $ERRORS -gt 0 ]; then
  echo ""
  echo "❌ $ERRORS 个文件版本校验失败！"
  exit 1
fi

# ── Old version residual scan (warning only) ──────────
echo ""
echo "🔎 扫描旧版本残留 ($OLD_VERSION)..."
RESIDUALS=$(grep -rn "$OLD_VERSION" "$PROJECT_ROOT" \
  --include="*.toml" --include="*.json" --include="*.md" --include="*.yml" --include="*.yaml" \
  --exclude-dir=node_modules --exclude-dir=target --exclude-dir=.git --exclude-dir=dist \
  --exclude-dir=_bmad-output --exclude-dir=marketing --exclude-dir=backend/uv.lock \
  --exclude="uv.lock" --exclude="package-lock.json" --exclude="Cargo.lock" \
  2>/dev/null || true)

if [ -n "$RESIDUALS" ]; then
  echo "  ⚠️  以下文件仍包含旧版本号 ${OLD_VERSION} (可能需要手动更新):"
  echo "$RESIDUALS" | head -20
else
  echo "  ✓ 无旧版本残留"
fi

echo ""
echo "✅ 版本同步完成: $VERSION"
echo "📝 请手动更新 README.md changelog 条目"
