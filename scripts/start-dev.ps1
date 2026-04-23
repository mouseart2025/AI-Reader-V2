param(
  [int]$BackendStartPort = 8000,
  [int]$FrontendStartPort = 5173,
  [int]$OllamaPort = 11434,
  [int]$MaxPortAttempts = 50,
  [string]$OllamaModel = "",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$PowerShellExe = "powershell.exe"

function Require-Command {
  param([string]$Name)

  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Missing required command: $Name"
  }
}

function Test-PortAvailable {
  param([int]$Port)

  $listener = $null

  try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    $listener.Start()
    return $true
  }
  catch {
    return $false
  }
  finally {
    if ($listener) {
      $listener.Stop()
    }
  }
}

function Find-FreePort {
  param(
    [int]$StartPort,
    [int]$MaxAttempts = 50,
    [int[]]$ReservedPorts = @(),
    [switch]$AllowEphemeralFallback
  )

  for ($offset = 0; $offset -lt $MaxAttempts; $offset++) {
    $candidate = $StartPort + $offset

    if (($ReservedPorts -notcontains $candidate) -and (Test-PortAvailable -Port $candidate)) {
      return $candidate
    }
  }

  if ($AllowEphemeralFallback) {
    $listener = $null

    try {
      $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
      $listener.Start()
      $candidate = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port

      if ($ReservedPorts -contains $candidate) {
        return Find-FreePort -StartPort ($candidate + 1) -MaxAttempts $MaxAttempts -ReservedPorts $ReservedPorts -AllowEphemeralFallback
      }

      return $candidate
    }
    finally {
      if ($listener) {
        $listener.Stop()
      }
    }
  }

  throw "No free port found from $StartPort after $MaxAttempts attempts."
}

function Test-HttpReady {
  param(
    [string]$Url,
    [int]$TimeoutSec = 2
  )

  try {
    Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec | Out-Null
    return $true
  }
  catch {
    return $false
  }
}

function Wait-HttpReady {
  param(
    [string]$Url,
    [string]$ServiceName,
    [int]$TimeoutSec = 30,
    [int]$PollIntervalMs = 500
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSec)

  while ((Get-Date) -lt $deadline) {
    if (Test-HttpReady -Url $Url -TimeoutSec 2) {
      return
    }

    Start-Sleep -Milliseconds $PollIntervalMs
  }

  throw "Timed out waiting for $ServiceName at $Url"
}

if (-not (Test-Path -LiteralPath $BackendDir)) {
  throw "Backend directory not found: $BackendDir"
}

if (-not (Test-Path -LiteralPath $FrontendDir)) {
  throw "Frontend directory not found: $FrontendDir"
}

Require-Command -Name "uv"
Require-Command -Name "npm"

$RequestedOllamaPort = $OllamaPort
$RequestedOllamaBaseUrl = "http://127.0.0.1:$RequestedOllamaPort"
$RequestedOllamaHealthUrl = "$RequestedOllamaBaseUrl/api/tags"

$ollamaRunning = Test-HttpReady -Url $RequestedOllamaHealthUrl -TimeoutSec 2

if ($ollamaRunning) {
  $OllamaPort = $RequestedOllamaPort
}
elseif (Test-PortAvailable -Port $RequestedOllamaPort) {
  Require-Command -Name "ollama"
  $OllamaPort = $RequestedOllamaPort
}
else {
  Require-Command -Name "ollama"
  $OllamaPort = Find-FreePort -StartPort $RequestedOllamaPort -MaxAttempts $MaxPortAttempts -AllowEphemeralFallback
}

$OllamaBaseUrl = "http://127.0.0.1:$OllamaPort"
$OllamaHealthUrl = "$OllamaBaseUrl/api/tags"

$BackendPort = Find-FreePort -StartPort $BackendStartPort -MaxAttempts $MaxPortAttempts -ReservedPorts @($OllamaPort) -AllowEphemeralFallback
$FrontendPort = Find-FreePort -StartPort $FrontendStartPort -MaxAttempts $MaxPortAttempts -ReservedPorts @($BackendPort, $OllamaPort) -AllowEphemeralFallback

$BackendBaseUrl = "http://127.0.0.1:$BackendPort"
$FrontendBaseUrl = "http://127.0.0.1:$FrontendPort"
$BackendHealthUrl = "$BackendBaseUrl/api/settings/health-check"

$BackendDirEscaped = $BackendDir.Replace("'", "''")
$FrontendDirEscaped = $FrontendDir.Replace("'", "''")
$OllamaBaseUrlEscaped = $OllamaBaseUrl.Replace("'", "''")
$OllamaModelEscaped = $OllamaModel.Replace("'", "''")

$backendCommandParts = @(
  "`$Host.UI.RawUI.WindowTitle = 'AI Reader Backend'"
  "Set-Location -LiteralPath '$BackendDirEscaped'"
  "`$env:OLLAMA_BASE_URL = '$OllamaBaseUrlEscaped'"
)

if ($OllamaModel) {
  $backendCommandParts += "`$env:OLLAMA_MODEL = '$OllamaModelEscaped'"
}

$backendCommandParts += "uv run uvicorn src.api.main:app --reload --host 127.0.0.1 --port $BackendPort"
$backendCommand = "& { " + ($backendCommandParts -join "; ") + " }"

$frontendCommand = "& { " + (@(
    "`$Host.UI.RawUI.WindowTitle = 'AI Reader Frontend'"
    "Set-Location -LiteralPath '$FrontendDirEscaped'"
    "`$env:VITE_API_BASE_URL = '$BackendBaseUrl'"
    "`$env:VITE_WS_BASE_URL = 'ws://127.0.0.1:$BackendPort'"
    "`$env:VITE_DEV_PORT = '$FrontendPort'"
    "npm run dev -- --host 127.0.0.1 --port $FrontendPort"
  ) -join "; ") + " }"

Write-Host "Project root  : $ProjectRoot"
Write-Host "Backend URL   : $BackendBaseUrl"
Write-Host "Frontend URL  : $FrontendBaseUrl"
Write-Host "Ollama URL    : $OllamaBaseUrl"
Write-Host ""

if ($DryRun) {
  if ($ollamaRunning) {
    Write-Host "Ollama        : reuse existing service"
  }
  else {
    if ($OllamaPort -ne $RequestedOllamaPort) {
      Write-Host "Ollama        : requested port $RequestedOllamaPort unavailable, would start on $OllamaPort"
    }
    else {
      Write-Host "Ollama        : would start new service on port $OllamaPort"
    }
  }

  Write-Host "Backend CMD   : $backendCommand"
  Write-Host "Frontend CMD  : $frontendCommand"
  exit 0
}

$ollamaProcess = $null

if ($ollamaRunning) {
  Write-Host "Ollama        : reusing existing service"
}
else {
  $ollamaCommand = "& { `$env:OLLAMA_HOST = '127.0.0.1:$OllamaPort'; ollama serve }"
  $ollamaProcess = Start-Process `
    -FilePath $PowerShellExe `
    -ArgumentList @("-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", $ollamaCommand) `
    -WindowStyle Hidden `
    -PassThru
  Wait-HttpReady -Url $OllamaHealthUrl -ServiceName "Ollama" -TimeoutSec 30
  if ($OllamaPort -ne $RequestedOllamaPort) {
    Write-Host "Ollama        : started PID $($ollamaProcess.Id) on fallback port $OllamaPort"
  }
  else {
    Write-Host "Ollama        : started PID $($ollamaProcess.Id)"
  }
}

$backendProcess = Start-Process `
  -FilePath $PowerShellExe `
  -WorkingDirectory $BackendDir `
  -ArgumentList @("-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $backendCommand) `
  -PassThru

Wait-HttpReady -Url $BackendHealthUrl -ServiceName "backend" -TimeoutSec 45

$frontendProcess = Start-Process `
  -FilePath $PowerShellExe `
  -WorkingDirectory $FrontendDir `
  -ArgumentList @("-NoLogo", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $frontendCommand) `
  -PassThru

Wait-HttpReady -Url $FrontendBaseUrl -ServiceName "frontend" -TimeoutSec 45

Write-Host ""
Write-Host "Ready:"
Write-Host "  UI       $FrontendBaseUrl"
Write-Host "  Backend  $BackendBaseUrl"
Write-Host "  Ollama   $OllamaBaseUrl"
Write-Host ""
Write-Host "PIDs:"
Write-Host "  Backend  $($backendProcess.Id)"
Write-Host "  Frontend $($frontendProcess.Id)"

if ($ollamaProcess) {
  Write-Host "  Ollama   $($ollamaProcess.Id)"
}
