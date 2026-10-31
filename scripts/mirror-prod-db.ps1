<#
Mirror scoped production data into a local BTTCO database.

Wraps the repo's existing, deliberately scoped sync pipeline (the owners):
  api/scripts/dump_prod_test_data.py   prod -> .dev/state/prod-test-dump.json
  api/scripts/sync_public_state.py     public artist profiles via public API
  api/scripts/sync_test_data.py        dump -> LOCAL database (refuses non-local)

Prod credentials come from the Railway CLI (project BTTCO / service Postgres,
already linked in this repo). Nothing writes to production; the dump is a read.
Client PII is pseudonymized by default (the dump script's default).

Usage:
  ./scripts/mirror-prod-db.ps1                 # into host Postgres (localhost:5432/nexus)
  ./scripts/mirror-prod-db.ps1 -Docker         # into the compose db service (localhost:5433/nexus)
  ./scripts/mirror-prod-db.ps1 -Days 180       # widen the booking window
#>
param(
  [switch]$Docker,
  [int]$Days = 90
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
$py = Join-Path $repo "api\.venv\Scripts\python.exe"

# 1. Resolve the prod read URL from Railway (public proxy URL, not the
#    .railway.internal one, which only resolves inside Railway's network).
Write-Host "[1/4] Resolving production database URL via Railway CLI..."
$vars = railway variables --json | ConvertFrom-Json
$prodUrl = $vars.DATABASE_PUBLIC_URL
if (-not $prodUrl) { $prodUrl = $vars.DATABASE_URL }
if (-not $prodUrl -or $prodUrl -match "railway\.internal") {
  Write-Error "No externally resolvable prod URL (need DATABASE_PUBLIC_URL from the Postgres service). Run 'railway variables' to inspect."
}

# 2. Dump scoped prod data (sanitized by default).
Write-Host "[2/4] Dumping scoped production data (sanitized)..."
$env:DATABASE_URL = $prodUrl
& $py (Join-Path $repo "api\scripts\dump_prod_test_data.py") --days $Days
if ($LASTEXITCODE -ne 0) { Write-Error "dump_prod_test_data.py failed" }

# 3. Point at the LOCAL target and import.
if ($Docker) {
  $localUrl = "postgresql+asyncpg://postgres:postgres@localhost:5433/nexus"
  Write-Host "[3/4] Target: docker compose db (localhost:5433/nexus)"
  # docker-compose.override.yml puts every dev service behind the `arkdev`
  # profile, so without it these two commands match no service at all: the db
  # is never started and `ps -q db` returns nothing, which then hands
  # docker inspect an empty argument. It only appeared to work when the stack
  # was already running for another reason.
  docker compose --profile arkdev up -d db | Out-Null
  # wait for healthy
  $tries = 0
  while ($true) {
    $dbId = (docker compose --profile arkdev ps -q db)
    if (-not $dbId) { Write-Error "db container was not created by docker compose" }
    if ((docker inspect --format "{{.State.Health.Status}}" $dbId) -eq "healthy") { break }
    Start-Sleep -Seconds 2; $tries++
    if ($tries -gt 30) { Write-Error "db container never became healthy" }
  }
} else {
  $localUrl = "postgresql+asyncpg://postgres:postgres@localhost:5432/nexus"
  Write-Host "[3/4] Target: host Postgres (localhost:5432/nexus)"
}
$env:DATABASE_URL = $localUrl

# Fresh docker volumes have no schema; migrate to the Alembic head (idempotent).
Write-Host "      Ensuring schema (alembic upgrade head)..."
& $py (Join-Path $repo "api\scripts\ensure_schema.py")
if ($LASTEXITCODE -ne 0) { Write-Error "schema init failed" }

Write-Host "      Syncing public artist state..."
& $py (Join-Path $repo "api\scripts\sync_public_state.py")
if ($LASTEXITCODE -ne 0) { Write-Error "sync_public_state.py failed" }

Write-Host "[4/4] Importing scoped prod dump into local db..."
& $py (Join-Path $repo "api\scripts\sync_test_data.py")
if ($LASTEXITCODE -ne 0) { Write-Error "sync_test_data.py failed" }

Remove-Item Env:\DATABASE_URL
Write-Host ""
Write-Host "Done. Local database now mirrors scoped production state." -ForegroundColor Green
if ($Docker) { Write-Host "API against it: set DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/nexus" }
