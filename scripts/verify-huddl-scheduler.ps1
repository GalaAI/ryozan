param(
  [string]$ApiBase = "http://127.0.0.1:8801",
  [string]$AppBase = "http://127.0.0.1:5173",
  [string]$ArtistSlug = "justin",
  [decimal]$PassThreshold = 96.7,
  [string]$AuthToken = "",
  [string]$StripeTestSecretKey = $env:STRIPE_TEST_SECRET_KEY,
  [switch]$SkipFrontendBuild,
  [switch]$ReadOnlyRemote
)

$ErrorActionPreference = "Continue"

# New artifact justification:
# Existing Ryozan scripts cover local data sync, while
# CI covers broad lint/import/unit checks. This verifier is a release-readiness
# boundary for the Huddl scheduler plan, combining existing checks with local
# scheduler HTTP probes and a numeric success score.

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$ApiDir = Join-Path $RepoRoot "api"
$AppDir = Join-Path $RepoRoot "app"
$ReportDir = Join-Path $RepoRoot ".dev\state"
$ReportFile = if ($ReadOnlyRemote) { "huddl-scheduler-remote-readonly-verification.json" } else { "huddl-scheduler-verification.json" }
$ReportPath = Join-Path $ReportDir $ReportFile
# No default password is built in. Only the local smoke path needs the database.
$DatabaseSyncUrl = if ($env:DATABASE_SYNC_URL) { $env:DATABASE_SYNC_URL } elseif ($env:DATABASE_URL) { $env:DATABASE_URL } else { $null }
if (-not $ReadOnlyRemote -and -not $DatabaseSyncUrl) {
  throw "Set DATABASE_SYNC_URL or DATABASE_URL (see api/.env.example) for local verification."
}
if ($DatabaseSyncUrl -and $DatabaseSyncUrl.StartsWith("postgresql+asyncpg://")) {
  $DatabaseSyncUrl = $DatabaseSyncUrl.Replace("postgresql+asyncpg://", "postgresql://")
}

New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$results = New-Object System.Collections.Generic.List[object]

function Limit-Text {
  param(
    [string]$Text,
    [int]$MaxLength = 6000
  )
  if (-not $Text) { return "" }
  if ($Text.Length -le $MaxLength) { return $Text }
  return $Text.Substring(0, $MaxLength) + "`n...[truncated]"
}

function Add-Result {
  param(
    [string]$Name,
    [bool]$Passed,
    [string]$Detail,
    [string]$Evidence = ""
  )
  $results.Add([ordered]@{
    name = $Name
    passed = $Passed
    detail = $Detail
    evidence = Limit-Text -Text $Evidence
  }) | Out-Null
}

function Invoke-CheckedCommand {
  param(
    [string]$Name,
    [string]$WorkingDirectory,
    [string]$Command
  )

  Push-Location $WorkingDirectory
  try {
    $output = & powershell -NoProfile -ExecutionPolicy Bypass -Command $Command 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    Add-Result -Name $Name -Passed ($exitCode -eq 0) -Detail "exit=$exitCode" -Evidence $text
  } catch {
    Add-Result -Name $Name -Passed $false -Detail $_.Exception.Message
  } finally {
    Pop-Location
  }
}

function Invoke-HttpCheck {
  param(
    [string]$Name,
    [string]$Url,
    [scriptblock]$Assert,
    [hashtable]$Headers = $null
  )

  try {
    $requestArgs = @{
      Uri = $Url
      Method = "Get"
      TimeoutSec = 15
    }
    if ($Headers) {
      $requestArgs.Headers = $Headers
    }
    $response = Invoke-RestMethod @requestArgs
    $ok = & $Assert $response
    $json = $response | ConvertTo-Json -Depth 8 -Compress
    Add-Result -Name $Name -Passed ([bool]$ok) -Detail "GET $Url" -Evidence $json
  } catch {
    Add-Result -Name $Name -Passed $false -Detail "GET $Url failed: $($_.Exception.Message)"
  }
}

function Invoke-LocalSlotHoldReleaseSmoke {
  param(
    [string]$ArtistSlug,
    [string]$StartDate,
    [string]$EndDate
  )

  $slotUrl = "$ApiBase/api/v1/calendar/slots/$ArtistSlug`?start=$StartDate&end=$EndDate&duration_minutes=120&limit=1&include_blocked=true&blocked_limit=20"
  try {
    $slotResponse = Invoke-RestMethod -Uri $slotUrl -Method Get -TimeoutSec 15
    $slot = @($slotResponse.slots | Select-Object -First 1)
    if (-not $slot) {
      $evidence = $slotResponse | ConvertTo-Json -Depth 8 -Compress
      Add-Result -Name "local slot hold and release" -Passed $false -Detail "no open slot to hold" -Evidence $evidence
      return
    }

    $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $holdBody = @{
      artist_slug = $ArtistSlug
      date = $slot.date
      time = $slot.time
      duration_minutes = $slot.duration_minutes
      inquiry_id = "huddl-verifier-$stamp"
      guest_email = "huddl-verifier-$stamp@example.test"
    }

    $holdResponse = Invoke-RestMethod `
      -Uri "$ApiBase/api/v1/calendar/slot-holds" `
      -Method Post `
      -ContentType "application/json" `
      -Body ($holdBody | ConvertTo-Json -Depth 5) `
      -TimeoutSec 15

    $releaseResponse = Invoke-RestMethod `
      -Uri "$ApiBase/api/v1/calendar/slot-holds/$($holdResponse.hold_token)" `
      -Method Delete `
      -TimeoutSec 15

    $passed = (
      $holdResponse.hold_token -and
      $holdResponse.artist_slug -eq $ArtistSlug -and
      $holdResponse.date -eq $slot.date -and
      $holdResponse.time -eq $slot.time -and
      $releaseResponse.status -eq "released" -and
      $releaseResponse.released -eq $true
    )
    $evidence = @{
      selected_slot = $slot
      hold = $holdResponse
      release = $releaseResponse
    } | ConvertTo-Json -Depth 8 -Compress

    Add-Result -Name "local slot hold and release" -Passed ([bool]$passed) -Detail "POST hold then DELETE release" -Evidence $evidence
  } catch {
    Add-Result -Name "local slot hold and release" -Passed $false -Detail $_.Exception.Message
  }
}

function Test-LocalApiBase {
  return $ApiBase -match "^https?://(127\.0\.0\.1|localhost)(:\d+)?"
}

function Invoke-LocalVerifierCleanup {
  param(
    [string]$Email,
    [string]$InquiryId
  )

  $safeEmail = $Email.Replace("'", "''")
  $safeInquiry = $InquiryId.Replace("'", "''")
  $sql = @"
WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id, conversation_id, reference_code
  FROM ryozan_bookings
  WHERE guest_email = '$safeEmail'
     OR client_id IN (SELECT id FROM target_user)
),
target_conversation AS (
  SELECT conversation_id AS id FROM target_booking WHERE conversation_id IS NOT NULL
)
DELETE FROM ryozan_email_log
WHERE to_email = '$safeEmail'
   OR subject LIKE '%Huddl Verifier%'
   OR error LIKE '%$safeEmail%';

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id, conversation_id, reference_code
  FROM ryozan_bookings
  WHERE guest_email = '$safeEmail'
     OR client_id IN (SELECT id FROM target_user)
)
DELETE FROM ryozan_notifications
WHERE body LIKE '%Huddl Verifier%'
   OR action_url IN (SELECT '/dashboard/bookings/' || reference_code FROM target_booking WHERE reference_code IS NOT NULL);

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
)
DELETE FROM ryozan_booking_lifecycle_events
WHERE guest_email = '$safeEmail'
   OR booking_id IN (SELECT id FROM target_booking)
   OR hold_token IN (
      SELECT hold_token FROM ryozan_booking_slot_holds
      WHERE guest_email = '$safeEmail' OR inquiry_id = '$safeInquiry'
   );

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
)
DELETE FROM ryozan_crm_pipeline_tasks
WHERE booking_id IN (SELECT id FROM target_booking);

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id, conversation_id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
),
target_conversation AS (
  SELECT conversation_id AS id FROM target_booking WHERE conversation_id IS NOT NULL
)
DELETE FROM ryozan_messages
WHERE conversation_id IN (SELECT id FROM target_conversation);

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id, conversation_id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
),
target_conversation AS (
  SELECT conversation_id AS id FROM target_booking WHERE conversation_id IS NOT NULL
)
DELETE FROM ryozan_conversation_participants
WHERE conversation_id IN (SELECT id FROM target_conversation);

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
)
DELETE FROM ryozan_booking_slot_holds
WHERE guest_email = '$safeEmail'
   OR inquiry_id = '$safeInquiry'
   OR booking_id IN (SELECT id FROM target_booking);

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id, conversation_id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
)
DELETE FROM ryozan_bookings
WHERE id IN (SELECT id FROM target_booking);

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_conversation AS (
  SELECT c.id
  FROM ryozan_conversations c
  WHERE c.created_by IN (SELECT id FROM target_user)
     OR c.name LIKE '%Huddl Verifier%'
)
DELETE FROM ryozan_conversations
WHERE id IN (SELECT id FROM target_conversation);

DELETE FROM ryozan_booking_drafts
WHERE guest_email = '$safeEmail' OR inquiry_id = '$safeInquiry';

WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
)
DELETE FROM ryozan_user_quests
WHERE user_id IN (SELECT id FROM target_user);

DELETE FROM ryozan_users
WHERE email = '$safeEmail';
"@

  $cleanup = & psql -X -q -v ON_ERROR_STOP=1 $DatabaseSyncUrl -c $sql 2>&1
  $cleanupExit = $LASTEXITCODE

  $countSql = @"
WITH target_user AS (
  SELECT id FROM ryozan_users WHERE email = '$safeEmail'
),
target_booking AS (
  SELECT id FROM ryozan_bookings WHERE guest_email = '$safeEmail' OR client_id IN (SELECT id FROM target_user)
)
SELECT
  (SELECT count(*) FROM target_user) +
  (SELECT count(*) FROM target_booking) +
  (SELECT count(*) FROM ryozan_booking_slot_holds WHERE guest_email = '$safeEmail' OR inquiry_id = '$safeInquiry') +
  (SELECT count(*) FROM ryozan_booking_drafts WHERE guest_email = '$safeEmail' OR inquiry_id = '$safeInquiry') AS remaining;
"@
  $remaining = (& psql -X -q -t -A -v ON_ERROR_STOP=1 $DatabaseSyncUrl -c $countSql 2>&1 | Out-String).Trim()
  return [ordered]@{
    exit = $cleanupExit
    remaining = $remaining
    output = Limit-Text -Text (($cleanup | Out-String).Trim()) -MaxLength 1000
  }
}

function Invoke-LocalSubmitSmoke {
  param(
    [string]$ArtistSlug,
    [string]$StartDate,
    [string]$EndDate
  )

  if (-not (Test-LocalApiBase)) {
    Add-Result -Name "local booking submit and cleanup" -Passed $false -Detail "ApiBase is not localhost"
    return
  }

  $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  $email = "huddl-submit-$stamp@example.test"
  $inquiryId = "huddl-submit-$stamp"
  $cleanupResult = $null

  try {
    $slotUrl = "$ApiBase/api/v1/calendar/slots/$ArtistSlug`?start=$StartDate&end=$EndDate&duration_minutes=120&limit=1&include_blocked=true&blocked_limit=20"
    $slotResponse = Invoke-RestMethod -Uri $slotUrl -Method Get -TimeoutSec 15
    $slot = @($slotResponse.slots | Select-Object -First 1)
    if (-not $slot) {
      $evidence = $slotResponse | ConvertTo-Json -Depth 8 -Compress
      Add-Result -Name "local booking submit and cleanup" -Passed $false -Detail "no open slot to submit" -Evidence $evidence
      return
    }

    $holdBody = @{
      artist_slug = $ArtistSlug
      date = $slot.date
      time = $slot.time
      duration_minutes = $slot.duration_minutes
      inquiry_id = $inquiryId
      guest_email = $email
    }
    $holdResponse = Invoke-RestMethod `
      -Uri "$ApiBase/api/v1/calendar/slot-holds" `
      -Method Post `
      -ContentType "application/json" `
      -Body ($holdBody | ConvertTo-Json -Depth 5) `
      -TimeoutSec 15

    $bookingBody = @{
      inquiry_id = $inquiryId
      artist_slug = $ArtistSlug
      first_name = "Huddl"
      last_name = "Verifier"
      email = $email
      phone = "864-555-0199"
      sms_opt_in = $true
      description = "Local verifier booking smoke for Huddl scheduler cleanup."
      style_preference = "black and grey"
      placement = "forearm"
      size_estimate = "medium"
      budget = "500-1000"
      timeline = "flexible"
      preferred_date = $slot.date
      preferred_time = $slot.time
      hold_token = $holdResponse.hold_token
      client_notes = "Disposable verifier booking. Safe to delete."
    }

    $draftResponse = Invoke-RestMethod `
      -Uri "$ApiBase/api/v1/bookings/draft" `
      -Method Post `
      -ContentType "application/json" `
      -Body ($bookingBody | ConvertTo-Json -Depth 6) `
      -TimeoutSec 15

    $submitResponse = Invoke-RestMethod `
      -Uri "$ApiBase/api/v1/bookings/submit" `
      -Method Post `
      -ContentType "application/json" `
      -Body ($bookingBody | ConvertTo-Json -Depth 6) `
      -TimeoutSec 20

    $cleanupResult = Invoke-LocalVerifierCleanup -Email $email -InquiryId $inquiryId

    $passed = (
      $holdResponse.hold_token -and
      $draftResponse.status -eq "draft" -and
      $submitResponse.reference_code -match "^BKG-" -and
      $cleanupResult.exit -eq 0 -and
      $cleanupResult.remaining -eq "0"
    )
    $evidence = @{
      selected_slot = $slot
      hold = $holdResponse
      draft = $draftResponse
      submit = $submitResponse
      cleanup = $cleanupResult
    } | ConvertTo-Json -Depth 8 -Compress
    Add-Result -Name "local booking submit and cleanup" -Passed ([bool]$passed) -Detail "hold, draft, submit, cleanup" -Evidence $evidence
  } catch {
    $cleanupResult = Invoke-LocalVerifierCleanup -Email $email -InquiryId $inquiryId
    $evidence = @{ cleanup = $cleanupResult } | ConvertTo-Json -Depth 6 -Compress
    Add-Result -Name "local booking submit and cleanup" -Passed $false -Detail $_.Exception.Message -Evidence $evidence
  }
}

function Test-ReadinessIncludesSchedulerTables {
  param([object]$Body)
  $checks = $Body.checks
  return (
    ($Body.status -eq "ready" -or $Body.status -eq "ok") -and
    $checks -and
    $checks.bttco_booking_slot_holds -eq "ok" -and
    $checks.bttco_booking_lifecycle_events -eq "ok"
  )
}

function Invoke-AlembicSchedulerHeadCheck {
  Push-Location $ApiDir
  try {
    $heads = @(alembic heads 2>&1)
    $headsExit = $LASTEXITCODE
    $current = @(alembic current 2>&1)
    $currentExit = $LASTEXITCODE
    $evidence = @($heads + $current) -join "`n"
    $currentText = $current -join "`n"
    $passed = (
      $headsExit -eq 0 -and
      $currentExit -eq 0 -and
      $heads.Count -eq 1 -and
      $heads[0] -match "20260703_booking_addons" -and
      $currentText -match "20260703_booking_addons.*head"
    )
    Add-Result -Name "alembic scheduler head" -Passed $passed -Detail "heads_exit=$headsExit current_exit=$currentExit" -Evidence $evidence
  } catch {
    Add-Result -Name "alembic scheduler head" -Passed $false -Detail $_.Exception.Message
  } finally {
    Pop-Location
  }
}

function Invoke-StripeTestCheckoutSmoke {
  param(
    [string]$SecretKey
  )

  if (-not $SecretKey) {
    Add-Result -Name "stripe test checkout smoke" -Passed $false -Detail "STRIPE_TEST_SECRET_KEY is not set"
    return
  }
  if (-not $SecretKey.StartsWith("sk_test")) {
    Add-Result -Name "stripe test checkout smoke" -Passed $false -Detail "refused non-test Stripe key"
    return
  }

  Push-Location $ApiDir
  $previousStripeKey = $env:STRIPE_SECRET_KEY
  $previousLocalMode = $env:LOCAL_DEV_MODE
  $previousFrontendUrl = $env:FRONTEND_URL
  try {
    $env:STRIPE_SECRET_KEY = $SecretKey
    $env:LOCAL_DEV_MODE = "true"
    $env:FRONTEND_URL = "http://127.0.0.1:5173"

    $python = @'
import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta

from app.api.v1.endpoints.bookings import _create_public_booking_checkout
from app.models.booking import Booking
from app.models.booking_lifecycle_event import BookingLifecycleEvent
from app.models.booking_slot_hold import BookingSlotHold
from app.models.user import User


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


async def main():
    artist_id = uuid.uuid4()
    artist = User(
        id=artist_id,
        email="stripe-smoke@example.test",
        password_hash="disabled",
        name="Stripe Smoke Artist",
        role="artist",
        slug="stripe-smoke",
        stripe_account_id=None,
        stripe_onboarding_complete=False,
        commission_rate=70,
    )
    booking = Booking(
        id=uuid.uuid4(),
        artist_id=artist_id,
        reference_code="BKG-SMOKE",
        guest_name="Stripe Smoke",
        deposit_amount=100,
    )
    hold = BookingSlotHold(
        id=uuid.uuid4(),
        hold_token=f"stripe-smoke-{uuid.uuid4()}",
        artist_id=artist_id,
        artist_slug="stripe-smoke",
        scheduled_date=date.today() + timedelta(days=7),
        scheduled_time="12:00",
        duration_minutes=120,
        status="consumed",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db = FakeSession()
    url = await _create_public_booking_checkout(
        db,
        booking=booking,
        artist=artist,
        slot_hold=hold,
        client_email="stripe-smoke@example.test",
        deposit_amount=100,
        save_payment_method=True,
    )
    events = [row for row in db.added if isinstance(row, BookingLifecycleEvent)]
    print(json.dumps({
        "checkout_url_prefix": url.split("?", 1)[0],
        "stripe_session_id_prefix": (booking.stripe_checkout_session_id or "")[:8],
        "events": [event.event_type for event in events],
        "route_mode": events[0].details.get("route_mode") if events else None,
        "save_payment_method": events[0].details.get("save_payment_method") if events else None,
    }))


asyncio.run(main())
'@
    $output = $python | python -
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $parsed = $null
    try { $parsed = $text | ConvertFrom-Json } catch { $parsed = $null }
    $passed = (
      $exitCode -eq 0 -and
      $parsed -and
      $parsed.checkout_url_prefix -match "^https://checkout\.stripe\.com/" -and
      $parsed.stripe_session_id_prefix -eq "cs_test_" -and
      @($parsed.events) -contains "checkout_created" -and
      $parsed.route_mode -eq "shop_fallback" -and
      $parsed.save_payment_method -eq $true
    )
    Add-Result -Name "stripe test checkout smoke" -Passed ([bool]$passed) -Detail "real Stripe Checkout Session in test mode" -Evidence $text
  } catch {
    Add-Result -Name "stripe test checkout smoke" -Passed $false -Detail $_.Exception.Message
  } finally {
    if ($null -eq $previousStripeKey) { Remove-Item Env:STRIPE_SECRET_KEY -ErrorAction SilentlyContinue } else { $env:STRIPE_SECRET_KEY = $previousStripeKey }
    if ($null -eq $previousLocalMode) { Remove-Item Env:LOCAL_DEV_MODE -ErrorAction SilentlyContinue } else { $env:LOCAL_DEV_MODE = $previousLocalMode }
    if ($null -eq $previousFrontendUrl) { Remove-Item Env:FRONTEND_URL -ErrorAction SilentlyContinue } else { $env:FRONTEND_URL = $previousFrontendUrl }
    Pop-Location
  }
}

function Invoke-BookingE2ESandboxGauntlet {
  param([string]$SecretKey)

  if (-not $SecretKey -or -not $SecretKey.StartsWith("sk_test_")) {
    Add-Result -Name "booking consultation and deposit E2E" -Passed $false -Detail "Stripe sk_test key is required"
    return
  }
  if (-not (Test-LocalApiBase)) {
    Add-Result -Name "booking consultation and deposit E2E" -Passed $false -Detail "ApiBase is not localhost"
    return
  }

  $sandboxEnv = @{
    APP_ENV = "development"
    RYOZAN_E2E_SANDBOX = "1"
    DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/nexus"
    DATABASE_SYNC_URL = "postgresql://postgres:postgres@127.0.0.1:5433/nexus"
    EMAIL_TEST_REDIRECT = ""
    GOOGLE_CALENDAR_AUTH_MODE = "oauth"
    GOOGLE_CLIENT_ID = "sandbox.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = "sandbox-secret"
    LOCAL_DEV_MODE = "false"
    PYTHONPATH = $ApiDir
    RESEND_API_KEY = ""
    SENDGRID_API_KEY = ""
    SHOP_EMAIL = "shop@example.test"
    SHOP_OWNER_EMAIL = "owner@example.test"
    SMTP_FROM_EMAIL = "dev@ryozan-studio.local"
    SMTP_HOST = "127.0.0.1"
    SMTP_PASSWORD = "mailhog"
    SMTP_PORT = "1025"
    SMTP_USER = "mailhog"
    STRIPE_SECRET_KEY = $SecretKey
    STRIPE_WEBHOOK_SECRET = "whsec_$([guid]::NewGuid().ToString('N'))"
    TWILIO_ACCOUNT_SID = ""
    TWILIO_AUTH_TOKEN = ""
    TWILIO_PHONE_NUMBER = ""
    VAPID_PRIVATE_KEY = ""
  }
  $previous = @{}
  foreach ($name in $sandboxEnv.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $sandboxEnv[$name], "Process")
  }

  Push-Location $ApiDir
  try {
    $python = Join-Path $ApiDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $python = "python" }
    $output = & $python -m unittest tests.booking_e2e_sandbox_tests -v 2>&1
    $exitCode = $LASTEXITCODE
    Add-Result `
      -Name "booking consultation and deposit E2E" `
      -Passed ($exitCode -eq 0) `
      -Detail "real routes, PostgreSQL, Stripe test Checkout, signed webhook, MailHog, Calendar capture" `
      -Evidence (($output | Out-String).Trim())
  } catch {
    Add-Result -Name "booking consultation and deposit E2E" -Passed $false -Detail $_.Exception.Message
  } finally {
    Pop-Location
    foreach ($name in $sandboxEnv.Keys) {
      [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
    }
  }
}

if (-not $ReadOnlyRemote) {
  Invoke-CheckedCommand `
    -Name "backend scheduler tests" `
    -WorkingDirectory $ApiDir `
    -Command "python -m pytest tests\booking_scheduler_tests.py tests\google_auth_status_tests.py -q"

  Invoke-CheckedCommand `
    -Name "backend scheduler ruff" `
    -WorkingDirectory $ApiDir `
    -Command "ruff check app\api\v1\endpoints\bookings.py app\api\v1\endpoints\calendar.py app\api\v1\endpoints\quotes.py app\api\v1\endpoints\flash.py app\api\v1\endpoints\stripe_webhooks.py app\schemas\booking.py tests\booking_scheduler_tests.py tests\google_auth_status_tests.py"

  Invoke-AlembicSchedulerHeadCheck

  Invoke-CheckedCommand `
    -Name "frontend lint" `
    -WorkingDirectory $AppDir `
    -Command "npm run lint"

  if (-not $SkipFrontendBuild) {
    Invoke-CheckedCommand `
      -Name "frontend build" `
      -WorkingDirectory $AppDir `
      -Command "npm run build"
  }

  Invoke-CheckedCommand `
    -Name "git diff whitespace" `
    -WorkingDirectory $RepoRoot `
    -Command "git diff --check"
}

Invoke-HttpCheck `
  -Name "api health" `
  -Url "$ApiBase/health" `
  -Assert { param($body) $body.status -eq "ok" }

Invoke-HttpCheck `
  -Name "api readiness" `
  -Url "$ApiBase/health/ready" `
  -Assert { param($body) Test-ReadinessIncludesSchedulerTables -Body $body }

Invoke-HttpCheck `
  -Name "scheduler contract" `
  -Url "$ApiBase/api/v1/calendar/scheduler-contract/$ArtistSlug" `
  -Assert {
    param($body)
    $actions = @($body.actions | ForEach-Object { $_.name })
    $required = @("list_slots", "hold_slot", "release_hold", "submit_booking")
    ($body.artist_slug -eq $ArtistSlug) -and (($required | Where-Object { $actions -notcontains $_ }).Count -eq 0)
  }

if ($AuthToken) {
  Invoke-HttpCheck `
    -Name "google oauth status" `
    -Url "$ApiBase/api/v1/auth/google/status" `
    -Headers @{ Authorization = "Bearer $AuthToken" } `
    -Assert {
      param($body)
      $body.connected -eq $true -and
      $body.healthy -eq $true -and
      $body.calendar_accessible -eq $true -and
      $body.reconnect_required -eq $false
    }
}

$slotStart = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
$slotEnd = (Get-Date).AddDays(30).ToString("yyyy-MM-dd")
Invoke-HttpCheck `
  -Name "server slot listing" `
  -Url "$ApiBase/api/v1/calendar/slots/$ArtistSlug`?start=$slotStart&end=$slotEnd&duration_minutes=120&limit=1&include_blocked=true&blocked_limit=20" `
  -Assert {
    param($body)
    (@($body.slots).Count -gt 0) -or (@($body.blocked_slots).Count -gt 0)
  }

Invoke-HttpCheck `
  -Name "public booking add-ons route" `
  -Url "$ApiBase/api/v1/booking-addons/artist/$ArtistSlug`?active_only=true" `
  -Assert {
    param($body)
    $body -is [array]
  }

if (-not $ReadOnlyRemote) {
  Invoke-LocalSlotHoldReleaseSmoke -ArtistSlug $ArtistSlug -StartDate $slotStart -EndDate $slotEnd
  Invoke-LocalSubmitSmoke -ArtistSlug $ArtistSlug -StartDate $slotStart -EndDate $slotEnd
  Invoke-StripeTestCheckoutSmoke -SecretKey $StripeTestSecretKey
  Invoke-BookingE2ESandboxGauntlet -SecretKey $StripeTestSecretKey

  Invoke-HttpCheck `
    -Name "local app route" `
    -Url "$AppBase/book/$ArtistSlug" `
    -Assert { param($body) $true }
} else {
  Invoke-HttpCheck `
    -Name "public booking route" `
    -Url "$AppBase/book/$ArtistSlug" `
    -Assert { param($body) $true }
}

$passedCount = @($results | Where-Object { $_.passed }).Count
$totalCount = $results.Count
$score = if ($totalCount -gt 0) { [math]::Round(($passedCount / $totalCount) * 100, 1) } else { 0 }
$passed = $score -ge $PassThreshold

$report = [ordered]@{
  timestamp = (Get-Date).ToString("o")
  objective = "Huddl Booking Cleanup And Acuity-Level Scheduler Plan"
  mode = if ($ReadOnlyRemote) { "read-only-remote" } else { "local" }
  apiBase = $ApiBase
  appBase = $AppBase
  artistSlug = $ArtistSlug
  threshold = [decimal]$PassThreshold
  score = $score
  passed = $passed
  passedCount = $passedCount
  totalCount = $totalCount
  results = $results
  productionGates = @(
    "deploy through approved PR path",
    "production alembic upgrade",
    "production health and readiness",
    "production Google OAuth status",
    "live slot hold smoke",
    "operator-approved live booking test",
    "real Stripe booking checkout smoke with sk_test",
    "quote checkout smoke",
    "flash checkout smoke",
    "post-deploy duplicate and scheduler monitoring"
  )
}

$report | ConvertTo-Json -Depth 10 | Set-Content -Path $ReportPath
$report | ConvertTo-Json -Depth 6

if (-not $passed) {
  exit 1
}
