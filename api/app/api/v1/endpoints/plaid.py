"""
Plaid endpoints -- bank account linking, balance retrieval.

Uses the plaid-python SDK to communicate with Plaid's API.
Each user stores their own plaid_access_token on the ryozan_users row.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import plaid
from fastapi import APIRouter, Depends, HTTPException
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import ROLE_RANK, get_current_user
from app.models.econ import EconConnection, EconLedgerEntry, EconSyncRun
from app.models.user import User

logger = logging.getLogger("ryozan.plaid")
router = APIRouter(prefix="/plaid", tags=["plaid"])


# ── Plaid client factory ────────────────────────────────────────────


# The environments the pinned plaid-python defines. Plaid shut down its
# Development environment in 2024 and the SDK no longer has it.
PLAID_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def plaid_environment() -> str:
    """PLAID_ENVIRONMENT, normalized; blank means sandbox."""
    return (get_settings().PLAID_ENVIRONMENT or "").strip().lower() or "sandbox"


def plaid_environment_supported() -> bool:
    return plaid_environment() in PLAID_HOSTS


def _plaid_host() -> str:
    """API host for PLAID_ENVIRONMENT, or a 503 that names the setting."""
    environment = plaid_environment()
    host = PLAID_HOSTS.get(environment)
    if host is None:
        logger.error("PLAID_ENVIRONMENT=%r is not supported; use sandbox or production", environment)
        raise HTTPException(
            status_code=503,
            detail=f"PLAID_ENVIRONMENT={environment!r} is not supported. Set it to sandbox or production.",
        )
    return host


def _get_plaid_client() -> plaid_api.PlaidApi:
    """Build a PlaidApi client from current settings."""
    settings = get_settings()
    host = _plaid_host()

    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": settings.PLAID_CLIENT_ID,
            "secret": settings.PLAID_SECRET,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def _require_role(user: User, min_role: str):
    if ROLE_RANK.get(user.role, 0) < ROLE_RANK.get(min_role, 99):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")


# ── Request/response schemas ────────────────────────────────────────


class ExchangeTokenRequest(BaseModel):
    public_token: str


class PlaidTransactionsSyncRequest(BaseModel):
    cursor: str | None = None
    count: int = 100
    persist: bool = True


def _now() -> datetime:
    return datetime.now(UTC)


def _money_to_cents(amount) -> int:
    try:
        return int((Decimal(str(amount)) * Decimal("100")).quantize(Decimal("1")))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _parse_plaid_date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)
    except ValueError:
        return None


async def _get_or_create_plaid_connection(db: AsyncSession, user: User) -> EconConnection:
    result = await db.execute(
        select(EconConnection).where(
            EconConnection.provider == "plaid",
            EconConnection.owner_user_id == user.id,
        )
    )
    conn = result.scalar_one_or_none()
    settings = get_settings()
    if conn:
        conn.status = "connected"
        conn.environment = settings.PLAID_ENVIRONMENT
        conn.updated_at = _now()
        return conn

    conn = EconConnection(
        provider="plaid",
        owner_user_id=user.id,
        display_name=f"Plaid bank link for {user.email}",
        status="connected",
        environment=settings.PLAID_ENVIRONMENT,
        auth_secret_ref="ryozan_users.plaid_access_token",
    )
    db.add(conn)
    await db.flush()
    return conn


async def _upsert_plaid_ledger_entry(db: AsyncSession, conn: EconConnection, txn: dict) -> None:
    external_id = txn.get("transaction_id") or txn.get("pending_transaction_id")
    if not external_id:
        return

    amount_cents = -_money_to_cents(txn.get("amount"))
    date_value = txn.get("datetime") or txn.get("date")
    category = None
    if isinstance(txn.get("personal_finance_category"), dict):
        category = txn["personal_finance_category"].get("primary")
    elif isinstance(txn.get("category"), list) and txn["category"]:
        category = txn["category"][0]

    result = await db.execute(
        select(EconLedgerEntry).where(
            EconLedgerEntry.provider == "plaid",
            EconLedgerEntry.external_id == external_id,
        )
    )
    entry = result.scalar_one_or_none()
    fields = {
        "connection_id": conn.id,
        "entry_type": "bank_transaction",
        "status": "pending" if txn.get("pending") else "posted",
        "amount_cents": amount_cents,
        "currency": txn.get("iso_currency_code") or "USD",
        "occurred_at": _parse_plaid_date(date_value),
        "description": txn.get("name") or txn.get("merchant_name"),
        "counterparty": txn.get("merchant_name"),
        "category": category,
        "payroll_tax_bucket": "income" if amount_cents > 0 else "expense",
        "raw": txn,
        "updated_at": _now(),
    }
    if entry:
        for key, value in fields.items():
            setattr(entry, key, value)
        return

    db.add(EconLedgerEntry(provider="plaid", external_id=external_id, **fields))


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/balance")
async def get_balance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return Plaid account balances for the current user.
    Requires the user to have linked a bank account via Plaid Link.
    """
    if not current_user.plaid_access_token:
        raise HTTPException(status_code=400, detail="No bank account linked. Complete Plaid Link first.")

    client = _get_plaid_client()

    try:
        request = AccountsBalanceGetRequest(access_token=current_user.plaid_access_token)
        response = client.accounts_balance_get(request)
    except plaid.ApiException as e:
        logger.error("Plaid balance fetch failed for user %s: %s", current_user.id, e.body)
        raise HTTPException(status_code=502, detail="Failed to fetch balances from Plaid") from e

    accounts = []
    for acct in response.accounts:
        accounts.append(
            {
                "account_id": acct.account_id,
                "name": acct.name,
                "official_name": acct.official_name,
                "type": str(acct.type),
                "subtype": str(acct.subtype) if acct.subtype else None,
                "mask": acct.mask,
                "balances": {
                    "available": acct.balances.available,
                    "current": acct.balances.current,
                    "limit": acct.balances.limit,
                    "iso_currency_code": acct.balances.iso_currency_code,
                },
            }
        )

    return {"accounts": accounts}


@router.post("/transactions/sync")
async def sync_transactions(
    body: PlaidTransactionsSyncRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch Plaid transaction updates for E-con.

    Uses the stored E-con cursor by default and persists normalized ledger rows
    for payroll/tax prep. Caller-supplied cursor is still accepted for replay.
    """
    _require_role(current_user, "assistant")
    if not current_user.plaid_access_token:
        raise HTTPException(status_code=400, detail="No bank account linked. Complete Plaid Link first.")

    client = _get_plaid_client()
    conn = await _get_or_create_plaid_connection(db, current_user)
    sync_run = EconSyncRun(provider="plaid", connection_id=conn.id, status="started")
    db.add(sync_run)
    await db.flush()

    try:
        request = TransactionsSyncRequest(
            access_token=current_user.plaid_access_token,
            cursor=body.cursor or conn.sync_cursor,
            count=max(1, min(body.count, 500)),
        )
        response = client.transactions_sync(request)
    except plaid.ApiException as e:
        sync_run.status = "error"
        sync_run.finished_at = _now()
        sync_run.error = str(e.body)[:2000]
        logger.error("Plaid transaction sync failed for user %s: %s", current_user.id, e.body)
        raise HTTPException(status_code=502, detail="Failed to sync transactions from Plaid") from e

    added = [txn.to_dict() for txn in response.added]
    modified = [txn.to_dict() for txn in response.modified]
    removed = [txn.to_dict() for txn in response.removed]

    if body.persist:
        for txn in added + modified:
            await _upsert_plaid_ledger_entry(db, conn, txn)
        for txn in removed:
            external_id = txn.get("transaction_id")
            if not external_id:
                continue
            result = await db.execute(
                select(EconLedgerEntry).where(
                    EconLedgerEntry.provider == "plaid",
                    EconLedgerEntry.external_id == external_id,
                )
            )
            entry = result.scalar_one_or_none()
            if entry:
                entry.status = "removed"
                entry.updated_at = _now()

        conn.sync_cursor = response.next_cursor
        conn.last_synced_at = _now()
        conn.updated_at = _now()

    sync_run.status = "success"
    sync_run.finished_at = _now()
    sync_run.added_count = len(added)
    sync_run.modified_count = len(modified)
    sync_run.removed_count = len(removed)
    sync_run.metadata_json = {"has_more": response.has_more, "persisted": body.persist}

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "next_cursor": response.next_cursor,
        "has_more": response.has_more,
        "persisted": body.persist,
    }


@router.post("/link-token")
async def create_link_token(
    current_user: User = Depends(get_current_user),
):
    """
    Create a Plaid Link token so the frontend can open Plaid Link
    and let the user connect their bank account.
    """
    settings = get_settings()
    client = _get_plaid_client()

    try:
        request = LinkTokenCreateRequest(
            user=LinkTokenCreateRequestUser(client_user_id=str(current_user.id)),
            client_name=settings.APP_NAME,
            products=[Products("transactions")],
            country_codes=[CountryCode("US")],
            language="en",
        )
        response = client.link_token_create(request)
    except plaid.ApiException as e:
        logger.error("Plaid link token creation failed: %s", e.body)
        raise HTTPException(status_code=502, detail="Failed to create Plaid link token") from e

    return {
        "link_token": response.link_token,
        "expiration": response.expiration,
    }


@router.post("/exchange-token")
async def exchange_public_token(
    body: ExchangeTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Exchange a public_token from Plaid Link for a permanent access_token.
    Stores the access_token on the user record for future balance/transaction calls.
    """
    client = _get_plaid_client()

    try:
        request = ItemPublicTokenExchangeRequest(public_token=body.public_token)
        response = client.item_public_token_exchange(request)
    except plaid.ApiException as e:
        logger.error("Plaid token exchange failed for user %s: %s", current_user.id, e.body)
        raise HTTPException(status_code=502, detail="Failed to exchange Plaid token") from e

    # Store the access token on the user
    current_user.plaid_access_token = response.access_token
    conn = await _get_or_create_plaid_connection(db, current_user)
    conn.external_account_id = response.item_id
    conn.sync_cursor = None
    conn.last_synced_at = None
    conn.metadata_json = {"item_id": response.item_id}
    conn.updated_at = _now()
    await db.flush()

    return {
        "connected": True,
        "item_id": response.item_id,
    }


@router.get("/status")
async def get_plaid_status(
    current_user: User = Depends(get_current_user),
):
    """Check whether the current user has a Plaid bank account linked."""
    return {
        "connected": current_user.plaid_access_token is not None,
    }


@router.delete("/disconnect", status_code=204)
async def disconnect_plaid(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the stored Plaid access token for the current user."""
    current_user.plaid_access_token = None
    result = await db.execute(
        select(EconConnection).where(
            EconConnection.provider == "plaid",
            EconConnection.owner_user_id == current_user.id,
        )
    )
    conn = result.scalar_one_or_none()
    if conn:
        conn.status = "disconnected"
        conn.updated_at = _now()
    await db.flush()
    return None
