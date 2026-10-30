"""
Hustle E-con connector persistence tests.

These cover the provider-normalization contract for Plaid, Clover, and Mercury.
Existing API tests focus on auth, routing, Courier, and inbound email, so E-con
gets its own file to keep payment ledger regressions visible.
"""

import os
import unittest
import uuid
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException

from app.api.v1.endpoints import econ, plaid
from app.models.econ import EconConnection, EconLedgerEntry, EconSyncRun


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []
        self.flushes = 0

    async def execute(self, _stmt):
        if self.results:
            return self.results.pop(0)
        return _ScalarResult(None)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()


def _connection(provider: str = "econ") -> EconConnection:
    return EconConnection(
        id=uuid.uuid4(),
        provider=provider,
        owner_user_id=uuid.uuid4(),
        display_name=f"{provider} connection",
        status="connected",
    )


class EconConnectorPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_clover_token_payload_sanitizer_never_returns_raw_tokens(self):
        sanitized = econ._sanitize_clover_token_payload(
            {
                "access_token": "secret-access",
                "refresh_token": "secret-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "access_token_expiration": 1781289600,
                "refresh_token_expiration": 1812825600,
                "scope": "READ_ORDERS",
            }
        )

        self.assertNotIn("access_token", sanitized)
        self.assertNotIn("refresh_token", sanitized)
        self.assertEqual(sanitized["access_token_received"], True)
        self.assertEqual(sanitized["refresh_token_received"], True)
        self.assertEqual(sanitized["token_type"], "Bearer")

    async def test_clover_exchange_requires_client_secret_before_network_call(self):
        class _Settings:
            CLOVER_CLIENT_ID = "client"
            CLOVER_CLIENT_SECRET = ""

        with (
            patch("app.api.v1.endpoints.econ.get_settings", return_value=_Settings()),
            self.assertRaises(HTTPException) as ctx,
        ):
            await econ._exchange_clover_code("code")

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("CLOVER_CLIENT_SECRET", str(ctx.exception.detail))

    async def test_missing_fields_returns_only_unset_setup_fields(self):
        self.assertEqual(
            econ._missing_fields(
                {
                    "PLAID_CLIENT_ID": "client",
                    "PLAID_SECRET": "",
                    "plaid_access_token": None,
                    "CLOVER_MERCHANT_ID": "merchant",
                }
            ),
            ["PLAID_SECRET", "plaid_access_token"],
        )

    async def test_provider_setup_manifest_lists_railway_variable_contract(self):
        class _Settings:
            MERCURY_ALLOWED_EGRESS_IP = "152.55.180.240"

        self.assertEqual(
            econ._provider_setup_manifest("clover")["required_env"],
            [],
        )
        self.assertEqual(
            econ._provider_setup_manifest("clover")["optional_env"],
            ["CLOVER_ACCESS_TOKEN", "CLOVER_MERCHANT_ID", "CLOVER_ENVIRONMENT"],
        )
        with patch("app.api.v1.endpoints.econ.get_settings", return_value=_Settings()):
            mercury_setup = econ._provider_setup_manifest("mercury")
        self.assertEqual(mercury_setup["required_env"], ["MERCURY_API_TOKEN"])
        self.assertIn("152.55.180.240", mercury_setup["runtime_requirements"][0])
        self.assertIn("PLAID_SECRET", econ._provider_setup_manifest("plaid")["required_env"])

    async def test_mercury_transactions_url_can_target_account_scope(self):
        class _Settings:
            MERCURY_API_BASE = "https://api.mercury.com/api/v1/"

        with patch("app.api.v1.endpoints.econ.get_settings", return_value=_Settings()):
            self.assertEqual(
                econ._mercury_transactions_url(),
                "https://api.mercury.com/api/v1/transactions",
            )
            self.assertEqual(
                econ._mercury_transactions_url("acct-1"),
                "https://api.mercury.com/api/v1/account/acct-1/transactions",
            )

    async def test_mercury_account_summaries_never_returns_sensitive_fields(self):
        conn = _connection("mercury")
        conn.metadata_json = {
            "accounts": [
                {
                    "id": "acct-1",
                    "name": "Operating",
                    "type": "checking",
                    "status": "active",
                    "accountNumber": "should-not-be-returned",
                },
                "invalid",
            ],
        }

        self.assertEqual(
            econ._mercury_account_summaries(conn),
            [
                {
                    "id": "acct-1",
                    "name": "Operating",
                    "type": "checking",
                    "status": "active",
                }
            ],
        )

    async def test_clover_webhook_summary_keeps_operational_shape_only(self):
        summary = econ._summarize_clover_webhook_payload(
            {
                "merchantId": "merchant-1",
                "events": [
                    {"type": "ORDER_CREATED", "objectId": "order-1", "customerEmail": "client@example.com"},
                    {"eventType": "PAYMENT_CREATED", "objectId": "payment-1"},
                ],
                "secretLikeField": "should-not-be-copied",
            }
        )

        self.assertEqual(summary["payload_type"], "dict")
        self.assertEqual(summary["merchant_id_present"], True)
        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["event_types"], ["ORDER_CREATED", "PAYMENT_CREATED"])
        self.assertIn("secretLikeField", summary["top_level_keys"])
        self.assertNotIn("should-not-be-copied", str(summary))
        self.assertNotIn("client@example.com", str(summary))

    async def test_clover_webhook_audit_creates_system_connection_and_sync_run(self):
        class _Settings:
            CLOVER_ENVIRONMENT = "sandbox"
            CLOVER_MERCHANT_ID = "merchant-1"

        db = _FakeSession()

        with patch("app.api.v1.endpoints.econ.get_settings", return_value=_Settings()):
            sync_run, summary = await econ._record_clover_webhook_audit(
                db,
                {"merchantId": "merchant-1", "events": [{"type": "ORDER_CREATED"}]},
            )

        connections = [value for value in db.added if isinstance(value, EconConnection)]
        sync_runs = [value for value in db.added if isinstance(value, EconSyncRun)]
        self.assertEqual(len(connections), 1)
        self.assertEqual(len(sync_runs), 1)
        self.assertEqual(connections[0].owner_user_id, None)
        self.assertEqual(connections[0].provider, "clover")
        self.assertEqual(connections[0].status, "webhook_active")
        self.assertEqual(sync_run.provider, "clover")
        self.assertEqual(sync_run.status, "received")
        self.assertEqual(sync_run.metadata_json["kind"], "webhook")
        self.assertEqual(summary["event_count"], 1)

    async def test_sync_overall_status_classifies_provider_results(self):
        self.assertEqual(econ._sync_overall_status([]), "skipped")
        self.assertEqual(
            econ._sync_overall_status([
                econ._sync_provider_payload("plaid", "success", detail="ok"),
                econ._sync_provider_payload("clover", "skipped", detail="missing token"),
            ]),
            "success",
        )
        self.assertEqual(
            econ._sync_overall_status([
                econ._sync_provider_payload("plaid", "success", detail="ok"),
                econ._sync_provider_payload("mercury", "error", detail="provider failed"),
            ]),
            "partial",
        )
        self.assertEqual(
            econ._sync_overall_status([
                econ._sync_provider_payload("plaid", "skipped", detail="not linked"),
                econ._sync_provider_payload("clover", "skipped", detail="missing token"),
            ]),
            "skipped",
        )
        self.assertEqual(
            econ._sync_overall_status([
                econ._sync_provider_payload("mercury", "error", detail="provider failed"),
            ]),
            "error",
        )

    async def test_diagnostics_overall_status_classifies_results(self):
        self.assertEqual(
            econ._diagnostics_overall_status([
                econ._diagnostic_payload("plaid", "not_run", detail="setup only"),
                econ._diagnostic_payload("clover", "not_run", detail="setup only"),
            ]),
            "not_run",
        )
        self.assertEqual(
            econ._diagnostics_overall_status([
                econ._diagnostic_payload("plaid", "pass", detail="ok"),
                econ._diagnostic_payload("clover", "blocked", detail="missing"),
            ]),
            "partial",
        )
        self.assertEqual(
            econ._diagnostics_overall_status([
                econ._diagnostic_payload("mercury", "fail", detail="provider failed"),
            ]),
            "fail",
        )

    async def test_ledger_tax_projection_summarizes_prep_totals(self):
        summary = econ._ledger_tax_projection(
            [
                ("clover", "income", 2, 120000),
                ("mercury", "expense", 1, -20000),
                ("plaid", None, 1, -5000),
            ]
        )

        self.assertEqual(summary["income_cents"], 120000)
        self.assertEqual(summary["expense_cents"], -20000)
        self.assertEqual(summary["deductible_expense_cents"], 20000)
        self.assertEqual(summary["net_taxable_cents"], 100000)
        self.assertEqual(summary["estimated_tax_reserve_cents"], 25000)
        self.assertEqual(summary["uncategorized_cents"], -5000)
        self.assertEqual(summary["prep_status"], "needs_review")

    async def test_contractor_projection_counts_shop_slices_and_cash_rent_only(self):
        entries = [
            EconLedgerEntry(
                provider="stripe",
                external_id="pi_split",
                entry_type="payment",
                status="posted",
                amount_cents=20_000,
                category="pos_payment",
                raw={"shop_share_category": "contractor_contribution", "shop_cut_cents": "6000"},
            ),
            EconLedgerEntry(
                provider="manual",
                external_id="rent_cash",
                entry_type="payment",
                status="posted",
                amount_cents=130_000,
                category="contractor_contribution",
                raw={"contribution_type": "booth_rent"},
            ),
            EconLedgerEntry(
                provider="stripe",
                external_id="pi_booth_artist",
                entry_type="payment",
                status="posted",
                amount_cents=20_000,
                category="pos_payment",
                raw={"shop_share_category": "", "shop_cut_cents": "0"},
            ),
        ]

        self.assertEqual(
            econ._contractor_contribution_projection(entries),
            {"contractor_contributions_cents": 136_000, "contractor_contribution_count": 2},
        )

    async def test_clover_diagnostic_reports_missing_setup_without_network_call(self):
        class _Settings:
            CLOVER_ACCESS_TOKEN = ""
            CLOVER_MERCHANT_ID = "merchant"

        with patch("app.api.v1.endpoints.econ.get_settings", return_value=_Settings()):
            result = await econ._diagnose_clover(live=True)

        self.assertEqual(result["provider"], "clover")
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["metadata"]["missing_platform_fields"], ["CLOVER_ACCESS_TOKEN"])

    async def test_mercury_diagnostic_reports_configured_setup_without_live_call(self):
        class _Settings:
            MERCURY_API_TOKEN = "token"

        with patch("app.api.v1.endpoints.econ.get_settings", return_value=_Settings()):
            result = await econ._diagnose_mercury(live=False)

        self.assertEqual(result["provider"], "mercury")
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["missing"], [])

    async def test_sync_run_serialization_redacts_to_operational_status(self):
        run = EconSyncRun(
            id=uuid.uuid4(),
            provider="clover",
            status="success",
            added_count=3,
            modified_count=2,
            removed_count=1,
            metadata_json={"persisted": True},
        )

        payload = econ._serialize_sync_run(run)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["provider"], "clover")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["added_count"], 3)
        self.assertEqual(payload["modified_count"], 2)
        self.assertEqual(payload["removed_count"], 1)
        self.assertEqual(payload["metadata"], {"persisted": True})

    async def test_plaid_transaction_persists_bank_ledger_entry(self):
        db = _FakeSession()
        conn = _connection("plaid")

        await plaid._upsert_plaid_ledger_entry(
            db,
            conn,
            {
                "transaction_id": "plaid-txn-1",
                "amount": 12.34,
                "date": "2026-06-13",
                "name": "Needle supply",
                "merchant_name": "Tattoo Supply Co",
                "iso_currency_code": "USD",
                "pending": False,
                "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"},
            },
        )

        entry = next(value for value in db.added if isinstance(value, EconLedgerEntry))
        self.assertEqual(entry.provider, "plaid")
        self.assertEqual(entry.external_id, "plaid-txn-1")
        self.assertEqual(entry.entry_type, "bank_transaction")
        self.assertEqual(entry.amount_cents, -1234)
        self.assertEqual(entry.payroll_tax_bucket, "expense")
        self.assertEqual(entry.counterparty, "Tattoo Supply Co")
        self.assertEqual(entry.category, "GENERAL_MERCHANDISE")

    async def test_clover_orders_persist_income_ledger_entry(self):
        db = _FakeSession()
        conn = _connection("clover")

        added_count, modified_count = await econ._persist_clover_orders(
            db,
            conn,
            {
                "elements": [
                    {
                        "id": "clover-order-1",
                        "total": 22500,
                        "createdTime": 1781289600000,
                        "title": "Walk-in tattoo",
                        "paymentState": "PAID",
                        "customer": {"firstName": "Client"},
                    }
                ]
            },
        )

        entry = next(value for value in db.added if isinstance(value, EconLedgerEntry))
        self.assertEqual((added_count, modified_count), (1, 1))
        self.assertEqual(entry.provider, "clover")
        self.assertEqual(entry.external_id, "clover-order-1")
        self.assertEqual(entry.entry_type, "pos_order")
        self.assertEqual(entry.status, "paid")
        self.assertEqual(entry.amount_cents, 22500)
        self.assertEqual(entry.payroll_tax_bucket, "income")
        self.assertEqual(entry.counterparty, "Client")

    async def test_mercury_transactions_persist_income_and_expense_entries(self):
        db = _FakeSession()
        conn = _connection("mercury")

        added_count, modified_count = await econ._persist_mercury_transactions(
            db,
            conn,
            {
                "transactions": [
                    {
                        "id": "mercury-credit-1",
                        "amount": "800.00",
                        "currency": "USD",
                        "postedAt": "2026-06-13T12:00:00Z",
                        "description": "Deposit",
                        "counterpartyName": "Stripe",
                        "status": "posted",
                    },
                    {
                        "id": "mercury-debit-1",
                        "amount": "45.67",
                        "currency": "USD",
                        "postedAt": "2026-06-13T13:00:00Z",
                        "description": "Tax software",
                        "counterpartyName": "Accounting Tool",
                        "kind": "debit",
                        "status": "posted",
                    },
                ]
            },
        )

        entries = [value for value in db.added if isinstance(value, EconLedgerEntry)]
        by_id = {entry.external_id: entry for entry in entries}
        self.assertEqual((added_count, modified_count), (2, 2))
        self.assertEqual(by_id["mercury-credit-1"].amount_cents, 80000)
        self.assertEqual(by_id["mercury-credit-1"].payroll_tax_bucket, "income")
        self.assertEqual(by_id["mercury-debit-1"].amount_cents, -4567)
        self.assertEqual(by_id["mercury-debit-1"].payroll_tax_bucket, "expense")

    async def test_mercury_accounts_store_sanitized_connection_metadata(self):
        db = _FakeSession()
        conn = _connection("mercury")

        account_count = await econ._persist_mercury_accounts(
            db,
            conn,
            {
                "accounts": [
                    {
                        "id": "acct-1",
                        "name": "Operating",
                        "type": "checking",
                        "status": "active",
                        "accountNumber": "should-not-be-stored",
                    }
                ]
            },
        )

        self.assertEqual(account_count, 1)
        self.assertEqual(
            conn.metadata_json,
            {
                "accounts": [
                    {
                        "id": "acct-1",
                        "name": "Operating",
                        "type": "checking",
                        "status": "active",
                    }
                ]
            },
        )


if __name__ == "__main__":
    unittest.main()
