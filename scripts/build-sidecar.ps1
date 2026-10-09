# Build the Python backend sidecar for Tauri (Windows).
#
# Usage:
#   .\scripts\build-sidecar.ps1
#
# Output:
#   src-tauri\binaries\ai-reader-sidecar-x86_64-pc-windows-msvc.exe
#
# Prerequisites:
#   cd backend; uv pip install pyinstaller

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$BinariesDir = Join-Path $ProjectRoot "frontend" "src-tauri" "binaries"

$TargetTriple = "x86_64-pc-windows-msvc"

Write-Host "=== AI Reader V2 — Sidecar Build (Windows) ==="
Write-Host "Target: $TargetTriple"
Write-Host ""

# Build with PyInstaller
Set-Location $BackendDir

# ── Prepare geo resources (ship as Tauri resources, NOT inside the onefile
#    sidecar — onefile would decompress ~30MB to a temp dir on every launch,
#    and the SQLite is only opened for point queries during analysis) ──
$GeoResDir = Join-Path $ProjectRoot "frontend/src-tauri/resources/geo"
$GeoDbGz = Join-Path $GeoResDir "zh_wiki_geo.sqlite.gz"
$GeoDb = Join-Path $GeoResDir "zh_wiki_geo.sqlite"
New-Item -ItemType Directory -Force -Path $GeoResDir | Out-Null
if ((Test-Path $GeoDbGz) -and ((-not (Test-Path $GeoDb)) -or ((Get-Item $GeoDbGz).LastWriteTime -gt (Get-Item $GeoDb).LastWriteTime))) {
    Write-Host ">> Unpacking zh_wiki_geo.sqlite.gz..."
    $in = [System.IO.File]::OpenRead($GeoDbGz)
    $gz = New-Object System.IO.Compression.GzipStream($in, [System.IO.Compression.CompressionMode]::Decompress)
    $out = [System.IO.File]::Create($GeoDb)
    $gz.CopyTo($out)
    $out.Close(); $gz.Close(); $in.Close()
}
$ZhGeonames = Join-Path $BackendDir "data/zh_geonames.tsv"
if (Test-Path $ZhGeonames) {
    Copy-Item $ZhGeonames (Join-Path $GeoResDir "zh_geonames.tsv") -Force
}
if (-not (Test-Path $GeoDb)) {
    Write-Warning "$GeoDb missing — Wikipedia geo layer will be DISABLED in the packaged app. Regenerate with: uv run python scripts/build_zh_wiki_geo.py"
}

Write-Host ">> Running PyInstaller..."
uv run pyinstaller ai-reader-sidecar.spec --noconfirm --clean

$DistBin = Join-Path $BackendDir "dist" "ai-reader-sidecar.exe"

if (-not (Test-Path $DistBin)) {
    Write-Error "PyInstaller output not found at $DistBin"
    exit 1
}

# Copy to Tauri binaries with target triple
if (-not (Test-Path $BinariesDir)) {
    New-Item -ItemType Directory -Path $BinariesDir | Out-Null
}

$Dest = Join-Path $BinariesDir "ai-reader-sidecar-${TargetTriple}.exe"
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
