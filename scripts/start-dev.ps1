param(
  [switch]$CheckOnly,
  [switch]$Visible,
  [switch]$OpenBrowser,
  [string]$OpenPath = '/portal',
  [int]$PreferredAppPort = 5173,
  [ValidateRange(1, 60)]
  [int]$TtlMinutes = 60,
  [switch]$ProductionProxy
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# The repo sits at <ark-root>\vessels\entities\<name>; ARK_ROOT overrides.
$ArkRoot = if ($env:ARK_ROOT) { $env:ARK_ROOT } else { (Resolve-Path (Join-Path $RepoRoot '..\..\..')).Path }
$CanonicalRoot = Join-Path $ArkRoot 'vessels\entities\bttco'
$ArkLauncher = Join-Path $ArkRoot '.ark\scripts\bttco-dev.ps1'

# Normal startup and recovery have one owner. A worktree may only request an
# explicit UI comparison on 5174; it cannot take over the shared app or API.
if ($PreferredAppPort -notin @(5173, 5174)) { throw 'Use shared 5173 or explicitly request comparison port 5174.' }
if ($PreferredAppPort -eq 5173) {
  if ($RepoRoot -ine $CanonicalRoot) { throw 'This worktree cannot own shared 5173. Use the canonical checkout or explicitly request -PreferredAppPort 5174.' }
  if ($ProductionProxy) { throw 'Shared 5173 uses the local API. ProductionProxy requires explicit comparison port 5174.' }
  if (-not (Test-Path -LiteralPath $ArkLauncher)) { throw 'The canonical Ark BTTCO launcher is unavailable.' }
  if ($CheckOnly) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ArkLauncher -CheckOnly
    exit $LASTEXITCODE
  }
  $probe = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ArkLauncher -CheckOnly
  if ($LASTEXITCODE -ne 0) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ArkLauncher -StartDetached
    if ($LASTEXITCODE -ne 0) { throw 'Canonical Docker startup failed.' }
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
      $probe = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ArkLauncher -CheckOnly
      if ($LASTEXITCODE -eq 0) { $ready = $true; break }
      Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Canonical stack has not become ready. Inspect Docker startup; no alternate server was launched.' }
  }
  Write-Output ($probe -join [Environment]::NewLine)
} else {
  $worktreeRoot = Join-Path $CanonicalRoot '.worktrees'
  if (-not $RepoRoot.StartsWith($worktreeRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
      -not (Test-Path -LiteralPath (Join-Path $RepoRoot '.git') -PathType Leaf)) {
    throw 'Comparison port 5174 requires a linked worktree under the canonical BTTCO .worktrees directory.'
  }
  $remote = (& git -C $RepoRoot remote get-url origin).Trim()
  if ($LASTEXITCODE -ne 0 -or $remote -notmatch '^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)GalaAI/BTTCO(?:\.git)?$') {
    throw 'Comparison workspace must belong to GalaAI/BTTCO.'
  }
  $listener = Get-NetTCPConnection -LocalPort 5174 -State Listen -ErrorAction SilentlyContinue
  if ($listener) { throw 'Comparison port 5174 is already owned. Reuse or stop its recorded owner; automatic replacement is disabled.' }
  $AppDir = Join-Path $RepoRoot 'app'
  $vite = Join-Path $AppDir 'node_modules\vite\bin\vite.js'
  if (-not (Test-Path -LiteralPath $vite)) { throw 'Install this worktree dependency lock before starting its comparison preview.' }
  if (-not $ProductionProxy) {
    $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8801/health' -TimeoutSec 5
    if ($response.StatusCode -ne 200) { throw 'The shared local API is unavailable.' }
  }
  $receipt = [ordered]@{ sourceRoot = $RepoRoot; branch = (& git -C $RepoRoot branch --show-current).Trim(); headSha = (& git -C $RepoRoot rev-parse HEAD).Trim(); appPort = 5174; mode = $(if ($ProductionProxy) { 'prod-proxy' } else { 'local-full' }) }
  if (-not $CheckOnly) {
    # The Ark owner records process identity and enforces expiry for every entry point.
    $candidateOwner = Join-Path $PSScriptRoot 'bttco-candidate.ps1'
    $target = if ($ProductionProxy) { 'https://api.ryozan.app' } else { 'http://127.0.0.1:8801' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $candidateOwner start -Worktree $RepoRoot -Port 5174 -ApiTarget $target -TtlMinutes $TtlMinutes -Json
    if ($LASTEXITCODE -ne 0) { throw 'The Ark candidate owner could not start a connected preview. Inspect its returned receipt and logs.' }
  } else {
    $receipt | ConvertTo-Json
  }
}
if ($OpenBrowser -and -not $CheckOnly) { Start-Process "http://localhost:$PreferredAppPort$OpenPath" }
