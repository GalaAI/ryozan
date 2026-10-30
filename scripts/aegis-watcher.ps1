param(
  [switch]$EmitNominal,
  [switch]$Watch,
  [int]$IntervalSeconds = 30,
  [switch]$Visible,
  [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

# New artifact justification:
# BTTCO had launch scripts but no recurring observer that could route health events into a repair path.
# Watcher is the stable orchestration entrypoint for automation, with Pulse logging and bounded handoff
# to Spectr recon and Sentinel recovery.

$ReconScript = Join-Path $PSScriptRoot "aegis-spectr-recon.ps1"
$SentinelScript = Join-Path $PSScriptRoot "aegis-sentinel-recover.ps1"
$PulseScript = Join-Path $PSScriptRoot "pulse-dev-health.ps1"

function Invoke-WatcherPass {
  $recon = & $ReconScript | ConvertFrom-Json
  $mode = [string]$recon.mode

  if ($recon.recoveryNeeded) {
    & $PulseScript `
      -EventType "degraded" `
      -Severity "warn" `
      -Source "Watcher" `
      -Mode $mode `
      -Message "Watcher detected a degraded BTTCO dev environment." `
      -DataJson ($recon | ConvertTo-Json -Compress) | Out-Null

    $sentinelResult = & $SentinelScript -Visible:$Visible -OpenBrowser:$OpenBrowser | ConvertFrom-Json
    return [ordered]@{
      state = "recovered"
      mode = $mode
      recon = $recon
      sentinel = $sentinelResult
    }
  }

  if ($EmitNominal) {
    & $PulseScript `
      -EventType "nominal" `
      -Severity "info" `
      -Source "Watcher" `
      -Mode $mode `
      -Message "Watcher confirms the BTTCO dev environment is nominal." `
      -DataJson ($recon | ConvertTo-Json -Compress) | Out-Null
  }

  return [ordered]@{
    state = "nominal"
    mode = $mode
    recon = $recon
  }
}

if ($Watch) {
  while ($true) {
    $result = Invoke-WatcherPass
    Write-Output ($result | ConvertTo-Json -Depth 8)
    Start-Sleep -Seconds $IntervalSeconds
  }
}

Invoke-WatcherPass | ConvertTo-Json -Depth 8
