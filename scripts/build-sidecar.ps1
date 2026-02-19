# Build the Python backend sidecar for Tauri (Windows).
#
# Usage:
#   .\scripts\build-sidecar.ps1
#
# Output:
#   src-tauri\binaries\ai-reader-backend-x86_64-pc-windows-msvc.exe
#
# Prerequisites:
#   cd backend; uv pip install pyinstaller

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BackendDir = Join-Path $ProjectRoot "backend"
$BinariesDir = Join-Path $ProjectRoot "src-tauri" "binaries"

$TargetTriple = "x86_64-pc-windows-msvc"

Write-Host "=== AI Reader V2 — Sidecar Build (Windows) ==="
Write-Host "Target: $TargetTriple"
Write-Host ""

# Build with PyInstaller
Set-Location $BackendDir
Write-Host ">> Running PyInstaller..."
uv run pyinstaller ai-reader-backend.spec --noconfirm --clean

$DistBin = Join-Path $BackendDir "dist" "ai-reader-backend.exe"

if (-not (Test-Path $DistBin)) {
    Write-Error "PyInstaller output not found at $DistBin"
    exit 1
}

# Copy to Tauri binaries with target triple
if (-not (Test-Path $BinariesDir)) {
    New-Item -ItemType Directory -Path $BinariesDir | Out-Null
}

$Dest = Join-Path $BinariesDir "ai-reader-backend-${TargetTriple}.exe"
Copy-Item $DistBin $Dest -Force

# Report
$SizeMB = [math]::Round((Get-Item $Dest).Length / 1MB, 1)
Write-Host ""
Write-Host "=== Build Complete ==="
Write-Host "Binary: $Dest"
Write-Host "Size:   $SizeMB MB"

if ($SizeMB -gt 200) {
    Write-Warning "Binary exceeds 200 MB target ($SizeMB MB)"
}

Write-Host "Done."
