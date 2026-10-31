"""Seed data for the editable SMS template library.

These hardcoded defaults populate `ryozan_sms_templates` on first deploy.
Re-running the seed endpoint upserts in place so edits aren't clobbered
unless the founder explicitly chooses to reseed.

Body length philosophy: every starter template fits inside a single 160-char
GSM-7 segment after rendering (assuming reasonable variable values), so each
send costs one segment. The founder UI shows live segment count when editing.
"""

from typing import Any


def get_seed_templates() -> list[dict[str, Any]]:
    return [
        {
            "slug": "double_opt_in_confirmation",
            "name": "Double Opt-In Confirmation",
            "category": "compliance",
            "description": (
                "Required CTIA double opt-in message sent the moment a user toggles SMS on. "
                "Bypasses the opt-in gate so it can land in the user's inbox. Must include "
                "program name, frequency, msg & data rates, HELP/STOP."
            ),
            "body": (
                "Ryozan Studio: You're signed up for booking and appointment messages. "
                "Msg freq varies (typ. 2-10/mo). Msg & data rates may apply. "
                "Reply HELP for help, STOP to cancel."
            ),
            "variables": [],
        },
        {
            "slug": "stop_confirmation",
            "name": "STOP Acknowledgement",
            "category": "compliance",
            "description": (
                "Auto-reply when a user texts STOP/UNSUBSCRIBE/CANCEL/QUIT. Twilio Advanced "
                "Opt-Out can also handle this, but our handler sends it explicitly to keep "
                "behaviour predictable and logged."
            ),
            "body": ("Ryozan Studio: You're unsubscribed. No more messages will be sent. Reply START to resubscribe."),
            "variables": [],
        },
        {
            "slug": "help_response",
            "name": "HELP Auto-Reply",
            "category": "compliance",
            "description": "Required HELP keyword response with support contact.",
            "body": (
                "Ryozan Studio: For help, email admin@ryozan.app or visit "
                "ryozan.app. Msg freq varies. Msg & data rates may apply. Reply STOP to cancel."
            ),
            "variables": [],
        },
        {
            "slug": "booking_confirmation",
            "name": "Booking Confirmation",
            "category": "transactional",
            "description": "Sent immediately after a booking is confirmed.",
            "body": (
                "Ryozan Studio: Hi {{client_name}}, your appointment with "
                "{{artist_name}} on {{date}} at {{time}} is confirmed. Reply STOP to opt out."
            ),
            "variables": [
                {"key": "client_name", "label": "Client first name", "sample": "Alex"},
                {"key": "artist_name", "label": "Artist name", "sample": "Alex"},
                {"key": "date", "label": "Appointment date", "sample": "Fri May 30"},
                {"key": "time", "label": "Appointment time", "sample": "2:00 PM"},
            ],
        },
        {
            "slug": "appointment_reminder_24h",
            "name": "Reminder · 24h Before",
            "category": "transactional",
            "description": "Sent 24 hours before a confirmed appointment.",
            "body": (
                "Ryozan Studio: Reminder: your tattoo with {{artist_name}} is "
                "tomorrow at {{time}}. "
                "Reply STOP to opt out."
            ),
            "variables": [
                {"key": "artist_name", "label": "Artist name", "sample": "Alex"},
                {"key": "time", "label": "Appointment time", "sample": "2:00 PM"},
            ],
        },
        {
            "slug": "appointment_reminder_1h",
            "name": "Reminder · 1h Before",
            "category": "transactional",
            "description": "Sent 1 hour before — light touch, no STOP footer is fine for transactional.",
            "body": ("Ryozan Studio: See you in 1 hour at {{time}}. Parking info: ryozan.app/parking"),
            "variables": [
                {"key": "time", "label": "Appointment time", "sample": "2:00 PM"},
            ],
        },
        {
            "slug": "aftercare_followup",
            "name": "Aftercare Check-In · Day 3",
            "category": "transactional",
            "description": "Sent 3 days post-session to check healing progress.",
            "body": (
                "Ryozan Studio: Hey {{client_name}}, how's the tattoo healing? "
                "Aftercare instructions: ryozan.app/aftercare. Questions? Just reply."
            ),
            "variables": [
                {"key": "client_name", "label": "Client first name", "sample": "Alex"},
            ],
        },
        {
            "slug": "staff_message_alert",
            "name": "Staff Message Alert",
            "category": "notification",
            "description": (
                "Sent to opted-in staff when a collector writes in a thread they are on. "
                "Points to Courier because a staff text to the shop line cannot reach the thread."
            ),
            "body": ('Ryozan Studio: {{sender_name}} wrote: "{{preview}}" Reply in Courier: {{courier_url}}'),
            "variables": [
                {"key": "sender_name", "label": "Sender display name", "sample": "Maria Miller"},
                {"key": "preview", "label": "First 80 characters", "sample": "How much would it be?"},
                {
                    "key": "courier_url",
                    "label": "Courier thread link",
                    "sample": "https://ryozan.app/dashboard/courier",
                },
            ],
        },
        {
            "slug": "new_message_notification",
            "name": "New Message Notification",
            "category": "notification",
            "description": (
                "Sent when a client receives a new in-app message. Includes a snippet "
                "so they don't have to log in to see context."
            ),
            "body": ('Ryozan Studio: New message from {{sender_name}}: "{{preview}}" - reply here or open the app.'),
            "variables": [
                {"key": "sender_name", "label": "Sender display name", "sample": "Alex Rivera"},
                {
                    "key": "preview",
                    "label": "Message preview (max 80 chars)",
                    "sample": "Hey, sent over the reference...",
                },
            ],
        },
        {
            "slug": "quote_ready",
            "name": "Quote Ready",
            "category": "transactional",
            "description": "Sent when a quote is published for a client to review.",
            "body": ("Ryozan Studio: Your quote is ready! Review and book: {{quote_url}}"),
            "variables": [
                {
                    "key": "quote_url",
                    "label": "Quote magic-link URL",
                    "sample": "https://ryozan.app/quote/abc123",
                },
            ],
        },
        {
            "slug": "deposit_received",
            "name": "Deposit Received",
            "category": "transactional",
            "description": "Sent when a deposit payment clears.",
            "body": ("Ryozan Studio: Got your ${{amount}} deposit. See you {{date}}. Receipt: {{receipt_url}}"),
            "variables": [
                {"key": "amount", "label": "Deposit amount (no $)", "sample": "100"},
                {"key": "date", "label": "Appointment date", "sample": "Fri May 30"},
                {"key": "receipt_url", "label": "Stripe receipt URL", "sample": "https://pay.stripe.com/receipts/..."},
            ],
        },
    ]
