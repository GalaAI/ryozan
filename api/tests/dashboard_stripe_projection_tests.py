"""Stripe projections keep platform and connected-artist balances distinct."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import stripe

from app.api.v1.endpoints.dashboard import _stripe_balance_projection
from app.api.v1.endpoints.stripe_connect import (
    CONNECT_PLATFORM_PROFILE_CODE,
    _account_link_configurations,
    _artist_connect_projection,
    _connect_onboarding_error,
    _create_account_link,
    _create_connected_account,
    _ensure_customer_configuration,
    admin_create_connect_account,
    create_connect_account,
    get_connect_status,
    list_artist_stripe_status,
)


def _user(
    *,
    role: str,
    account_id: str | None = None,
    complete: bool = False,
    commission_rate: int | None = 70,
):
    return SimpleNamespace(
        id="user-1",
        role=role,
        email="artist@example.com",
        name="Artist Example",
        slug="artist-example",
        commission_rate=commission_rate,
        stripe_account_id=account_id,
        stripe_onboarding_complete=complete,
    )


def _balance():
    return SimpleNamespace(
        available=[SimpleNamespace(amount=12_300, currency="usd")],
        pending=[SimpleNamespace(amount=4_500, currency="usd")],
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Db:
    def __init__(self, value):
        self.value = value
        self.statement = None
        self.flushes = 0

    async def execute(self, statement):
        self.statement = statement
        return _ScalarResult(self.value)

    async def flush(self):
        self.flushes += 1


@patch("app.api.v1.endpoints.dashboard._init_stripe")
@patch("app.api.v1.endpoints.dashboard.stripe.Payout.list")
@patch("app.api.v1.endpoints.dashboard.stripe.Balance.retrieve")
def test_founder_reads_platform_balance(balance_retrieve, payout_list, _init):
    balance_retrieve.return_value = _balance()
    payout_list.return_value = SimpleNamespace(data=[])

    result = _stripe_balance_projection(_user(role="founder"))

    balance_retrieve.assert_called_once_with()
    payout_list.assert_called_once_with(limit=1, status="pending")
    assert result["connected"] is True
    assert result["account_scope"] == "platform"
    assert result["available_balance"] == 12_300
    assert result["pending_balance"] == 4_500


@patch("app.api.v1.endpoints.dashboard._init_stripe")
@patch("app.api.v1.endpoints.dashboard.stripe.Payout.list")
@patch("app.api.v1.endpoints.dashboard.stripe.Balance.retrieve")
def test_artist_reads_only_completed_connected_account(balance_retrieve, payout_list, _init):
    balance_retrieve.return_value = _balance()
    payout_list.return_value = SimpleNamespace(data=[])

    result = _stripe_balance_projection(_user(role="artist", account_id="acct_artist", complete=True))

    balance_retrieve.assert_called_once_with(stripe_account="acct_artist")
    payout_list.assert_called_once_with(limit=1, status="pending", stripe_account="acct_artist")
    assert result["connected"] is True
    assert result["account_scope"] == "artist"


@patch("app.api.v1.endpoints.dashboard.stripe.Balance.retrieve")
def test_incomplete_artist_never_reads_provider_balance(balance_retrieve):
    result = _stripe_balance_projection(_user(role="artist", account_id="acct_artist", complete=False))

    balance_retrieve.assert_not_called()
    assert result["connected"] is False
    assert result["account_scope"] == "artist"
    assert result["available_balance"] is None
    assert result["pending_balance"] is None


@patch("app.api.v1.endpoints.dashboard._init_stripe")
@patch("app.api.v1.endpoints.dashboard.stripe.Balance.retrieve", side_effect=RuntimeError("offline"))
def test_failed_provider_read_returns_unknown_money(balance_retrieve, _init):
    result = _stripe_balance_projection(_user(role="founder"))

    balance_retrieve.assert_called_once_with()
    assert result["connected"] is False
    assert result["available_balance"] is None
    assert result["pending_balance"] is None


@patch("app.api.v1.endpoints.stripe_connect._init_stripe")
def test_connected_account_uses_accounts_v2_merchant_configuration(init_stripe):
    account_create = init_stripe.return_value.v2.core.accounts.create
    account_create.return_value = SimpleNamespace(id="acct_artist")

    _create_connected_account(_user(role="artist"))

    params = account_create.call_args.args[0]
    assert "type" not in params
    assert "business_type" not in params
    assert params["dashboard"] == "full"
    assert params["defaults"]["responsibilities"] == {
        "fees_collector": "stripe",
        "losses_collector": "stripe",
    }
    assert set(params["configuration"]) == {"merchant", "customer"}


def test_account_link_configurations_preserve_all_supported_applied_configurations():
    account = {
        "applied_configurations": ["recipient", "customer", "merchant", "recipient", "unsupported"],
    }

    assert _account_link_configurations(account) == ["recipient", "customer", "merchant"]


@patch("app.api.v1.endpoints.stripe_connect._init_stripe")
def test_account_link_uses_the_accounts_exact_applied_configurations(init_stripe):
    account_link_create = init_stripe.return_value.v2.core.account_links.create
    configurations = ["recipient", "customer", "merchant"]

    _create_account_link(
        "acct_artist",
        configurations=configurations,
        refresh_url="https://example.test/refresh",
        return_url="https://example.test/return",
    )

    params = account_link_create.call_args.args[0]
    assert params["use_case"]["account_onboarding"]["configurations"] == configurations


@patch("app.api.v1.endpoints.stripe_connect._init_stripe")
@patch("app.api.v1.endpoints.stripe_connect._retrieve_connected_account")
def test_existing_account_preserves_recipient_configuration_for_onboarding(account_retrieve, init_stripe):
    account_retrieve.return_value = {
        "applied_configurations": ["recipient", "customer", "merchant"],
    }

    configurations = _ensure_customer_configuration("acct_artist")

    assert configurations == ["recipient", "customer", "merchant"]
    init_stripe.return_value.v2.core.accounts.update.assert_not_called()


@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
@patch("app.api.v1.endpoints.stripe_connect._retrieve_connected_account")
def test_artist_projection_uses_live_payout_state(account_retrieve, _configured):
    account_retrieve.return_value = {
        "configuration": {
            "merchant": {
                "capabilities": {
                    "card_payments": {"status": "active"},
                    "stripe_balance": {"payouts": {"status": "inactive"}},
                }
            }
        },
        "requirements": {"summary": {"minimum_deadline": {"status": "currently_due"}}},
    }

    result = _artist_connect_projection(_user(role="artist", account_id="acct_artist", complete=True))

    assert result["provider_state"] == "live"
    assert result["charges_enabled"] is True
    assert result["payouts_enabled"] is False
    assert result["routing_ready"] is False
    assert result["requirements_due"] == 1


@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
@patch("app.api.v1.endpoints.stripe_connect._retrieve_connected_account")
def test_connect_status_clears_stale_readiness_when_payouts_are_disabled(account_retrieve, _configured):
    artist = _user(role="artist", account_id="acct_artist", complete=True)
    db = _Db(artist)
    account_retrieve.return_value = {
        "configuration": {
            "merchant": {
                "capabilities": {
                    "card_payments": {"status": "active"},
                    "stripe_balance": {"payouts": {"status": "inactive"}},
                }
            }
        }
    }

    result = asyncio.run(get_connect_status(db=db, current_user=artist))

    assert result["routing_ready"] is False
    assert result["onboarding_complete"] is False
    assert result["provider_state"] == "live"
    assert result["requirements_due"] == 0
    assert artist.stripe_onboarding_complete is False
    assert db.flushes == 1


@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
@patch("app.api.v1.endpoints.stripe_connect._retrieve_connected_account")
def test_connect_status_reports_current_accounts_v2_requirements(account_retrieve, _configured):
    artist = _user(role="artist", account_id="acct_artist", complete=False)
    account_retrieve.return_value = {
        "configuration": {
            "merchant": {
                "capabilities": {
                    "card_payments": {"status": "inactive"},
                    "stripe_balance": {"payouts": {"status": "inactive"}},
                }
            }
        },
        "requirements": {"summary": {"minimum_deadline": {"status": "past_due"}}},
    }

    result = asyncio.run(get_connect_status(db=_Db(artist), current_user=artist))

    assert result["connected"] is True
    assert result["provider_state"] == "live"
    assert result["requirements_due"] == 1


@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=False)
def test_connect_status_distinguishes_unconfigured_provider_from_disconnected_account(_configured):
    artist = _user(role="artist")

    result = asyncio.run(get_connect_status(db=_Db(artist), current_user=artist))

    assert result["connected"] is False
    assert result["provider_state"] == "unavailable"
    assert result["requirements_due"] == 0


@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
def test_connect_status_reports_not_connected_when_provider_is_available(_configured):
    artist = _user(role="artist")

    result = asyncio.run(get_connect_status(db=_Db(artist), current_user=artist))

    assert result["connected"] is False
    assert result["provider_state"] == "not_connected"
    assert result["requirements_due"] == 0


@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=False)
def test_connect_roster_includes_founder_without_changing_role(_configured):
    founder = _user(role="founder", commission_rate=None)
    artist = _user(role="artist")
    artist.id = "user-2"
    db = _Db([founder, artist])

    result = asyncio.run(list_artist_stripe_status(db=db, current_user=founder))

    assert [item["role"] for item in result] == ["founder", "artist"]
    assert result[0]["commission_rate"] == 0
    assert founder.role == "founder"
    assert "IN" in str(db.statement)


@patch("app.api.v1.endpoints.stripe_connect._create_account_link")
@patch("app.api.v1.endpoints.stripe_connect._create_connected_account")
@patch("app.api.v1.endpoints.stripe_connect._retrieve_connected_account")
@patch("app.api.v1.endpoints.stripe_connect.get_settings")
@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
@patch("app.api.v1.endpoints.stripe_connect._init_stripe")
def test_founder_can_start_admin_connect_onboarding(
    _init,
    _configured,
    settings,
    retrieve_account,
    create_account,
    create_link,
):
    founder = _user(role="founder")
    db = _Db(founder)
    settings.return_value = SimpleNamespace(STRIPE_CONNECT_RETURN_URL="https://example.test/dashboard")
    create_account.return_value = SimpleNamespace(id="acct_founder")
    retrieve_account.return_value = {
        "applied_configurations": ["customer", "merchant", "recipient"],
    }
    create_link.return_value = SimpleNamespace(url="https://connect.stripe.test/onboard")

    result = asyncio.run(admin_create_connect_account(founder.id, db=db, current_user=founder))

    assert result == {
        "url": "https://connect.stripe.test/onboard",
        "account_id": "acct_founder",
    }
    assert founder.role == "founder"
    assert founder.stripe_account_id == "acct_founder"
    assert db.flushes == 1
    retrieve_account.assert_called_once_with("acct_founder")
    create_link.assert_called_once_with(
        "acct_founder",
        configurations=["customer", "merchant", "recipient"],
        refresh_url=("https://example.test/dashboard?portal=hub&workflow=connections&stripe=refresh"),
        return_url=("https://example.test/dashboard?portal=hub&workflow=connections&stripe=return"),
    )


@patch("app.api.v1.endpoints.stripe_connect._ensure_customer_configuration")
@patch("app.api.v1.endpoints.stripe_connect._create_account_link")
@patch("app.api.v1.endpoints.stripe_connect.get_settings")
@patch("app.api.v1.endpoints.stripe_connect._stripe_configured", return_value=True)
def test_expired_onboarding_returns_to_connections_and_requests_a_fresh_link(
    _configured,
    settings,
    create_link,
    ensure_customer,
):
    artist = _user(role="artist", account_id="acct_artist")
    db = _Db(artist)
    settings.return_value = SimpleNamespace(STRIPE_CONNECT_RETURN_URL="https://example.test/dashboard?source=stripe")
    ensure_customer.return_value = ["recipient", "customer", "merchant"]
    create_link.side_effect = [
        SimpleNamespace(url="https://connect.stripe.test/first"),
        SimpleNamespace(url="https://connect.stripe.test/fresh"),
    ]

    first = asyncio.run(create_connect_account(db=db, current_user=artist))
    refreshed = asyncio.run(create_connect_account(db=db, current_user=artist))

    assert first["url"] == "https://connect.stripe.test/first"
    assert refreshed["url"] == "https://connect.stripe.test/fresh"
    assert create_link.call_count == 2
    assert [record.args for record in ensure_customer.call_args_list] == [
        ("acct_artist",),
        ("acct_artist",),
    ]
    create_link.assert_called_with(
        "acct_artist",
        configurations=["recipient", "customer", "merchant"],
        refresh_url=("https://example.test/dashboard?source=stripe&portal=hub&workflow=connections&stripe=refresh"),
        return_url=("https://example.test/dashboard?source=stripe&portal=hub&workflow=connections&stripe=return"),
    )


def test_incomplete_platform_profile_returns_actionable_safe_error():
    provider_error = stripe.error.InvalidRequestError(
        "You must complete your platform profile to use Connect and create live connected accounts.",
        param=None,
    )

    error = _connect_onboarding_error(provider_error)

    assert error.status_code == 409
    assert error.detail == {
        "code": CONNECT_PLATFORM_PROFILE_CODE,
        "message": "Complete Ryozan's Stripe Connect platform profile before starting artist onboarding.",
    }


def test_other_connect_provider_errors_do_not_expose_provider_details():
    provider_error = stripe.error.InvalidRequestError(
        "private provider diagnostic",
        param=None,
    )

    error = _connect_onboarding_error(provider_error)

    assert error.status_code == 502
    assert "private provider diagnostic" not in str(error.detail)
