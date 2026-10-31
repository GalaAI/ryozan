import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import set_committed_value

from app.api.v1.endpoints import email_templates
from app.models.user import User
from app.services.email_foundry import (
    FOUNDRY_STATE_DEFAULTS,
    _probe_foundry_connector,
    create_foundry_unsubscribe_token,
    dispatch_foundry_audience_trigger,
    dispatch_foundry_trigger,
    foundry_connector_inventory,
    process_foundry_campaigns,
    process_foundry_trigger_queue,
)


# The pre-existing template tests exercise rendered email delivery. This file
# owns the separate persistence contract required by the locked Foundry adapter.
class EmailFoundryStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = User(
            id=uuid.uuid4(),
            email="founder@example.com",
            name="Founder",
            role="founder",
        )
        self.db = AsyncMock()

    async def test_state_hydrates_all_locked_store_keys(self):
        state = await email_templates.get_foundry_state(current_user=self.user)

        self.assertEqual(set(state), set(FOUNDRY_STATE_DEFAULTS))
        self.assertEqual(state["bttco-foundry-custom-v1"], [])

    async def test_state_round_trips_exact_adapter_value(self):
        value = {"booking-confirmation": {"client_name": "Justin"}}

        result = await email_templates.put_foundry_state(
            "bttco-foundry-fields-v1",
            value,
            db=self.db,
            current_user=self.user,
        )
        hydrated = await email_templates.get_foundry_state(current_user=self.user)

        self.assertTrue(result["saved"])
        self.assertEqual(result["key"], "bttco-foundry-fields-v1")
        self.assertEqual(result["value"], value)
        self.assertEqual(hydrated["bttco-foundry-fields-v1"], value)
        self.db.flush.assert_awaited_once()

    async def test_state_rejects_unknown_keys(self):
        with self.assertRaises(HTTPException) as raised:
            await email_templates.put_foundry_state(
                "invented-key",
                {},
                db=self.db,
                current_user=self.user,
            )

        self.assertEqual(raised.exception.status_code, 400)

    async def test_provider_connect_and_disconnect_persist_boolean_state_and_receipts(self):
        with (
            patch("app.services.email_foundry._foundry_connector_configured", return_value=True),
            patch(
                "app.services.email_foundry._probe_foundry_connector",
                new_callable=AsyncMock,
                return_value=(True, "live proof"),
            ),
        ):
            connected = await email_templates.update_foundry_connector(
                "sendgrid",
                "connect",
                db=self.db,
                current_user=self.user,
            )
            disconnected = await email_templates.update_foundry_connector(
                "sendgrid",
                "disconnect",
                db=self.db,
                current_user=self.user,
            )

        self.assertTrue(connected["ok"])
        self.assertTrue(connected["connector"]["connected"])
        self.assertEqual(connected["receipt"]["detail"], "live proof")
        self.assertTrue(disconnected["ok"])
        self.assertFalse(disconnected["connector"]["connected"])
        self.assertEqual(disconnected["connector"]["state"], "configured")
        stored = self.user.nexus_profile["email_foundry_state"]["bttco-foundry-connectors-v1"]
        self.assertFalse(stored["sendgrid"]["enabled"])

    async def test_second_provider_connect_marks_profile_dirty_for_the_orm(self):
        # Production regression: once any connector row existed in the stored
        # profile, every later connect returned connected=true but never
        # reached Postgres, because the nested dict was mutated in place and
        # the reassigned JSONB compared equal to its committed value.
        # A profile that has been written once holds every locked key, exactly
        # as store_foundry_state leaves it in Postgres.
        committed_state = {
            key: (default.copy() if isinstance(default, (dict, list)) else default)
            for key, default in FOUNDRY_STATE_DEFAULTS.items()
        }
        committed_state["bttco-foundry-connectors-v1"] = {
            "cloudflare-r2": {
                "enabled": True,
                "lastTestAt": "2026-08-30T00:47:53+00:00",
                "lastTestPassed": True,
            }
        }
        set_committed_value(self.user, "nexus_profile", {"email_foundry_state": committed_state})
        with (
            patch("app.services.email_foundry._foundry_connector_configured", return_value=True),
            patch(
                "app.services.email_foundry._probe_foundry_connector",
                new=AsyncMock(return_value=(True, "Twilio account probe returned HTTP 200")),
            ),
        ):
            result = await email_templates.update_foundry_connector(
                "twilio",
                "connect",
                db=self.db,
                current_user=self.user,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["connector"]["connected"])
        history = sa_inspect(self.user).attrs.nexus_profile.history
        self.assertTrue(history.has_changes(), "nexus_profile must be flagged dirty or the UPDATE is skipped")
        stored = self.user.nexus_profile["email_foundry_state"]["bttco-foundry-connectors-v1"]
        self.assertTrue(stored["twilio"]["enabled"])
        self.assertTrue(stored["cloudflare-r2"]["enabled"])
        self.db.flush.assert_awaited_once()

    async def test_state_snapshot_does_not_alias_orm_profile(self):
        set_committed_value(
            self.user,
            "nexus_profile",
            {"email_foundry_state": {"bttco-foundry-connectors-v1": {"cloudflare-r2": {"enabled": True}}}},
        )
        from app.services.email_foundry import foundry_state_for_user

        snapshot = foundry_state_for_user(self.user)
        snapshot["bttco-foundry-connectors-v1"]["cloudflare-r2"]["enabled"] = False

        self.assertTrue(
            self.user.nexus_profile["email_foundry_state"]["bttco-foundry-connectors-v1"]["cloudflare-r2"]["enabled"]
        )

    async def test_provider_connect_reports_missing_ryozan_credential_without_exposing_values(self):
        with patch("app.services.email_foundry._foundry_connector_configured", return_value=False):
            result = await email_templates.update_foundry_connector(
                "resend",
                "connect",
                db=self.db,
                current_user=self.user,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["connector"]["state"], "not-configured")
        self.assertIn("not configured", result["receipt"]["detail"])
        self.assertNotIn("credential", str(self.user.nexus_profile).lower())

    def test_inventory_lists_ryozan_shop_operation_bindings(self):
        with patch("app.services.email_foundry._foundry_connector_configured", return_value=False):
            inventory = {row["id"]: row for row in foundry_connector_inventory({})}

        for connector_id in ("google-calendar", "stripe", "meta-whatsapp", "meta-instagram", "meta-facebook"):
            self.assertIn(connector_id, inventory)
            self.assertEqual(inventory[connector_id]["state"], "not-configured")
            self.assertEqual(inventory[connector_id]["actions"], ["connect"])

    async def test_stripe_probe_reads_balance_only(self):
        stripe_client = MagicMock()
        stripe_client.v1.balance.retrieve.return_value = MagicMock(livemode=True)
        with (
            patch("app.services.email_foundry._foundry_connector_configured", return_value=True),
            patch("app.services.stripe_client.get_stripe_client", return_value=stripe_client),
        ):
            ok, detail = await _probe_foundry_connector("stripe")

        self.assertTrue(ok)
        self.assertIn("live-mode", detail)
        stripe_client.v1.balance.retrieve.assert_called_once_with()

    async def test_calendar_probe_reports_reason_without_raising(self):
        with (
            patch("app.services.email_foundry._foundry_connector_configured", return_value=True),
            patch(
                "app.services.calendar_sync.check_calendar_connection",
                return_value={"healthy": False, "reason": "refresh_failed"},
            ),
        ):
            ok, detail = await _probe_foundry_connector("google-calendar")

        self.assertFalse(ok)
        self.assertIn("refresh_failed", detail)

    async def test_meta_probe_reads_one_graph_node_with_bearer_token(self):
        client = AsyncMock()
        client.get.return_value = MagicMock(status_code=200)
        context = AsyncMock()
        context.__aenter__.return_value = client
        settings = MagicMock(
            META_GRAPH_API_VERSION="v20.0",
            META_WHATSAPP_PHONE_NUMBER_ID=" 1055 ",
            META_WHATSAPP_ACCESS_TOKEN="token-value",
        )
        with (
            patch("app.services.email_foundry._foundry_connector_configured", return_value=True),
            patch("app.core.config.get_settings", return_value=settings),
            patch("app.services.email_foundry.httpx.AsyncClient", return_value=context),
        ):
            ok, detail = await _probe_foundry_connector("meta-whatsapp")

        self.assertTrue(ok)
        self.assertNotIn("token-value", detail)
        url = client.get.await_args.args[0]
        self.assertEqual(url, "https://graph.facebook.com/v20.0/1055")
        self.assertEqual(client.get.await_args.kwargs["headers"], {"Authorization": "Bearer token-value"})

    async def test_test_outbox_send_returns_authoritative_sent_receipt(self):
        outbox = [
            {
                "id": "o-test-1",
                "at": "2026-08-29T12:00:00Z",
                "slug": "booking_confirmation",
                "name": "Booking Confirmation",
                "to": "justin@example.test",
                "kind": "test",
                "status": "queued",
            }
        ]

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            result = await email_templates.put_foundry_state(
                "bttco-foundry-outbox-v1",
                outbox,
                db=self.db,
                current_user=self.user,
            )

        row = result["value"][0]
        self.assertEqual(row["status"], "sent")
        self.assertTrue(row["receipt"]["providerAccepted"])
        self.assertEqual(row["receipt"]["recipients"], 1)
        send_email.assert_awaited_once()

    async def test_terminal_outbox_receipt_cannot_be_downgraded_by_stale_iframe(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-outbox-v1": [
                    {
                        "id": "o-terminal",
                        "slug": "booking_confirmation",
                        "kind": "test",
                        "status": "sent",
                        "receipt": {"providerAccepted": True},
                    }
                ]
            }
        }
        stale = [
            {
                "id": "o-terminal",
                "slug": "booking_confirmation",
                "kind": "test",
                "to": "justin@example.test",
                "status": "queued",
            }
        ]

        with patch("app.services.email.send_email", new_callable=AsyncMock) as send_email:
            result = await email_templates.put_foundry_state(
                "bttco-foundry-outbox-v1",
                stale,
                db=self.db,
                current_user=self.user,
            )

        self.assertEqual(result["value"][0]["status"], "sent")
        self.assertTrue(result["value"][0]["receipt"]["providerAccepted"])
        send_email.assert_not_awaited()

    async def test_manual_list_send_is_durable_but_not_executed_before_founder_approval(self):
        outbox = [
            {
                "id": "o-manual-1",
                "at": "2026-08-29T12:00:00Z",
                "slug": "campaign_broadcast",
                "name": "Campaign Broadcast",
                "to": "full send list",
                "kind": "manual",
                "status": "held for sign-off",
            }
        ]

        with patch("app.services.email.send_email", new_callable=AsyncMock) as send_email:
            result = await email_templates.put_foundry_state(
                "bttco-foundry-outbox-v1",
                outbox,
                db=self.db,
                current_user=self.user,
            )

        self.assertEqual(result["value"][0]["status"], "held for sign-off")
        send_email.assert_not_awaited()

    async def test_due_campaign_sends_once_to_marketing_opted_in_audience(self):
        recipient = User(
            id=uuid.uuid4(),
            email="collector@example.test",
            name="Casey Collector",
            role="collector",
            marketing_opt_in=True,
        )
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-fields-v1": {
                    "campaign_broadcast": {
                        "headline": "One real scheduled send",
                        "body_copy": "This campaign is captured by the provider.",
                    }
                },
                "bttco-foundry-schedule-v1": {
                    "campaign_broadcast": {
                        "mode": "campaign",
                        "date": "2026-08-29",
                        "time": "01:00",
                        "repeat": "once",
                    }
                },
            }
        }
        audience_result = MagicMock()
        audience_result.scalars.return_value.all.return_value = [recipient]
        self.db.execute.return_value = audience_result
        now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            first = await process_foundry_campaigns(self.db, now=now, owners=[self.user])
            second = await process_foundry_campaigns(self.db, now=now, owners=[self.user])

        self.assertEqual(first, {"due": 1, "sent": 1, "failed": 0})
        self.assertEqual(second, {"due": 0, "sent": 0, "failed": 0})
        send_email.assert_awaited_once()
        schedule = self.user.nexus_profile["email_foundry_state"]["bttco-foundry-schedule-v1"]["campaign_broadcast"]
        self.assertEqual(schedule["lastStatus"], "sent")

    async def test_event_trigger_sends_immediately_and_is_idempotent(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-schedule-v1": {
                    "booking_confirmation": {
                        "mode": "trigger",
                        "trigger": "booking_filed",
                        "delay": "0h",
                    }
                }
            }
        }
        arguments = {
            "recipient": "collector@example.test",
            "variables": {"client_name": "Casey", "artist_name": "Justin Walker"},
            "event_id": "booking:proof-1:filed",
            "owners": [self.user],
        }

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            first = await dispatch_foundry_trigger(self.db, "booking_filed", **arguments)
            second = await dispatch_foundry_trigger(self.db, "booking_filed", **arguments)

        self.assertEqual(first, {"matched": ["booking_confirmation"], "sent": 1, "queued": 0, "failed": 0})
        self.assertEqual(second, {"matched": ["booking_confirmation"], "sent": 1, "queued": 0, "failed": 0})
        send_email.assert_awaited_once()

    async def test_event_trigger_applies_fixed_and_automatic_field_bindings(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-fields-v1": {
                    "booking_confirmation": {
                        "client_name": "Fixed Casey",
                        "artist_name": "Preview Artist",
                        "__courier_bindings": {
                            "client_name": {"mode": "fixed"},
                            "artist_name": {"mode": "automatic"},
                        },
                    }
                },
                "bttco-foundry-schedule-v1": {
                    "booking_confirmation": {
                        "mode": "trigger",
                        "trigger": "booking_filed",
                        "delay": "0h",
                    }
                },
            }
        }

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            result = await dispatch_foundry_trigger(
                self.db,
                "booking_filed",
                recipient="collector@example.test",
                variables={"client_name": "Event Casey", "artist_name": "Event Artist"},
                event_id="booking:binding-proof:filed",
                owners=[self.user],
            )

        self.assertEqual(result, {"matched": ["booking_confirmation"], "sent": 1, "queued": 0, "failed": 0})
        html_body = send_email.await_args.kwargs["html_body"]
        self.assertIn("Fixed Casey", html_body)
        self.assertIn("Event Artist", html_body)
        self.assertNotIn("Event Casey", html_body)
        self.assertNotIn("Preview Artist", html_body)

    async def test_event_trigger_applies_saved_element_overlay_to_provider_html(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-fields-v1": {
                    "booking_confirmation": {
                        "__courier_elements": {
                            "body/table[1]": {
                                "tag": "table",
                                "locked": True,
                                "styles": {"backgroundColor": "#123456"},
                            }
                        }
                    }
                },
                "bttco-foundry-schedule-v1": {
                    "booking_confirmation": {
                        "mode": "trigger",
                        "trigger": "booking_filed",
                        "delay": "0h",
                    }
                },
            }
        }

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            result = await dispatch_foundry_trigger(
                self.db,
                "booking_filed",
                recipient="collector@example.test",
                variables={"client_name": "Casey"},
                event_id="booking:element-overlay:filed",
                owners=[self.user],
            )

        self.assertEqual(result, {"matched": ["booking_confirmation"], "sent": 1, "queued": 0, "failed": 0})
        self.assertIn("background-color:#123456", send_email.await_args.kwargs["html_body"])

    async def test_event_trigger_can_use_a_fixed_transactional_recipient(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-fields-v1": {
                    "booking_confirmation": {
                        "__courier_delivery": {
                            "recipientMode": "fixed",
                            "fixedRecipient": "proof@example.test",
                        }
                    }
                },
                "bttco-foundry-schedule-v1": {
                    "booking_confirmation": {
                        "mode": "trigger",
                        "trigger": "booking_filed",
                        "delay": "0h",
                    }
                },
            }
        }

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            result = await dispatch_foundry_trigger(
                self.db,
                "booking_filed",
                recipient="not-an-event-address",
                variables={"client_name": "Casey"},
                event_id="booking:recipient-proof:filed",
                owners=[self.user],
            )

        self.assertEqual(result, {"matched": ["booking_confirmation"], "sent": 1, "queued": 0, "failed": 0})
        self.assertEqual(send_email.await_args.kwargs["to"], "proof@example.test")

    async def test_invalid_fixed_recipient_fails_closed_before_provider_delivery(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-fields-v1": {
                    "booking_confirmation": {
                        "__courier_delivery": {
                            "recipientMode": "fixed",
                            "fixedRecipient": "not-an-email",
                        }
                    }
                },
                "bttco-foundry-schedule-v1": {
                    "booking_confirmation": {
                        "mode": "trigger",
                        "trigger": "booking_filed",
                        "delay": "0h",
                    }
                },
            }
        }

        with patch("app.services.email.send_email", new_callable=AsyncMock) as send_email:
            result = await dispatch_foundry_trigger(
                self.db,
                "booking_filed",
                recipient="collector@example.test",
                variables={"client_name": "Casey"},
                event_id="booking:invalid-recipient-proof:filed",
                owners=[self.user],
            )

        self.assertEqual(result, {"matched": ["booking_confirmation"], "sent": 0, "queued": 0, "failed": 1})
        send_email.assert_not_awaited()

    async def test_failed_event_trigger_retries_instead_of_becoming_a_false_duplicate(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-schedule-v1": {
                    "booking_confirmation": {
                        "mode": "trigger",
                        "trigger": "booking_filed",
                        "delay": "0h",
                    }
                }
            }
        }
        arguments = {
            "recipient": "collector@example.test",
            "variables": {"client_name": "Casey", "artist_name": "Justin Walker"},
            "event_id": "booking:proof-retry:filed",
            "owners": [self.user],
        }

        with patch(
            "app.services.email.send_email",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as send_email:
            first = await dispatch_foundry_trigger(self.db, "booking_filed", **arguments)
            second = await dispatch_foundry_trigger(self.db, "booking_filed", **arguments)

        self.assertEqual(first, {"matched": ["booking_confirmation"], "sent": 0, "queued": 0, "failed": 1})
        self.assertEqual(second, {"matched": ["booking_confirmation"], "sent": 1, "queued": 0, "failed": 0})
        self.assertEqual(send_email.await_count, 2)

    async def test_delayed_event_trigger_executes_from_hourly_queue(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-schedule-v1": {
                    "aftercare_followup": {
                        "mode": "trigger",
                        "trigger": "session_done",
                        "delay": "2h",
                    }
                }
            }
        }
        occurred_at = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            queued = await dispatch_foundry_trigger(
                self.db,
                "session_done",
                recipient="collector@example.test",
                variables={"client_name": "Casey", "artist_name": "Justin Walker"},
                event_id="booking:proof-2:done",
                occurred_at=occurred_at,
                owners=[self.user],
            )
            early = await process_foundry_trigger_queue(
                self.db,
                now=datetime(2026, 8, 29, 13, 59, tzinfo=UTC),
                owners=[self.user],
            )
            due = await process_foundry_trigger_queue(
                self.db,
                now=datetime(2026, 8, 29, 14, 0, tzinfo=UTC),
                owners=[self.user],
            )

        self.assertEqual(queued, {"matched": ["aftercare_followup"], "sent": 0, "queued": 1, "failed": 0})
        self.assertEqual(early, {"due": 0, "sent": 0, "failed": 0})
        self.assertEqual(due, {"due": 1, "sent": 1, "failed": 0})
        send_email.assert_awaited_once()

    async def test_delayed_event_trigger_resolves_current_recipient_policy_when_due(self):
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-schedule-v1": {
                    "aftercare_followup": {
                        "mode": "trigger",
                        "trigger": "session_done",
                        "delay": "2h",
                    }
                }
            }
        }
        occurred_at = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

        queued = await dispatch_foundry_trigger(
            self.db,
            "session_done",
            recipient="collector@example.test",
            variables={"client_name": "Casey", "artist_name": "Justin Walker"},
            event_id="booking:recipient-policy-queue:done",
            occurred_at=occurred_at,
            owners=[self.user],
        )
        state = self.user.nexus_profile["email_foundry_state"]
        state["bttco-foundry-fields-v1"] = {
            "aftercare_followup": {
                "__courier_delivery": {
                    "recipientMode": "fixed",
                    "fixedRecipient": "proof@example.test",
                }
            }
        }

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            due = await process_foundry_trigger_queue(
                self.db,
                now=datetime(2026, 8, 29, 14, 0, tzinfo=UTC),
                owners=[self.user],
            )

        self.assertEqual(queued, {"matched": ["aftercare_followup"], "sent": 0, "queued": 1, "failed": 0})
        self.assertEqual(due, {"due": 1, "sent": 1, "failed": 0})
        self.assertEqual(send_email.await_args.kwargs["to"], "proof@example.test")

    async def test_flash_published_trigger_uses_only_marketing_opted_in_audience(self):
        recipient = User(
            id=uuid.uuid4(),
            email="collector@example.test",
            name="Casey Collector",
            role="collector",
            marketing_opt_in=True,
        )
        self.user.nexus_profile = {
            "email_foundry_state": {
                "bttco-foundry-schedule-v1": {
                    "flash_drop": {
                        "mode": "trigger",
                        "trigger": "flash_published",
                        "delay": "0h",
                    }
                }
            }
        }
        audience_result = MagicMock()
        audience_result.scalars.return_value.all.return_value = [recipient]
        self.db.execute.return_value = audience_result

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            result = await dispatch_foundry_audience_trigger(
                self.db,
                "flash_published",
                variables={"drop_name": "Proof Drop", "piece_count": "1"},
                event_id="flash:proof:published",
                owners=[self.user],
            )

        self.assertEqual(result, {"matched": ["flash_drop"], "sent": 1, "queued": 0, "failed": 0})
        send_email.assert_awaited_once()
        self.assertEqual(send_email.await_args.kwargs["to"], "collector@example.test")

    async def test_manual_send_uses_locked_foundry_template_without_db_seed(self):
        request = email_templates.TemplateSendRequest(
            recipients=[{"email": "casey@example.test", "name": "Casey"}],
            variables={"artist_name": "Justin Walker", "reference_code": "RYOZAN-PROOF"},
        )

        with patch("app.services.email.send_email", new_callable=AsyncMock, return_value=True) as send_email:
            result = await email_templates.send_template_manual(
                "booking_confirmation",
                request,
                db=self.db,
                current_user=self.user,
            )

        self.assertEqual(result, {"results": [{"email": "casey@example.test", "sent": True}], "sent": 1, "failed": 0})
        self.db.execute.assert_not_awaited()
        call = send_email.await_args.kwargs
        self.assertEqual(call["subject"], "We got your request — Ryozan Studio")
        self.assertEqual(call["tags"], ["booking_confirmation"])
        self.assertIn("RYOZAN-PROOF", call["html_body"])
        self.assertNotIn("{{", call["html_body"])

    async def test_herald_control_routes_through_approved_gateway(self):
        request = email_templates.FoundryAssistRequest(
            system="Return JSON only.",
            messages=[{"role": "user", "content": "Draft the aftercare fields."}],
            max_tokens=800,
        )
        settings = MagicMock(
            ARK_MODEL_GATEWAY_KEY="configured",
            ANTHROPIC_MONTHLY_BUDGET_CAP=40.0,
            ARK_GATEWAY_UTILITY_MODEL="ark/bttco/gemini",
            ARK_GATEWAY_UTILITY_FALLBACK_MODEL="ark/deepseek",
        )
        completion = {
            "choices": [{"message": {"content": '{"fields":{"body":"Proof"}}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }

        with (
            patch("app.core.config.get_settings", return_value=settings),
            patch("app.services.ai_routing.require_ai_access") as require_access,
            patch(
                "app.services.ai_routing.call_ark_gateway_chat_completion",
                new_callable=AsyncMock,
                return_value=completion,
            ) as complete,
            patch("app.services.api_budget.check_budget", new_callable=AsyncMock) as check_budget,
            patch("app.services.api_budget.log_usage", new_callable=AsyncMock) as log_usage,
        ):
            result = await email_templates.assist_foundry(request, self.db, self.user)

        self.assertEqual(result, {"text": '{"fields":{"body":"Proof"}}'})
        require_access.assert_called_once_with(settings, self.user)
        check_budget.assert_awaited_once_with(self.db, 40.0)
        complete.assert_awaited_once()
        log_usage.assert_awaited_once_with(self.db, "email-foundry/herald", 12, 8, self.user.id)

    async def test_signed_unsubscribe_requires_confirmation_then_disables_marketing(self):
        self.user.marketing_opt_in = True
        token = create_foundry_unsubscribe_token(self.user.id)
        confirmation = await email_templates.confirm_foundry_unsubscribe(token)
        self.db.get.return_value = self.user

        completed = await email_templates.unsubscribe_foundry(token, self.db)

        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(b"Unsubscribe me", confirmation.body)
        self.assertEqual(completed.status_code, 200)
        self.assertFalse(self.user.marketing_opt_in)
        self.db.flush.assert_awaited()
