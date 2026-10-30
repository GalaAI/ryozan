"""Focused tests for the collector reward engine."""

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from app.models.reward import RewardGrant
from app.models.user import BTTCOUser
from app.services.reward_engine import REWARD_RULES, grant_reward, profile_is_complete, reward_source_key


def _collector() -> BTTCOUser:
    return BTTCOUser(
        id=uuid.uuid4(),
        email="collector@example.com",
        password_hash="test",
        name="Collector",
        role="collector",
        bttcoin_balance=0,
        quest_points=0,
        reasons=["tattoos"],
        avatar_url="https://cdn.example/avatar.webp",
    )


class RewardRuleTests(unittest.TestCase):
    def test_reward_catalog_covers_verified_client_actions(self):
        self.assertEqual(
            set(REWARD_RULES),
            {
                "portal.daily_visit",
                "profile.completed",
                "booking.inquiry_submitted",
                "booking.deposit_paid",
                "appointment.completed",
                "conversation.artist_interaction",
            },
        )
        self.assertGreater(REWARD_RULES["appointment.completed"].xp, REWARD_RULES["portal.daily_visit"].xp)
        self.assertGreater(REWARD_RULES["booking.deposit_paid"].coin, REWARD_RULES["booking.inquiry_submitted"].coin)

    def test_profile_completion_requires_identity_avatar_and_reason(self):
        user = _collector()
        self.assertTrue(profile_is_complete(user))
        user.avatar_url = None
        self.assertFalse(profile_is_complete(user))

    def test_source_key_is_stable(self):
        self.assertEqual(
            reward_source_key("booking.deposit_paid", "booking-1"),
            "booking.deposit_paid:booking-1",
        )


class RewardGrantTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_receipt_returns_existing_without_balance_update(self):
        user = _collector()
        existing = RewardGrant(
            id=uuid.uuid4(),
            user_id=user.id,
            event_type="booking.deposit_paid",
            source_id="booking-1",
            idempotency_key="booking.deposit_paid:booking-1",
            xp_delta=50,
            coin_delta=20,
            description="Paid a booking deposit",
            metadata_json={},
        )
        db = AsyncMock()
        db.scalar.side_effect = [None, existing]

        grant, awarded = await grant_reward(
            db,
            user,
            event_type="booking.deposit_paid",
            source_id="booking-1",
            xp=50,
            coin=20,
            description="Paid a booking deposit",
        )

        self.assertIs(grant, existing)
        self.assertFalse(awarded)
        db.execute.assert_not_awaited()

    @patch("app.services.reward_engine._maybe_issue_minigame_offer", new_callable=AsyncMock)
    @patch("app.services.reward_engine._maybe_attach_unlock", new_callable=AsyncMock)
    async def test_new_receipt_uses_atomic_balance_update(self, attach_unlock, issue_offer):
        user = _collector()
        inserted_id = uuid.uuid4()
        inserted = RewardGrant(
            id=inserted_id,
            user_id=user.id,
            event_type="quest.claimed",
            source_id="quest-1",
            idempotency_key="quest.claimed:quest-1",
            xp_delta=25,
            coin_delta=5,
            description="Claimed quest",
            metadata_json={},
        )
        db = AsyncMock()
        db.scalar.return_value = inserted_id
        db.get.return_value = inserted

        grant, awarded = await grant_reward(
            db,
            user,
            event_type="quest.claimed",
            source_id="quest-1",
            xp=25,
            coin=5,
            description="Claimed quest",
        )

        self.assertIs(grant, inserted)
        self.assertTrue(awarded)
        db.execute.assert_awaited_once()
        db.refresh.assert_awaited_once_with(user, attribute_names=["quest_points", "bttcoin_balance"])
        attach_unlock.assert_awaited_once_with(db, inserted)
        issue_offer.assert_awaited_once_with(db, inserted)


if __name__ == "__main__":
    unittest.main()
