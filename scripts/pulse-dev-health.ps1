param(
  [string]$EventType = "status",
  [string]$Severity = "info",
  [string]$Message = "A.E.G.I.S. pulse emitted.",
  [string]$Source = "Watcher",
  [string]$Mode = "unknown",
  [string]$DataJson = "{}"
)

$ErrorActionPreference = "Stop"

# New artifact justification:
# BTTCO had startup scripts but no structured Pulse log for local dev health.
# This file creates the smallest reusable event lane so Watcher, Spectr, and Sentinel
# can record state changes without each inventing its own log format.

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot ".dev\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$PulsePath = Join-Path $LogDir "aegis-pulse.jsonl"

try {
  $payload = $DataJson | ConvertFrom-Json -AsHashtable
} catch {
  $payload = @{ raw = $DataJson }
}

$entry = [ordered]@{
  timestamp = (Get-Date).ToString("o")
  source = $Source
  mode = $Mode
  severity = $Severity
  eventType = $EventType
  message = $Message
  data = $payload
}

($entry | ConvertTo-Json -Compress) + [Environment]::NewLine | Add-Content -Path $PulsePath
Write-Output ($entry | ConvertTo-Json -Depth 5)
