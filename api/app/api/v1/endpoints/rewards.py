"""Collector rewards, wallet receipts, and inventory endpoints."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.reward import RewardGrant, RewardOffer
from app.models.user import User
from app.schemas.reward import (
    BTTCoinAwardRequest,
    BTTCoinSpendRequest,
    BTTCoinTransactionListResponse,
    BTTCoinTransactionResponse,
    BTTCoinWalletResponse,
    PortalActivityResponse,
    RewardGrantResponse,
    RewardInventoryResponse,
    RewardOfferCompleteResponse,
    RewardOfferResponse,
)
from app.services.reward_engine import complete_reward_offer, grant_configured_reward, grant_reward

router = APIRouter(prefix="/rewards", tags=["rewards"])
wallet_router = APIRouter(prefix="/bttcoin", tags=["bttcoin"])


def _transaction(grant: RewardGrant, balance_after: int) -> BTTCoinTransactionResponse:
    tx_type = "spend" if grant.coin_delta < 0 else "earn"
    return BTTCoinTransactionResponse(
        id=grant.id,
        user_id=grant.user_id,
        amount=abs(grant.coin_delta),
        type=tx_type,
        source=grant.event_type,
        description=grant.description,
        balance_after=balance_after,
        created_at=grant.created_at,
    )


async def _transactions_with_balances(db: AsyncSession, user: User) -> list[BTTCoinTransactionResponse]:
    rows = (
        (
            await db.execute(
                select(RewardGrant)
                .where(RewardGrant.user_id == user.id, RewardGrant.coin_delta != 0)
                .order_by(RewardGrant.created_at, RewardGrant.id)
            )
        )
        .scalars()
        .all()
    )
    running = (user.bttcoin_balance or 0) - sum(row.coin_delta for row in rows)
    transactions = []
    for row in rows:
        running += row.coin_delta
        transactions.append(_transaction(row, running))
    return list(reversed(transactions))


@router.post("/portal-activity", response_model=PortalActivityResponse)
async def record_portal_activity(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Award one modest portal-use reward per collector per UTC day."""
    if current_user.role != "collector":
        raise HTTPException(status_code=403, detail="Collector reward only")
    source_id = datetime.now(UTC).date().isoformat()
    grant, awarded = await grant_configured_reward(db, current_user, "portal.daily_visit", source_id)
    return PortalActivityResponse(
        awarded=awarded,
        grant=RewardGrantResponse.model_validate(grant),
        new_quest_points=current_user.quest_points or 0,
        new_bttcoin_balance=current_user.bttcoin_balance or 0,
    )


@router.get("/inventory", response_model=RewardInventoryResponse)
async def get_reward_inventory(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    grants = (
        (
            await db.execute(
                select(RewardGrant)
                .where(RewardGrant.user_id == current_user.id, RewardGrant.unlock_kind.is_not(None))
                .order_by(desc(RewardGrant.created_at))
            )
        )
        .scalars()
        .all()
    )
    offers = (
        (
            await db.execute(
                select(RewardOffer).where(RewardOffer.user_id == current_user.id).order_by(desc(RewardOffer.created_at))
            )
        )
        .scalars()
        .all()
    )
    return RewardInventoryResponse(
        grants=[RewardGrantResponse.model_validate(item) for item in grants],
        offers=[RewardOfferResponse.model_validate(item) for item in offers],
        total_unlocks=len(grants),
    )


@router.post("/offers/{offer_id}/complete", response_model=RewardOfferCompleteResponse)
async def complete_offer(
    offer_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Redeem one server-issued minigame opportunity after the game reports completion."""
    try:
        offer, grant = await complete_reward_offer(db, current_user, offer_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RewardOfferCompleteResponse(
        offer=RewardOfferResponse.model_validate(offer),
        grant=RewardGrantResponse.model_validate(grant),
        new_quest_points=current_user.quest_points or 0,
        new_bttcoin_balance=current_user.bttcoin_balance or 0,
    )


@wallet_router.get("/wallet", response_model=BTTCoinWalletResponse)
async def get_wallet(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    transactions = await _transactions_with_balances(db, current_user)
    total_earned = sum(tx.amount for tx in transactions if tx.type != "spend")
    total_spent = sum(tx.amount for tx in transactions if tx.type == "spend")
    return BTTCoinWalletResponse(
        balance=current_user.bttcoin_balance or 0,
        total_earned=total_earned,
        total_spent=total_spent,
        transactions=transactions,
    )


@wallet_router.get("/transactions", response_model=BTTCoinTransactionListResponse)
async def list_transactions(
    transaction_type: str | None = Query(default=None, alias="type", pattern="^(earn|spend|bonus|refund)$"),
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    transactions = await _transactions_with_balances(db, current_user)
    if transaction_type:
        transactions = [item for item in transactions if item.type == transaction_type]
    total = len(transactions)
    return BTTCoinTransactionListResponse(transactions=transactions[skip : skip + limit], total=total)


@wallet_router.post("/award", response_model=BTTCoinTransactionResponse)
async def award_bttcoin(
    body: BTTCoinAwardRequest,
    db: AsyncSession = Depends(get_db),
    _caller: User = Depends(require_role("admin")),
):
    user = await db.get(User, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    source_id = f"{body.user_id}:{datetime.now(UTC).isoformat()}"
    grant, _ = await grant_reward(
        db,
        user,
        event_type="admin.award",
        source_id=source_id,
        xp=0,
        coin=body.amount,
        description=body.description,
    )
    return _transaction(grant, user.bttcoin_balance or 0)


@wallet_router.post("/spend", response_model=BTTCoinTransactionResponse)
async def spend_bttcoin(
    body: BTTCoinSpendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    locked_user = await db.scalar(select(User).where(User.id == current_user.id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=404, detail="User not found")
    if (locked_user.bttcoin_balance or 0) < body.amount:
        raise HTTPException(status_code=409, detail="Insufficient RYO balance")
    source_id = f"{locked_user.id}:{datetime.now(UTC).isoformat()}"
    grant, _ = await grant_reward(
        db,
        locked_user,
        event_type="wallet.spend",
        source_id=source_id,
        xp=0,
        coin=-body.amount,
        description=body.description,
    )
    return _transaction(grant, locked_user.bttcoin_balance or 0)
