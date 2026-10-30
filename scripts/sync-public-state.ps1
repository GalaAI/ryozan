$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ApiDir = Join-Path $RepoRoot "api"

Push-Location $ApiDir
try {
  python scripts\sync_public_state.py --create-missing
} finally {
  Pop-Location
}
