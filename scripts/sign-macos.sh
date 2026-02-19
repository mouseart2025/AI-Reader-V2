#!/usr/bin/env bash
# Ad-hoc sign the sidecar binary BEFORE Tauri builds the .app bundle.
#
# Tauri's own signing will re-sign the entire .app, but the sidecar
# must be individually signed first to pass Gatekeeper validation.
#
# Usage:
#   ./scripts/sign-macos.sh                          # ad-hoc sign (dev)
#   ./scripts/sign-macos.sh --identity "Developer ID Application: ..."  # production
#
# Environment variables (for CI):
#   APPLE_SIGNING_IDENTITY  — signing identity string

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BINARIES_DIR="$PROJECT_ROOT/src-tauri/binaries"
ENTITLEMENTS="$PROJECT_ROOT/src-tauri/entitlements.plist"

# ── Parse arguments ─────────────────────────────
IDENTITY="${APPLE_SIGNING_IDENTITY:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --identity)
            IDENTITY="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Default to ad-hoc signing if no identity provided
if [[ -z "$IDENTITY" ]]; then
    IDENTITY="-"
    echo "No signing identity provided — using ad-hoc signing"
fi

echo "=== macOS Sidecar Signing ==="
echo "Identity: $IDENTITY"
echo ""

# ── Sign each sidecar binary ────────────────────
SIGNED=0
for bin in "$BINARIES_DIR"/ai-reader-backend-*; do
    [[ -f "$bin" ]] || continue
    [[ "$(basename "$bin")" == ".gitkeep" ]] && continue

    echo ">> Signing: $(basename "$bin")"

    codesign --force --options runtime \
        --sign "$IDENTITY" \
        --entitlements "$ENTITLEMENTS" \
        --timestamp \
        "$bin"

    # Verify
    codesign --verify --verbose=2 "$bin" 2>&1 || true

    SIGNED=$((SIGNED + 1))
done

if [[ "$SIGNED" -eq 0 ]]; then
    echo "WARNING: No sidecar binaries found in $BINARIES_DIR"
    echo "Run ./scripts/build-sidecar.sh first"
    exit 1
fi

echo ""
echo "=== Signed $SIGNED binaries ==="
echo "Done. Now run 'cargo tauri build' to create the .app bundle."
