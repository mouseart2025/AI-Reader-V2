#!/usr/bin/env bash
# Build the Python backend sidecar for Tauri.
#
# Usage:
#   ./scripts/build-sidecar.sh          # auto-detect current platform
#   ./scripts/build-sidecar.sh --target aarch64-apple-darwin
#
# Output:
#   src-tauri/binaries/ai-reader-backend-<target-triple>
#
# Prerequisites:
#   cd backend && uv pip install pyinstaller

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
BINARIES_DIR="$PROJECT_ROOT/src-tauri/binaries"

# ── Detect target triple ────────────────────────
detect_target_triple() {
    local arch
    local os

    arch="$(uname -m)"
    os="$(uname -s)"

    case "$os" in
        Darwin)
            case "$arch" in
                arm64)  echo "aarch64-apple-darwin" ;;
                x86_64) echo "x86_64-apple-darwin" ;;
                *)      echo "unknown-apple-darwin" ;;
            esac
            ;;
        Linux)
            case "$arch" in
                x86_64)  echo "x86_64-unknown-linux-gnu" ;;
                aarch64) echo "aarch64-unknown-linux-gnu" ;;
                *)       echo "unknown-unknown-linux-gnu" ;;
            esac
            ;;
        MINGW*|MSYS*|CYGWIN*)
            echo "x86_64-pc-windows-msvc"
            ;;
        *)
            echo "unknown-unknown-unknown"
            ;;
    esac
}

TARGET_TRIPLE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)
            TARGET_TRIPLE="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [[ -z "$TARGET_TRIPLE" ]]; then
    TARGET_TRIPLE="$(detect_target_triple)"
fi

echo "=== AI Reader V2 — Sidecar Build ==="
echo "Target: $TARGET_TRIPLE"
echo "Backend: $BACKEND_DIR"
echo ""

# ── Build with PyInstaller ──────────────────────
cd "$BACKEND_DIR"

echo ">> Running PyInstaller..."
uv run pyinstaller ai-reader-backend.spec --noconfirm --clean 2>&1

DIST_BIN="$BACKEND_DIR/dist/ai-reader-backend"

if [[ ! -f "$DIST_BIN" ]]; then
    echo "ERROR: PyInstaller output not found at $DIST_BIN"
    exit 1
fi

# ── Copy to Tauri binaries with target triple ───
mkdir -p "$BINARIES_DIR"
DEST="$BINARIES_DIR/ai-reader-backend-${TARGET_TRIPLE}"

cp "$DIST_BIN" "$DEST"
chmod +x "$DEST"

# ── Report ──────────────────────────────────────
SIZE_MB=$(du -m "$DEST" | cut -f1)
echo ""
echo "=== Build Complete ==="
echo "Binary: $DEST"
echo "Size:   ${SIZE_MB} MB"

if [[ "$SIZE_MB" -gt 200 ]]; then
    echo "WARNING: Binary exceeds 200 MB target (${SIZE_MB} MB)"
fi

echo "Done."
