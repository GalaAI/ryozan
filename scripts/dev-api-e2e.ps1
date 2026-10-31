<#
Start the Ryozan API in E2E mode: real sends OFF, everything captured locally.

- Email: providers blanked, SMTP pointed at the compose mailhog (UI :8025),
  LOCAL_DEV_MODE off so mail actually flows (into mailhog, true recipients).
- EMAIL_TEST_REDIRECT blanked so nothing leaves the machine.
- Stripe: UNCHANGED from api/.env. If api/.env carries live keys, do NOT run
  purchase E2E against this instance; put sk_test/pk_test keys in api/.env
  first (see docs/testing/E2E_LOCAL.md).
- DB: host nexus by default; -Docker targets the compose db on :5433.

Usage:
  docker compose up -d mailhog          # once
  ./scripts/dev-api-e2e.ps1             # host db
  ./scripts/dev-api-e2e.ps1 -Docker     # compose db
#>
param(
  [switch]$Docker,
  [int]$Port = 8801
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent

$env:LOCAL_DEV_MODE = "false"
$env:EMAIL_TEST_REDIRECT = ""
$env:RESEND_API_KEY = ""
$env:SENDGRID_API_KEY = ""
$env:SMTP_HOST = "localhost"
$env:SMTP_PORT = "1025"
$env:SMTP_USER = "mailhog"
$env:SMTP_PASSWORD = "mailhog"
$env:SMTP_FROM_EMAIL = "dev@ryozan-studio.local"
if ($Docker) {
  $env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5433/nexus"
}

Write-Host "E2E API on :$Port (email -> mailhog http://localhost:8025, sends OFF)" -ForegroundColor Green
& (Join-Path $repo "api\.venv\Scripts\python.exe") -m uvicorn main:app --port $Port --app-dir (Join-Path $repo "api")
