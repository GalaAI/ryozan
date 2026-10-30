"""Schemas for the BTTCO rewards ledger and collector inventory."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RewardGrantResponse(BaseModel):
    id: UUID
    user_id: UUID
    event_type: str
    source_id: str
    xp_delta: int
    coin_delta: int
    description: str
    unlock_kind: str | None = None
    unlock_slug: str | None = None
    metadata_json: dict = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class RewardOfferResponse(BaseModel):
    id: UUID
    offer_type: str
    game_slug: str
    reward_kind: str
    reward_slug: str
    status: str
    expires_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RewardInventoryResponse(BaseModel):
    grants: list[RewardGrantResponse]
    offers: list[RewardOfferResponse]
    total_unlocks: int


class PortalActivityResponse(BaseModel):
    awarded: bool
    grant: RewardGrantResponse
    new_quest_points: int
    new_bttcoin_balance: int


class RewardOfferCompleteResponse(BaseModel):
    offer: RewardOfferResponse
    grant: RewardGrantResponse
    new_quest_points: int
    new_bttcoin_balance: int


class BTTCoinTransactionResponse(BaseModel):
    id: UUID
    user_id: UUID
    amount: int
    type: str
    source: str
    description: str
    balance_after: int
    created_at: datetime


class BTTCoinWalletResponse(BaseModel):
    balance: int
    total_earned: int
    total_spent: int
    transactions: list[BTTCoinTransactionResponse]
    conversion_rate: int = 100


class BTTCoinTransactionListResponse(BaseModel):
    transactions: list[BTTCoinTransactionResponse]
    total: int


class BTTCoinAwardRequest(BaseModel):
    user_id: UUID
    amount: int = Field(gt=0, le=100000)
    description: str = Field(min_length=1, max_length=500)


class BTTCoinSpendRequest(BaseModel):
    amount: int = Field(gt=0, le=100000)
    description: str = Field(min_length=1, max_length=500)
