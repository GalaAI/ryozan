# Candidate process ownership lives in Ark. Keep this compatibility entry point
# so BTTCO callers consume the same lock, receipt, expiry and stop implementation.
param(
  [Parameter(Position = 0)]
  [ValidateSet('start', 'stop', 'status', 'probe', 'extend', 'supervise')]
  [string]$Action = 'status',
  [string]$Worktree = '',
  [int]$Port = 5174,
  [string]$ApiTarget = '',
  [int]$TtlMinutes = 60,
  [switch]$Expose,
  [string]$StateRoot = '',
  [int]$ReadyTimeoutSeconds = 120,
  [switch]$Json
)
$ErrorActionPreference = 'Stop'
# The repo sits at <ark-root>\vessels\entities\<name>; ARK_ROOT overrides.
$ArkRoot = if ($env:ARK_ROOT) { $env:ARK_ROOT } else { (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path }
$owner = Join-Path $ArkRoot '.ark\scripts\bttco-candidate.ps1'
if (-not (Test-Path -LiteralPath $owner -PathType Leaf)) {
  throw 'The Ark candidate owner is unavailable. Restore it before starting a preview.'
}
& $owner @PSBoundParameters
exit $LASTEXITCODE
