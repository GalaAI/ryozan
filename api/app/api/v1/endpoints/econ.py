"""
Hustle E-con connector endpoints.

This module owns read-only payment integration visibility for finance rails
that are not yet first-class ledgers. Write actions such as Mercury payments,
Clover order mutation, payroll execution, and tax filing need a dedicated
approval table before they are exposed here.
"""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants.timezones import shop_day_bounds, shop_today
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.econ import EconConnection, EconLedgerEntry, EconSyncRun
from app.models.user import User

logger = logging.getLogger("ryozan.econ")
router = APIRouter(prefix="/econ", tags=["econ"])


class EconSyncRequest(BaseModel):
    providers: list[str] | None = None
    limit: int = 25
    persist: bool = True
    mercury_account_id: str | None = None


def _require_role(user: User, min_role: str):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")


def _clover_web_base() -> str:
    settings = get_settings()
    if settings.CLOVER_ENVIRONMENT.lower() == "production":
        return "https://www.clover.com"
    return "https://sandbox.dev.clover.com"


def _clover_api_base() -> str:
    settings = get_settings()
    if settings.CLOVER_ENVIRONMENT.lower() == "production":
        return "https://api.clover.com/v3"
    return "https://apisandbox.dev.clover.com/v3"


def _clover_token_url() -> str:
    return f"{_clover_web_base()}/oauth/v2/token"


def _mercury_headers() -> dict[str, str]:
    token = get_settings().MERCURY_API_TOKEN
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _mercury_transactions_url(account_id: str | None = None) -> str:
    base = get_settings().MERCURY_API_BASE.rstrip("/")
    if account_id:
        return f"{base}/account/{account_id}/transactions"
    return f"{base}/transactions"


def _redact_connector_status(configured: bool, detail: str) -> dict[str, Any]:
    return {"configured": configured, "detail": detail}


def _provider_setup_manifest(provider: str) -> dict[str, list[str]]:
    settings = get_settings()
    manifests = {
        "plaid": {
            "required_env": ["PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENVIRONMENT"],
            "runtime_requirements": ["User Plaid access token from Link"],
        },
        "clover": {
            "required_env": [],
            "optional_env": ["CLOVER_ACCESS_TOKEN", "CLOVER_MERCHANT_ID", "CLOVER_ENVIRONMENT"],
            "runtime_requirements": [
                "Clover in-store activity is monitored through QuickBooks and Mercury until Platform REST access is re-enabled."
            ],
        },
        "mercury": {
            "required_env": ["MERCURY_API_TOKEN"],
            "optional_env": ["MERCURY_API_BASE", "MERCURY_ALLOWED_EGRESS_IP"],
            "runtime_requirements": [
                f"Mercury API calls must originate from whitelisted IP {settings.MERCURY_ALLOWED_EGRESS_IP}"
            ],
        },
    }
    return manifests.get(provider, {"required_env": []})


def _missing_fields(fields: dict[str, Any]) -> list[str]:
    return [name for name, value in fields.items() if not value]


def _sanitize_clover_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "access_token_received": bool(payload.get("access_token")),
        "refresh_token_received": bool(payload.get("refresh_token")),
        "token_type": payload.get("token_type"),
        "expires_in": payload.get("expires_in"),
        "access_token_expiration": payload.get("access_token_expiration"),
        "refresh_token_expiration": payload.get("refresh_token_expiration"),
        "scope": payload.get("scope"),
    }


async def _exchange_clover_code(code: str) -> dict[str, Any]:
    settings = get_settings()
    missing = _missing_fields(
        {
            "CLOVER_CLIENT_ID": settings.CLOVER_CLIENT_ID,
            "CLOVER_CLIENT_SECRET": settings.CLOVER_CLIENT_SECRET,
        }
    )
    if missing:
        raise HTTPException(status_code=503, detail=f"Clover OAuth exchange missing {', '.join(missing)}")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            _clover_token_url(),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": settings.CLOVER_CLIENT_ID,
                "client_secret": settings.CLOVER_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        logger.warning("Clover token exchange failed: %s %s", response.status_code, response.text[:300])
        raise HTTPException(status_code=502, detail="Clover token exchange failed")
    return response.json()


def _serialize_sync_run(run: EconSyncRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": str(run.id),
        "provider": run.provider,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "added_count": run.added_count,
        "modified_count": run.modified_count,
        "removed_count": run.removed_count,
        "error": run.error,
        "metadata": run.metadata_json,
    }


def _sync_provider_payload(
    provider: str,
    status: str,
    *,
    detail: str,
    updates: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": status,
        "detail": detail,
        "updates": updates,
        "metadata": metadata or {},
    }


def _diagnostic_payload(
    provider: str,
    status: str,
    *,
    detail: str,
    missing: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": status,
        "detail": detail,
        "missing": missing or [],
        "metadata": metadata or {},
        "checked_at": _now(),
    }


def _provider_error_detail(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Provider returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "Provider request timed out"
    if isinstance(exc, httpx.HTTPError):
        return "Provider request failed"
    return str(exc)[:160]


def _sync_overall_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return "skipped"
    statuses = {str(result.get("status")) for result in results}
    if statuses == {"skipped"}:
        return "skipped"
    if "error" in statuses and ("success" in statuses or "skipped" in statuses):
        return "partial"
    if "error" in statuses:
        return "error"
    return "success"


def _diagnostics_overall_status(results: list[dict[str, Any]]) -> str:
    statuses = {str(result.get("status")) for result in results}
    if "fail" in statuses:
        return "fail"
    if "blocked" in statuses and "pass" in statuses:
        return "partial"
    if statuses == {"blocked"}:
        return "blocked"
    if statuses == {"not_run"}:
        return "not_run"
    if "not_run" in statuses and "pass" in statuses:
        return "partial"
    return "pass"


def _ledger_period_bounds(period_start, period_end):
    """Resolve an optional reporting period to shop-time bounds.

    None means "all time", which is what both summaries did before they took a
    period at all, so omitting the params preserves the old behaviour exactly.
    """
    if period_start is None and period_end is None:
        return None
    end = period_end or shop_today()
    start = period_start or date(end.year, 1, 1)
    if start > end:
        raise HTTPException(status_code=400, detail="period_start must be before period_end")
    return shop_day_bounds(start, end)


async def _ledger_bucket_rows(db, bounds):
    """The provider/bucket aggregate both E-con summaries are built from.

    /ledger/summary and /payroll-tax/summary each issued this exact query
    independently, and HustlePage calls both, so the same aggregate was computed
    twice per page load and the two answers could drift apart on any edit.
    """
    query = select(
        EconLedgerEntry.provider,
        EconLedgerEntry.payroll_tax_bucket,
        func.count(EconLedgerEntry.id),
        func.coalesce(func.sum(EconLedgerEntry.amount_cents), 0),
    ).where(EconLedgerEntry.status != "removed")
    if bounds:
        query = query.where(
            EconLedgerEntry.occurred_at >= bounds[0],
            EconLedgerEntry.occurred_at <= bounds[1],
        )
    result = await db.execute(query.group_by(EconLedgerEntry.provider, EconLedgerEntry.payroll_tax_bucket))
    return result.all()


async def _ledger_contribution_rows(db, bounds):
    """Only the three columns the contribution projection reads, bounded by period.

    This was `select(EconLedgerEntry)` with no bound, loading every ledger row the
    shop has ever recorded into memory on every call to /ledger/summary.
    """
    query = select(
        EconLedgerEntry.category,
        EconLedgerEntry.amount_cents,
        EconLedgerEntry.raw,
    ).where(EconLedgerEntry.status != "removed")
    if bounds:
        query = query.where(
            EconLedgerEntry.occurred_at >= bounds[0],
            EconLedgerEntry.occurred_at <= bounds[1],
        )
    result = await db.execute(query)
    return [SimpleNamespace(category=c, amount_cents=a, raw=r) for c, a, r in result.all()]


def _ledger_tax_projection(rows: list[tuple[str, str | None, int, int]]) -> dict[str, Any]:
    buckets = [
        {
            "provider": provider,
            "bucket": bucket or "uncategorized",
            "count": count,
            "amount_cents": amount_cents,
        }
        for provider, bucket, count, amount_cents in rows
    ]
    income_cents = sum(row["amount_cents"] for row in buckets if row["bucket"] == "income")
    expense_cents = sum(row["amount_cents"] for row in buckets if row["bucket"] == "expense")
    uncategorized_cents = sum(row["amount_cents"] for row in buckets if row["bucket"] == "uncategorized")
    deductible_expense_cents = abs(min(expense_cents, 0))
    net_taxable_cents = max(income_cents - deductible_expense_cents, 0)
    estimated_tax_reserve_cents = int(net_taxable_cents * Decimal("0.25"))
    prep_status = "ready" if income_cents and not uncategorized_cents else "needs_review"
    return {
        "income_cents": income_cents,
        "expense_cents": expense_cents,
        "deductible_expense_cents": deductible_expense_cents,
        "net_taxable_cents": net_taxable_cents,
        "estimated_tax_reserve_cents": estimated_tax_reserve_cents,
        "uncategorized_cents": uncategorized_cents,
        "prep_status": prep_status,
        "buckets": buckets,
    }


def _contractor_contribution_projection(entries: list[EconLedgerEntry]) -> dict[str, int]:
    """Count only the shop-owned slice, never the full collector payment."""
    amount_cents = 0
    count = 0
    for entry in entries:
        if entry.category == "contractor_contribution":
            contribution = int(entry.amount_cents or 0)
        else:
            raw = entry.raw or {}
            if raw.get("shop_share_category") != "contractor_contribution":
                continue
            try:
                contribution = int(raw.get("shop_cut_cents") or 0)
            except (TypeError, ValueError):
                contribution = 0
        if contribution > 0:
            amount_cents += contribution
            count += 1
    return {"contractor_contributions_cents": amount_cents, "contractor_contribution_count": count}


def _now() -> datetime:
    return datetime.now(UTC)


def _money_to_cents(amount) -> int:
    try:
        return int((Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _parse_epoch_ms(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    except (TypeError, ValueError, OSError):
        return None


def _parse_iso_datetime(value) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def _get_or_create_connection(
    db: AsyncSession,
    provider: str,
    user: User,
    *,
    display_name: str,
    status: str = "connected",
    environment: str | None = None,
    external_account_id: str | None = None,
    auth_secret_ref: str | None = None,
    metadata_json: dict | None = None,
) -> EconConnection:
    result = await db.execute(
        select(EconConnection).where(
            EconConnection.provider == provider,
            EconConnection.owner_user_id == user.id,
        )
    )
    conn = result.scalar_one_or_none()
    if conn:
        conn.display_name = display_name
        conn.status = status
        conn.environment = environment
        conn.external_account_id = external_account_id or conn.external_account_id
        conn.auth_secret_ref = auth_secret_ref or conn.auth_secret_ref
        conn.metadata_json = metadata_json or conn.metadata_json
        conn.updated_at = _now()
        return conn

    conn = EconConnection(
        provider=provider,
        owner_user_id=user.id,
        display_name=display_name,
        status=status,
        environment=environment,
        external_account_id=external_account_id,
        auth_secret_ref=auth_secret_ref,
        metadata_json=metadata_json,
    )
    db.add(conn)
    await db.flush()
    return conn


async def _get_or_create_system_connection(
    db: AsyncSession,
    provider: str,
    *,
    display_name: str,
    status: str = "connected",
    environment: str | None = None,
    external_account_id: str | None = None,
    auth_secret_ref: str | None = None,
    metadata_json: dict | None = None,
) -> EconConnection:
    result = await db.execute(
        select(EconConnection).where(
            EconConnection.provider == provider,
            EconConnection.owner_user_id.is_(None),
        )
    )
    conn = result.scalar_one_or_none()
    if conn:
        conn.display_name = display_name
        conn.status = status
        conn.environment = environment
        conn.external_account_id = external_account_id or conn.external_account_id
        conn.auth_secret_ref = auth_secret_ref or conn.auth_secret_ref
        conn.metadata_json = metadata_json or conn.metadata_json
        conn.updated_at = _now()
        return conn

    conn = EconConnection(
        provider=provider,
        owner_user_id=None,
        display_name=display_name,
        status=status,
        environment=environment,
        external_account_id=external_account_id,
        auth_secret_ref=auth_secret_ref,
        metadata_json=metadata_json,
    )
    db.add(conn)
    await db.flush()
    return conn


async def _upsert_ledger_entry(db: AsyncSession, provider: str, external_id: str, fields: dict[str, Any]) -> None:
    result = await db.execute(
        select(EconLedgerEntry).where(
            EconLedgerEntry.provider == provider,
            EconLedgerEntry.external_id == external_id,
        )
    )
    entry = result.scalar_one_or_none()
    fields["updated_at"] = _now()
    if entry:
        for key, value in fields.items():
            setattr(entry, key, value)
        return
    db.add(EconLedgerEntry(provider=provider, external_id=external_id, **fields))


def _extract_clover_orders(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("elements"), list):
        return [item for item in payload["elements"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_mercury_items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return [item for item in payload[key] if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _mercury_account_summaries(conn: EconConnection | None) -> list[dict[str, Any]]:
    if not conn or not isinstance(conn.metadata_json, dict):
        return []
    accounts = conn.metadata_json.get("accounts")
    if not isinstance(accounts, list):
        return []
    summaries = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        summaries.append(
            {
                "id": account.get("id"),
                "name": account.get("name"),
                "type": account.get("type"),
                "status": account.get("status"),
            }
        )
    return summaries


def _summarize_clover_webhook_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__, "top_level_keys": [], "event_count": 0, "event_types": []}

    events = payload.get("events")
    if not isinstance(events, list):
        events = payload.get("notifications") if isinstance(payload.get("notifications"), list) else []
    event_types = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type") or event.get("eventType") or event.get("objectType")
        if event_type:
            event_types.append(str(event_type))

    merchant_id = (
        payload.get("merchantId") or payload.get("merchant_id") or payload.get("merchant") or payload.get("mId")
    )
    return {
        "payload_type": "dict",
        "top_level_keys": sorted(str(key) for key in payload)[:25],
        "merchant_id_present": bool(merchant_id),
        "event_count": len(events),
        "event_types": sorted(set(event_types))[:25],
    }


async def _record_clover_webhook_audit(db: AsyncSession, payload: Any) -> tuple[EconSyncRun, dict[str, Any]]:
    settings = get_settings()
    summary = _summarize_clover_webhook_payload(payload)
    conn = await _get_or_create_system_connection(
        db,
        "clover",
        display_name="Clover webhook ingest",
        status="webhook_active",
        environment=settings.CLOVER_ENVIRONMENT,
        external_account_id=settings.CLOVER_MERCHANT_ID or None,
        auth_secret_ref="CLOVER_ACCESS_TOKEN",
    )
    sync_run = EconSyncRun(
        provider="clover",
        connection_id=conn.id,
        status="received",
        finished_at=_now(),
        metadata_json={"kind": "webhook", **summary},
    )
    db.add(sync_run)
    await db.flush()
    return sync_run, summary


async def _persist_clover_orders(
    db: AsyncSession,
    conn: EconConnection,
    payload: Any,
) -> tuple[int, int]:
    orders = _extract_clover_orders(payload)
    for order in orders:
        external_id = order.get("id")
        if not external_id:
            continue
        total_cents = int(order.get("total") or order.get("payableAmount") or 0)
        await _upsert_ledger_entry(
            db,
            "clover",
            str(external_id),
            {
                "connection_id": conn.id,
                "entry_type": "pos_order",
                "status": str(order.get("paymentState") or order.get("state") or "posted").lower(),
                "amount_cents": total_cents,
                "currency": "USD",
                "occurred_at": _parse_epoch_ms(order.get("createdTime")),
                "description": order.get("title") or order.get("note") or f"Clover order {external_id}",
                "counterparty": order.get("customer", {}).get("firstName")
                if isinstance(order.get("customer"), dict)
                else None,
                "category": "pos",
                "payroll_tax_bucket": "income",
                "raw": order,
            },
        )
    return len(orders), len(orders)


async def _persist_mercury_accounts(db: AsyncSession, conn: EconConnection, payload: Any) -> int:
    accounts = _extract_mercury_items(payload, "accounts")
    conn.metadata_json = {
        **(conn.metadata_json or {}),
        "accounts": [
            {
                "id": acct.get("id"),
                "name": acct.get("name"),
                "type": acct.get("type"),
                "status": acct.get("status"),
            }
            for acct in accounts
        ],
    }
    conn.updated_at = _now()
    return len(accounts)


async def _persist_mercury_transactions(db: AsyncSession, conn: EconConnection, payload: Any) -> tuple[int, int]:
    txns = _extract_mercury_items(payload, "transactions")
    for txn in txns:
        external_id = txn.get("id") or txn.get("transactionId")
        if not external_id:
            continue
        amount = txn.get("amount")
        if isinstance(amount, dict):
            amount = amount.get("amount") or amount.get("value")
        amount_cents = _money_to_cents(amount)
        direction = str(txn.get("kind") or txn.get("type") or txn.get("direction") or "").lower()
        if direction in {"debit", "withdrawal", "outgoing"} and amount_cents > 0:
            amount_cents = -amount_cents
        await _upsert_ledger_entry(
            db,
            "mercury",
            str(external_id),
            {
                "connection_id": conn.id,
                "entry_type": "bank_transaction",
                "status": str(txn.get("status") or "posted").lower(),
                "amount_cents": amount_cents,
                "currency": txn.get("currency") or "USD",
                "occurred_at": _parse_iso_datetime(
                    txn.get("postedAt") or txn.get("createdAt") or txn.get("date") or txn.get("transactedAt")
                ),
                "description": txn.get("note") or txn.get("description") or txn.get("counterpartyName"),
                "counterparty": txn.get("counterpartyName") or txn.get("counterparty"),
                "category": txn.get("category"),
                "payroll_tax_bucket": "income" if amount_cents > 0 else "expense",
                "raw": txn,
            },
        )
    return len(txns), len(txns)


async def _sync_single_provider(
    provider: str,
    *,
    safe_limit: int,
    persist: bool,
    mercury_account_id: str | None,
    current_user: User,
    db: AsyncSession,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        if provider == "plaid":
            if not current_user.plaid_access_token:
                return _sync_provider_payload("plaid", "skipped", detail="Plaid bank link is not connected")
            from app.api.v1.endpoints import plaid as plaid_endpoints

            payload = await plaid_endpoints.sync_transactions(
                plaid_endpoints.PlaidTransactionsSyncRequest(count=safe_limit, persist=persist),
                db,
                current_user,
            )
            updates = len(payload["added"]) + len(payload["modified"]) + len(payload["removed"])
            return _sync_provider_payload(
                "plaid",
                "success",
                detail="Plaid transaction sync completed",
                updates=updates,
                metadata={"has_more": payload["has_more"], "persisted": payload["persisted"]},
            )

        if provider == "clover":
            if not settings.CLOVER_ACCESS_TOKEN or not settings.CLOVER_MERCHANT_ID:
                return _sync_provider_payload("clover", "skipped", detail="Clover merchant token is not configured")
            payload = await list_clover_orders(safe_limit, persist, current_user, db)
            orders = _extract_clover_orders(payload)
            return _sync_provider_payload(
                "clover",
                "success",
                detail="Clover order sync completed",
                updates=len(orders),
                metadata={"persisted": persist},
            )

        if provider == "mercury":
            if not settings.MERCURY_API_TOKEN:
                return _sync_provider_payload("mercury", "skipped", detail="Mercury API token is not configured")
            if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 99):
                return _sync_provider_payload("mercury", "skipped", detail="Mercury sync requires admin role or higher")
            account_payload = await list_mercury_accounts(current_user, db)
            transaction_payload = await list_mercury_transactions(
                safe_limit,
                None,
                None,
                mercury_account_id,
                None,
                None,
                "desc",
                persist,
                current_user,
                db,
            )
            accounts = _extract_mercury_items(account_payload, "accounts")
            transactions = _extract_mercury_items(transaction_payload, "transactions")
            return _sync_provider_payload(
                "mercury",
                "success",
                detail="Mercury account and transaction sync completed",
                updates=len(accounts) + len(transactions),
                metadata={
                    "accounts": len(accounts),
                    "transactions": len(transactions),
                    "account_id": mercury_account_id,
                    "persisted": persist,
                },
            )

        return _sync_provider_payload(provider, "skipped", detail="Unsupported E-con provider")
    except HTTPException as exc:
        return _sync_provider_payload(
            provider, "error", detail=str(exc.detail), metadata={"status_code": exc.status_code}
        )
    except Exception as exc:  # pragma: no cover - defensive provider boundary
        logger.exception("E-con provider sync failed for %s", provider)
        return _sync_provider_payload(provider, "error", detail=str(exc)[:300])


async def _diagnose_plaid(*, live: bool, current_user: User) -> dict[str, Any]:
    settings = get_settings()
    missing = _missing_fields(
        {
            "PLAID_CLIENT_ID": settings.PLAID_CLIENT_ID,
            "PLAID_SECRET": settings.PLAID_SECRET,
            "plaid_access_token": current_user.plaid_access_token,
        }
    )
    if missing:
        return _diagnostic_payload("plaid", "blocked", detail="Plaid setup is incomplete", missing=missing)
    from app.api.v1.endpoints.plaid import plaid_environment, plaid_environment_supported

    if not plaid_environment_supported():
        return _diagnostic_payload(
            "plaid",
            "blocked",
            detail=f"PLAID_ENVIRONMENT={plaid_environment()!r} is not supported. Set it to sandbox or production.",
            missing=["PLAID_ENVIRONMENT"],
        )
    if not live:
        return _diagnostic_payload("plaid", "not_run", detail="Plaid is configured. Live check was not requested.")

    try:
        from plaid import ApiException
        from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest

        from app.api.v1.endpoints.plaid import _get_plaid_client

        response = _get_plaid_client().accounts_balance_get(
            AccountsBalanceGetRequest(access_token=current_user.plaid_access_token)
        )
    except ApiException as exc:
        logger.warning("Plaid diagnostic failed for user %s: %s", current_user.id, exc.body)
        return _diagnostic_payload("plaid", "fail", detail="Plaid balance check failed")
    except Exception as exc:  # pragma: no cover - defensive provider boundary
        logger.warning("Plaid diagnostic failed for user %s: %s", current_user.id, exc)
        return _diagnostic_payload("plaid", "fail", detail=_provider_error_detail(exc))

    return _diagnostic_payload(
        "plaid",
        "pass",
        detail="Plaid balance check succeeded",
        metadata={"accounts": len(response.accounts)},
    )


async def _diagnose_clover(*, live: bool) -> dict[str, Any]:
    settings = get_settings()
    missing = _missing_fields(
        {
            "CLOVER_ACCESS_TOKEN": settings.CLOVER_ACCESS_TOKEN,
            "CLOVER_MERCHANT_ID": settings.CLOVER_MERCHANT_ID,
        }
    )
    if missing:
        return _diagnostic_payload(
            "clover",
            "not_run",
            detail="Clover Platform REST is parked. Monitor in-store activity through QuickBooks and Mercury.",
            missing=[],
            metadata={"missing_platform_fields": missing},
        )
    if not live:
        return _diagnostic_payload(
            "clover",
            "not_run",
            detail="Clover in-store POS is monitored through QuickBooks and Mercury. Platform REST live checks are optional.",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{_clover_api_base()}/merchants/{settings.CLOVER_MERCHANT_ID}/orders",
                params={"limit": 1, "orderBy": "createdTime DESC"},
                headers={"Authorization": f"Bearer {settings.CLOVER_ACCESS_TOKEN}", "Accept": "application/json"},
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning("Clover diagnostic failed: %s", _provider_error_detail(exc))
        return _diagnostic_payload("clover", "fail", detail=_provider_error_detail(exc))

    payload = response.json()
    return _diagnostic_payload(
        "clover",
        "pass",
        detail="Clover order read check succeeded",
        metadata={"orders_seen": len(_extract_clover_orders(payload))},
    )


async def _diagnose_mercury(*, live: bool) -> dict[str, Any]:
    settings = get_settings()
    missing = _missing_fields({"MERCURY_API_TOKEN": settings.MERCURY_API_TOKEN})
    if missing:
        return _diagnostic_payload("mercury", "blocked", detail="Mercury setup is incomplete", missing=missing)
    if not live:
        return _diagnostic_payload("mercury", "not_run", detail="Mercury is configured. Live check was not requested.")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.MERCURY_API_BASE.rstrip('/')}/accounts", headers=_mercury_headers())
            response.raise_for_status()
    except Exception as exc:
        logger.warning("Mercury diagnostic failed: %s", _provider_error_detail(exc))
        return _diagnostic_payload("mercury", "fail", detail=_provider_error_detail(exc))

    payload = response.json()
    return _diagnostic_payload(
        "mercury",
        "pass",
        detail="Mercury account read check succeeded",
        metadata={"accounts_seen": len(_extract_mercury_items(payload, "accounts"))},
    )


@router.get("/integrations/status")
async def get_econ_integrations_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Return E-con rail readiness without exposing secrets."""
    _require_role(current_user, "assistant")
    settings = get_settings()

    plaid_missing = _missing_fields(
        {
            "PLAID_CLIENT_ID": settings.PLAID_CLIENT_ID,
            "PLAID_SECRET": settings.PLAID_SECRET,
            "plaid_access_token": current_user.plaid_access_token,
        }
    )
    clover_missing = _missing_fields(
        {
            "CLOVER_ACCESS_TOKEN": settings.CLOVER_ACCESS_TOKEN,
            "CLOVER_MERCHANT_ID": settings.CLOVER_MERCHANT_ID,
        }
    )
    clover_oauth_missing = _missing_fields(
        {
            "CLOVER_CLIENT_ID": settings.CLOVER_CLIENT_ID,
            "CLOVER_REDIRECT_URI": settings.CLOVER_REDIRECT_URI,
        }
    )
    mercury_missing = _missing_fields({"MERCURY_API_TOKEN": settings.MERCURY_API_TOKEN})

    clover_configured = not clover_missing
    clover_oauth_ready = bool(settings.CLOVER_CLIENT_ID and settings.CLOVER_REDIRECT_URI)
    mercury_configured = not mercury_missing
    connection_result = await db.execute(select(EconConnection).where(EconConnection.owner_user_id == current_user.id))
    connections = {conn.provider: conn for conn in connection_result.scalars().all()}
    sync_result = await db.execute(
        select(EconSyncRun)
        .join(EconConnection, EconConnection.id == EconSyncRun.connection_id)
        .where(or_(EconConnection.owner_user_id == current_user.id, EconConnection.owner_user_id.is_(None)))
        .order_by(desc(EconSyncRun.started_at))
        .limit(25)
    )
    latest_syncs: dict[str, EconSyncRun] = {}
    for run in sync_result.scalars().all():
        latest_syncs.setdefault(run.provider, run)

    return {
        "plaid": {
            "configured": bool(settings.PLAID_CLIENT_ID and settings.PLAID_SECRET),
            "connected": bool(current_user.plaid_access_token),
            "ready_to_sync": not plaid_missing,
            "missing": plaid_missing,
            "setup": _provider_setup_manifest("plaid"),
            "last_synced_at": connections.get("plaid").last_synced_at if connections.get("plaid") else None,
            "latest_sync": _serialize_sync_run(latest_syncs.get("plaid")),
            "detail": "Bank link and balance read are available. Transaction sync is on demand.",
        },
        "clover": {
            "configured": clover_configured,
            "oauth_ready": clover_oauth_ready,
            "environment": settings.CLOVER_ENVIRONMENT,
            "merchant_configured": bool(settings.CLOVER_MERCHANT_ID),
            "ready_to_sync": False,
            "missing": [],
            "oauth_missing": clover_oauth_missing,
            "setup": _provider_setup_manifest("clover"),
            "last_synced_at": connections.get("clover").last_synced_at if connections.get("clover") else None,
            "latest_sync": _serialize_sync_run(latest_syncs.get("clover")),
            "detail": "Clover stays in-store for now. Eco monitors Clover activity through QuickBooks records and Mercury deposits instead of direct POS order import.",
        },
        "mercury": {
            "configured": mercury_configured,
            "api_base": settings.MERCURY_API_BASE,
            "ready_to_sync": not mercury_missing,
            "missing": mercury_missing,
            "setup": _provider_setup_manifest("mercury"),
            "accounts": _mercury_account_summaries(connections.get("mercury")),
            "last_synced_at": connections.get("mercury").last_synced_at if connections.get("mercury") else None,
            "latest_sync": _serialize_sync_run(latest_syncs.get("mercury")),
            "detail": "Mercury account and transaction reads are available when an API token is configured.",
        },
        "payroll_tax": _redact_connector_status(
            configured=True,
            detail="Payroll and tax prep now read from POS plus normalized E-con ledger entries.",
        ),
    }


@router.get("/integrations/diagnostics")
async def get_econ_integrations_diagnostics(
    providers: list[str] | None = Query(default=None),
    live: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
):
    """Check Plaid, Clover, and Mercury setup, with optional live read tests."""
    _require_role(current_user, "assistant")
    requested = providers or ["plaid", "clover", "mercury"]
    normalized = []
    for provider in requested:
        value = provider.strip().lower()
        if value and value not in normalized:
            normalized.append(value)

    results = []
    for provider in normalized:
        if provider == "plaid":
            results.append(await _diagnose_plaid(live=live, current_user=current_user))
        elif provider == "clover":
            results.append(await _diagnose_clover(live=live))
        elif provider == "mercury":
            if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 99):
                results.append(
                    _diagnostic_payload(
                        "mercury",
                        "blocked",
                        detail="Mercury diagnostics require admin role or higher",
                    )
                )
            else:
                results.append(await _diagnose_mercury(live=live))
        else:
            results.append(_diagnostic_payload(provider, "blocked", detail="Unsupported E-con provider"))

    return {
        "status": _diagnostics_overall_status(results),
        "live": live,
        "results": results,
    }


@router.post("/sync")
async def sync_econ_integrations(
    body: EconSyncRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Sync configured read-only E-con rails into the normalized ledger."""
    _require_role(current_user, "assistant")
    requested = body.providers or ["plaid", "mercury"]
    providers = []
    for provider in requested:
        normalized = provider.strip().lower()
        if normalized and normalized not in providers:
            providers.append(normalized)

    safe_limit = max(1, min(body.limit, 100))
    results = [
        await _sync_single_provider(
            provider,
            safe_limit=safe_limit,
            persist=body.persist,
            mercury_account_id=body.mercury_account_id,
            current_user=current_user,
            db=db,
        )
        for provider in providers
    ]
    return {
        "status": _sync_overall_status(results),
        "results": results,
    }


@router.get("/sync-runs")
async def list_econ_sync_runs(
    provider: str | None = None,
    limit: int = 25,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Return recent Plaid, Clover, and Mercury sync attempts for audit."""
    _require_role(current_user, "assistant")
    safe_limit = max(1, min(limit, 100))
    query = (
        select(EconSyncRun)
        .join(EconConnection, EconConnection.id == EconSyncRun.connection_id)
        .where(or_(EconConnection.owner_user_id == current_user.id, EconConnection.owner_user_id.is_(None)))
        .order_by(desc(EconSyncRun.started_at))
        .limit(safe_limit)
    )
    if provider:
        query = query.where(EconSyncRun.provider == provider.strip().lower())
    result = await db.execute(query)
    return [_serialize_sync_run(run) for run in result.scalars().all()]


@router.get("/clover/oauth-url")
async def get_clover_oauth_url(
    current_user: User = Depends(get_current_user),
):
    """Build the Clover OAuth URL for dashboard setup."""
    _require_role(current_user, "admin")
    settings = get_settings()
    if not settings.CLOVER_CLIENT_ID:
        raise HTTPException(status_code=503, detail="CLOVER_CLIENT_ID is not configured")

    query = urlencode(
        {
            "client_id": settings.CLOVER_CLIENT_ID,
            "redirect_uri": settings.CLOVER_REDIRECT_URI,
            "response_type": "code",
            "state": str(current_user.id),
        }
    )
    return {"authorization_url": f"{_clover_web_base()}/oauth/v2/authorize?{query}"}


@router.get("/clover/callback")
async def clover_oauth_callback(
    code: str | None = None,
    merchant_id: str | None = None,
    client_id: str | None = None,
    state: UUID | None = None,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Receive Clover OAuth redirects.

    Exchanges the authorization code and records only sanitized token metadata.
    Raw Clover tokens are not returned or stored in E-con rows.
    """
    logger.info(
        "Clover OAuth callback received: code_present=%s merchant_id_present=%s client_id_present=%s",
        bool(code),
        bool(merchant_id),
        bool(client_id),
    )
    conn = None
    if state:
        user_result = await db.execute(select(User).where(User.id == state))
        user = user_result.scalar_one_or_none()
        if user and ROLE_RANK.get(user.role, 0) >= ROLE_RANK.get("admin", 99):
            token_metadata: dict[str, Any] | None = None
            status = "oauth_received"
            if code:
                token_payload = await _exchange_clover_code(code)
                token_metadata = _sanitize_clover_token_payload(token_payload)
                status = "oauth_exchanged"
            conn = await _get_or_create_connection(
                db,
                "clover",
                user,
                display_name="Clover POS",
                status=status,
                environment=get_settings().CLOVER_ENVIRONMENT,
                external_account_id=merchant_id,
                auth_secret_ref="CLOVER_ACCESS_TOKEN",
                metadata_json={
                    "code_received": bool(code),
                    "client_id": client_id,
                    "token_exchange": token_metadata,
                },
            )
    return {
        "received": True,
        "merchant_id": merchant_id,
        "connection_id": str(conn.id) if conn else None,
        "token_exchanged": bool(conn and conn.metadata_json and conn.metadata_json.get("token_exchange")),
        "next_step": "Configure CLOVER_ACCESS_TOKEN and CLOVER_MERCHANT_ID in the server secret store.",
    }


@router.get("/clover/orders")
async def list_clover_orders(
    limit: int = 25,
    persist: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Read recent Clover orders for E-con reconciliation."""
    _require_role(current_user, "assistant")
    settings = get_settings()
    if not settings.CLOVER_ACCESS_TOKEN or not settings.CLOVER_MERCHANT_ID:
        raise HTTPException(status_code=503, detail="Clover merchant token is not configured")

    safe_limit = max(1, min(limit, 100))
    conn = await _get_or_create_connection(
        db,
        "clover",
        current_user,
        display_name="Clover POS",
        status="connected",
        environment=settings.CLOVER_ENVIRONMENT,
        external_account_id=settings.CLOVER_MERCHANT_ID,
        auth_secret_ref="CLOVER_ACCESS_TOKEN",
    )
    sync_run = EconSyncRun(provider="clover", connection_id=conn.id, status="started")
    db.add(sync_run)
    await db.flush()
    url = f"{_clover_api_base()}/merchants/{settings.CLOVER_MERCHANT_ID}/orders"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url,
                params={"limit": safe_limit, "orderBy": "createdTime DESC"},
                headers={"Authorization": f"Bearer {settings.CLOVER_ACCESS_TOKEN}", "Accept": "application/json"},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = exc.response.text[:2000]
        logger.warning("Clover order fetch failed: %s", exc.response.text[:300])
        raise HTTPException(status_code=502, detail="Clover order fetch failed") from exc
    except httpx.HTTPError as exc:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = str(exc)[:2000]
        logger.warning("Clover order fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Clover order fetch failed") from exc

    payload = response.json()
    added_count = modified_count = 0
    if persist:
        added_count, modified_count = await _persist_clover_orders(db, conn, payload)
        conn.last_synced_at = _now()
        conn.updated_at = _now()
    sync_run.status = "success"
    sync_run.finished_at = _now()
    sync_run.added_count = added_count
    sync_run.modified_count = modified_count
    sync_run.metadata_json = {"persisted": persist, "limit": safe_limit}

    return payload


@router.post("/clover/webhook", status_code=200)
async def clover_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """
    Acknowledge Clover webhook delivery.

    Clover expects a quick 200. This stores a sanitized sync-run audit record
    so webhook delivery is visible in E-con without storing raw payload data.
    """
    payload = await request.json()
    sync_run, summary = await _record_clover_webhook_audit(db, payload)
    logger.info("Clover webhook received: summary=%s sync_run=%s", summary, sync_run.id)
    return {"received": True, "sync_run_id": str(sync_run.id), "summary": summary}


@router.get("/mercury/accounts")
async def list_mercury_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Read Mercury accounts for E-con cash position."""
    _require_role(current_user, "admin")
    settings = get_settings()
    if not settings.MERCURY_API_TOKEN:
        raise HTTPException(status_code=503, detail="MERCURY_API_TOKEN is not configured")

    conn = await _get_or_create_connection(
        db,
        "mercury",
        current_user,
        display_name="Mercury banking",
        status="connected",
        environment="production",
        auth_secret_ref="MERCURY_API_TOKEN",
    )
    sync_run = EconSyncRun(
        provider="mercury", connection_id=conn.id, status="started", metadata_json={"kind": "accounts"}
    )
    db.add(sync_run)
    await db.flush()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{settings.MERCURY_API_BASE.rstrip('/')}/accounts", headers=_mercury_headers())
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = exc.response.text[:2000]
        logger.warning("Mercury account fetch failed: %s", exc.response.text[:300])
        raise HTTPException(status_code=502, detail="Mercury account fetch failed") from exc
    except httpx.HTTPError as exc:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = str(exc)[:2000]
        logger.warning("Mercury account fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Mercury account fetch failed") from exc

    payload = response.json()
    account_count = await _persist_mercury_accounts(db, conn, payload)
    conn.last_synced_at = _now()
    sync_run.status = "success"
    sync_run.finished_at = _now()
    sync_run.added_count = account_count
    sync_run.metadata_json = {"kind": "accounts", "persisted": True}
    return payload


@router.get("/mercury/transactions")
async def list_mercury_transactions(
    limit: int = 25,
    start: str | None = None,
    end: str | None = None,
    account_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    order: str = "desc",
    persist: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Read recent Mercury transactions for E-con reconciliation."""
    _require_role(current_user, "admin")
    settings = get_settings()
    if not settings.MERCURY_API_TOKEN:
        raise HTTPException(status_code=503, detail="MERCURY_API_TOKEN is not configured")

    params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    if search:
        params["search"] = search
    if status:
        params["status"] = status
    if account_id and order in {"asc", "desc"}:
        params["order"] = order

    conn = await _get_or_create_connection(
        db,
        "mercury",
        current_user,
        display_name="Mercury banking",
        status="connected",
        environment="production",
        auth_secret_ref="MERCURY_API_TOKEN",
    )
    sync_run = EconSyncRun(
        provider="mercury",
        connection_id=conn.id,
        status="started",
        metadata_json={"kind": "transactions", "account_id": account_id},
    )
    db.add(sync_run)
    await db.flush()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                _mercury_transactions_url(account_id),
                params=params,
                headers=_mercury_headers(),
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = exc.response.text[:2000]
        logger.warning("Mercury transaction fetch failed: %s", exc.response.text[:300])
        raise HTTPException(status_code=502, detail="Mercury transaction fetch failed") from exc
    except httpx.HTTPError as exc:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = str(exc)[:2000]
        logger.warning("Mercury transaction fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Mercury transaction fetch failed") from exc

    payload = response.json()
    added_count = modified_count = 0
    if persist:
        added_count, modified_count = await _persist_mercury_transactions(db, conn, payload)
        conn.last_synced_at = _now()
        conn.updated_at = _now()
    sync_run.status = "success"
    sync_run.finished_at = _now()
    sync_run.added_count = added_count
    sync_run.modified_count = modified_count
    sync_run.metadata_json = {"kind": "transactions", "persisted": persist, "params": params}
    return payload


@router.get("/ledger")
async def list_ledger_entries(
    provider: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Return normalized E-con ledger entries for finance review."""
    _require_role(current_user, "assistant")
    safe_limit = max(1, min(limit, 200))
    query = select(EconLedgerEntry).order_by(desc(EconLedgerEntry.occurred_at), desc(EconLedgerEntry.created_at))
    if provider:
        query = query.where(EconLedgerEntry.provider == provider)
    result = await db.execute(query.limit(safe_limit))
    rows = result.scalars().all()
    return [
        {
            "id": str(row.id),
            "provider": row.provider,
            "external_id": row.external_id,
            "entry_type": row.entry_type,
            "status": row.status,
            "amount_cents": row.amount_cents,
            "currency": row.currency,
            "occurred_at": row.occurred_at,
            "description": row.description,
            "counterparty": row.counterparty,
            "category": row.category,
            "payroll_tax_bucket": row.payroll_tax_bucket,
        }
        for row in rows
    ]


@router.get("/ledger/summary")
async def get_ledger_summary(
    period_start: date | None = Query(None, description="Shop-time start date; omit for all time"),
    period_end: date | None = Query(None, description="Shop-time end date; omit for all time"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Summarize normalized E-con entries for gamified finance and tax prep.

    Shares its aggregate with /payroll-tax/summary and returns that projection
    whole, so the two endpoints cannot disagree about the same period. The
    response is a superset of what this returned before.
    """
    _require_role(current_user, "assistant")
    bounds = _ledger_period_bounds(period_start, period_end)
    rows = await _ledger_bucket_rows(db, bounds)
    contributions = _contractor_contribution_projection(await _ledger_contribution_rows(db, bounds))
    return {**_ledger_tax_projection(rows), **contributions}


@router.get("/payroll-tax/summary")
async def get_payroll_tax_summary(
    period_start: date | None = Query(None, description="Shop-time start date; omit for all time"),
    period_end: date | None = Query(None, description="Shop-time end date; omit for all time"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Project payroll and tax prep totals from normalized E-con entries.

    Same aggregate, same period semantics as /ledger/summary.
    """
    _require_role(current_user, "assistant")
    return _ledger_tax_projection(await _ledger_bucket_rows(db, _ledger_period_bounds(period_start, period_end)))
