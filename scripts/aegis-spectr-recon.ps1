param(
  [int]$ApiPort = 8801,
  [int]$AppPort = 5173
)

$ErrorActionPreference = "Stop"

# New artifact justification:
# There was no single recon surface for BTTCO dev state, only ad hoc port checks in start-dev.ps1.
# Spectr centralizes health, owner PID, and mode inspection so Sentinel recovery decisions stay deterministic.

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StatePath = Join-Path $RepoRoot ".dev\state\dev-mode.json"

function Get-ModeState {
  if (Test-Path $StatePath) {
    try {
      return Get-Content -Raw $StatePath | ConvertFrom-Json
    } catch {
      return $null
    }
  }
  return $null
}

function Get-PortOwner {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $conn) { return $null }
  try {
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)" -ErrorAction SilentlyContinue
    return [ordered]@{
      pid = $conn.OwningProcess
      processName = $proc.ProcessName
      path = $proc.Path
      commandLine = if ($cim) { $cim.CommandLine } else { $null }
    }
  } catch {
    return [ordered]@{
      pid = $conn.OwningProcess
      processName = "unknown"
      path = $null
      commandLine = $null
    }
  }
}

function Get-AppActualMode {
  param([object]$Owner)
  if (-not $Owner -or -not $Owner.commandLine) { return "down" }
  $commandLine = [string]$Owner.commandLine
  if ($commandLine -match "--mode\s+prod-proxy") { return "prod-proxy" }
  if ($commandLine -match "vite") { return "local-full" }
  return "unknown"
}

function Test-HttpHealthy {
  param([string]$Url)
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
    return [ordered]@{
      healthy = ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400)
      statusCode = $response.StatusCode
    }
  } catch {
    $statusCode = $null
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
      $statusCode = [int]$_.Exception.Response.StatusCode
    }
    return [ordered]@{
      healthy = $false
      statusCode = $statusCode
      error = $_.Exception.Message
    }
  }
}

$modeState = Get-ModeState
$mode = if ($modeState -and $modeState.mode) { [string]$modeState.mode } else { "unknown" }
$openPath = if ($modeState -and $modeState.openPath) { [string]$modeState.openPath } else { "/portal" }

$apiOwner = Get-PortOwner -Port $ApiPort
$appOwner = Get-PortOwner -Port $AppPort
$apiProbe = Test-HttpHealthy -Url "http://127.0.0.1:$ApiPort/health"
$appProbe = Test-HttpHealthy -Url "http://127.0.0.1:$AppPort$openPath"
$appActualMode = Get-AppActualMode -Owner $appOwner
$appModeMatches = $mode -eq "unknown" -or $appActualMode -eq "down" -or $appActualMode -eq $mode

$issues = @()
if (-not $apiOwner) { $issues += "api-port-not-listening" }
if (-not $apiProbe.healthy) { $issues += "api-health-failed" }
if (-not $appOwner) { $issues += "app-port-not-listening" }
if (-not $appProbe.healthy) { $issues += "app-health-failed" }
if (-not $appModeMatches) { $issues += "app-mode-mismatch" }

$result = [ordered]@{
  timestamp = (Get-Date).ToString("o")
  mode = $mode
  openPath = $openPath
  api = [ordered]@{
    port = $ApiPort
    owner = $apiOwner
    health = $apiProbe
  }
  app = [ordered]@{
    port = $AppPort
    expectedMode = $mode
    actualMode = $appActualMode
    modeMatches = $appModeMatches
    owner = $appOwner
    health = $appProbe
  }
  issues = $issues
  recoveryNeeded = ($issues.Count -gt 0)
}

$result | ConvertTo-Json -Depth 6
