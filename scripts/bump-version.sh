#!/usr/bin/env bash
# bump-version.sh — 一键同步所有版本文件到指定版本号
#
# 用法: ./scripts/bump-version.sh 0.52.1
#
# 管理的文件清单（新增版本文件时请同步更新此列表）:
#   1.  backend/pyproject.toml              — version = "X.Y.Z"
#   1b. backend/src/infra/version.py        — BACKEND_VERSION = "X.Y.Z" (sidecar 内置常量)
#   2.  frontend/package.json               — "version": "X.Y.Z"
#   3.  frontend/src-tauri/tauri.conf.json  — "version": "X.Y.Z"
#   4.  frontend/src-tauri/Cargo.toml       — version = "X.Y.Z" ([package] section)
#   4b. frontend/src-tauri/Cargo.lock       — [[package]] name="ai-reader" 的 version
#   5.  backend/uv.lock                     — [[package]] name="ai-reader-v2-backend" 的
#                                             version，**PEP 440 形式** (X.Y.Z-beta.N → X.Y.ZbN)
#   6.  frontend/package-lock.json          — 两处 "version"（与 package.json 同步）
#   7.  README.md                           — 顶部 badge（值里 '-' 转义成 '--'）+ download links
#                                             ⚠️ 只改 badge 与 releases/download 行，
#                                            **绝不动 changelog 历史行**

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

if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$'; then
  echo "❌ 版本号格式错误: $VERSION"
  echo "   要求格式: X.Y.Z 或 X.Y.Z-beta.N (不带 v 前缀)"
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
  sed -E -i.bak "$@" && rm -f "${@: -1}.bak"
}

# ── Helpers: alternate version spellings ──────────────
# PEP 440 — what uv.lock must contain (Cargo can't be told otherwise):
#   0.78.0-beta.2 -> 0.78.0b2 ; -alpha.N -> aN ; -rc.N -> rcN ; plain X.Y.Z unchanged
_to_pep440() {
  echo "$1" | sed -E 's/-alpha\.?([0-9]+)$/a\1/; s/-beta\.?([0-9]+)$/b\1/; s/-rc\.?([0-9]+)$/rc\1/'
}
# shields.io badge value: a literal '-' in the value is written as '--'
_to_badge() {
  echo "$1" | sed 's/-/--/g'
}

# ── 1. backend/pyproject.toml ─────────────────────────
FILE="$PROJECT_ROOT/backend/pyproject.toml"
_sed_i "s/^version = \"$OLD_VERSION\"/version = \"$VERSION\"/" "$FILE"
echo "  ✅ backend/pyproject.toml"

# ── 1b. backend/src/infra/version.py (sidecar 内置常量) ─
FILE="$PROJECT_ROOT/backend/src/infra/version.py"
_sed_i "s/^BACKEND_VERSION = \"[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*(-[a-zA-Z0-9.]*)?\"/BACKEND_VERSION = \"$VERSION\"/" "$FILE"
echo "  ✅ backend/src/infra/version.py"

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
_sed_i "s/^version = \"[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*(-[a-zA-Z0-9.]*)?\"/version = \"$VERSION\"/" "$FILE"
echo "  ✅ frontend/src-tauri/Cargo.toml"

# ── 4b. frontend/src-tauri/Cargo.lock (own [[package]] only) ─
FILE="$PROJECT_ROOT/frontend/src-tauri/Cargo.lock"
python3 - "$FILE" "$OLD_VERSION" "$VERSION" <<'PY'
import re, sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(path, encoding="utf-8").read()
# Anchor on the crate's own block (name = "ai-reader") so no dependency
# version can ever be touched by this substitution.
pat = re.compile(r'(name = "ai-reader"\nversion = ")' + re.escape(old) + r'(")')
t2, n = pat.subn(lambda m: m.group(1) + new + m.group(2), t)
assert n == 1, f"Cargo.lock: expected 1 ai-reader {old} block, got {n}"
open(path, "w", encoding="utf-8").write(t2)
PY
echo "  ✅ frontend/src-tauri/Cargo.lock"

# ── 5. backend/uv.lock (own [[package]], PEP 440 spelling) ─
FILE="$PROJECT_ROOT/backend/uv.lock"
OLD_PEP440="$(_to_pep440 "$OLD_VERSION")"
NEW_PEP440="$(_to_pep440 "$VERSION")"
python3 - "$FILE" "$OLD_PEP440" "$NEW_PEP440" <<'PY'
import re, sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(path, encoding="utf-8").read()
pat = re.compile(r'(name = "ai-reader-v2-backend"\nversion = ")' + re.escape(old) + r'(")')
t2, n = pat.subn(lambda m: m.group(1) + new + m.group(2), t)
assert n == 1, f"uv.lock: expected 1 ai-reader-v2-backend {old} block, got {n}"
open(path, "w", encoding="utf-8").write(t2)
PY
echo "  ✅ backend/uv.lock (PEP 440: $OLD_PEP440 → $NEW_PEP440)"

# ── 6. README.md badge + download links ──────────────
FILE="$PROJECT_ROOT/README.md"
# The badge value is shields.io-escaped: a literal '-' is written '--', so the
# raw version NEVER appears in the badge text (0.78.0-beta.2 -> 0.78.0--beta.2).
# Match the escaped form, or the badge silently keeps the old version while the
# rest of the repo moves on.
OLD_BADGE="$(_to_badge "$OLD_VERSION")"
NEW_BADGE="$(_to_badge "$VERSION")"
_sed_i "s/version-$OLD_BADGE-blue/version-$NEW_BADGE-blue/g" "$FILE"
# Download links: ONLY lines containing releases/download/ — cannot reach the
# changelog rows (those carry "vX.Y.Z" with no download path).
_sed_i "/releases\/download\//s/$OLD_VERSION/$VERSION/g" "$FILE"
echo "  ✅ README.md (badge + download links; changelog untouched)"

# ── 7. frontend/package-lock.json (deterministic, offline) ─
# Patch the two "version" fields directly instead of shelling out to npm.
# `npm install --package-lock-only` may still reach the registry, and a non-zero
# exit there would abort the bump under `set -e` — after the canonical files had
# already been rewritten. A version-only bump needs no npm; the two mirrored
# fields are all that ever change.
FILE="$PROJECT_ROOT/frontend/package-lock.json"
python3 - "$FILE" "$OLD_VERSION" "$VERSION" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(path, encoding="utf-8").read()
needle, repl = '"' + old + '"', '"' + new + '"'
n = t.count(needle)
assert n >= 1, f"package-lock.json: no occurrence of {old!r}"
open(path, "w", encoding="utf-8").write(t.replace(needle, repl))
print(f"      (patched {n} occurrence(s))")
PY
echo "  ✅ frontend/package-lock.json"

# ── Post-execution verification ───────────────────────
echo ""
echo "🔍 校验版本号..."
ERRORS=0

# Needle is passed explicitly: lock files carry alternate spellings (PEP 440,
# shields.io escaping) that a plain "$VERSION" grep would never match.
check_contains() {
  local file="$1"; local label="$2"; local needle="$3"
  if ! grep -qF -- "$needle" "$PROJECT_ROOT/$file"; then
    echo "  ❌ $label ($file) 未包含 $needle"
    ERRORS=$((ERRORS + 1))
  else
    echo "  ✓ $label"
  fi
}

check_contains "backend/pyproject.toml" "pyproject.toml" "$VERSION"
check_contains "backend/src/infra/version.py" "version.py (BACKEND_VERSION)" "$VERSION"
check_contains "frontend/package.json" "package.json" "$VERSION"
check_contains "frontend/src-tauri/tauri.conf.json" "tauri.conf.json" "$VERSION"
check_contains "frontend/src-tauri/Cargo.toml" "Cargo.toml" "$VERSION"
check_contains "frontend/src-tauri/Cargo.lock" "Cargo.lock" "$VERSION"
check_contains "backend/uv.lock" "uv.lock (PEP 440)" "$(_to_pep440 "$VERSION")"
check_contains "frontend/package-lock.json" "package-lock.json" "$VERSION"
check_contains "README.md" "README.md badge" "version-$(_to_badge "$VERSION")-blue"

if [ $ERRORS -gt 0 ]; then
  echo ""
  echo "❌ $ERRORS 个文件版本校验失败！"
  exit 1
fi

# ── Old version residual scan (warning only) ──────────
# Both spellings, and lock files are NO LONGER excluded: every version file is
# now handled, so any hit is a real miss. README is excluded because its
# changelog deliberately keeps past versions as history.
echo ""
echo "🔎 扫描旧版本残留 ($OLD_VERSION / $OLD_PEP440)..."
# git grep keeps this instant and precise: it only looks at TRACKED files, so
# .venv / node_modules / target are never walked (a raw `grep -r` over the tree
# is slow enough on the Baiduyun-synced drive to get killed). README is excluded
# because its changelog deliberately keeps past versions as history.
RESIDUALS=$(git -C "$PROJECT_ROOT" grep -n -F \
  -e "$OLD_VERSION" -e "$OLD_PEP440" \
  -- ':(exclude)README.md' 2>/dev/null || true)

if [ -n "$RESIDUALS" ]; then
  echo "  ⚠️  以下文件仍包含旧版本号 ${OLD_VERSION} (可能需要手动更新):"
  echo "$RESIDUALS" | head -20
else
  echo "  ✓ 无旧版本残留"
fi

echo ""
echo "✅ 版本同步完成: $VERSION"
echo "📝 请手动更新 README.md changelog 条目"
