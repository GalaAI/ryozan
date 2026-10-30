param([switch]$Visible, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
# Docker and the canonical launcher own recovery. Never kill an unidentified
# port owner or revive an obsolete native API/proxy stack from saved mode state.
$startArgs = @{ OpenPath = '/dashboard'; Visible = $Visible; OpenBrowser = $OpenBrowser }
& (Join-Path $PSScriptRoot 'start-dev.ps1') @startArgs
if ($LASTEXITCODE -ne 0) { throw 'Canonical BTTCO recovery failed.' }