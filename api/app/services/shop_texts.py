"""The shop's own texting line: a Twilio sub-account on the platform, or the shop's own Twilio.

Platform (default): Ryozan opens a Twilio sub-account for the shop under the
platform account, which keeps its billing, compliance and credentials apart,
buys a toll-free number in it, and submits the shop's toll-free verification.
Carriers block unverified toll-free traffic, so the line sends only once Twilio
approves the verification.

Own: the shop pastes its own Account SID, Auth Token and number. Ryozan checks
them against Twilio and sends with them.

Credentials are sealed with app.core.secret_box and never returned or logged.
On the demo shop nothing reaches Twilio: no sub-account, number or
verification is made, the card says so, and the nightly reset clears it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from app.core.config import get_settings
from app.core.secret_box import open_sealed, seal
from app.models.studio_setting import StudioSetting

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SETTING_KEY = "texts_connection"
API = "https://api.twilio.com/2010-04-01"
MESSAGING = "https://messaging.twilio.com/v1"
ACCOUNT_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
APPROVED = "TWILIO_APPROVED"
USE_CASES = frozenset(
    {
        "ACCOUNT_NOTIFICATIONS",
        "CUSTOMER_CARE",
        "DELIVERY_NOTIFICATIONS",
        "EVENTS",
        "MARKETING",
        "TWO_FACTOR_AUTHENTICATION",
    }
)
OPT_IN_TYPES = frozenset({"VERBAL", "WEB_FORM", "PAPER_FORM", "VIA_TEXT", "MOBILE_QR_CODE"})
VOLUMES = ("10", "100", "1,000", "10,000", "100,000")

_state: dict = {}


class TextsError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ShopSender:
    account_sid: str
    auth_token: str
    from_number: str


def _demo() -> bool:
    from app.services.demo_shop import demo_shop_enabled

    return demo_shop_enabled()


def _platform_ready() -> bool:
    settings = get_settings()
    return bool((settings.TWILIO_ACCOUNT_SID or "").strip() and (settings.TWILIO_AUTH_TOKEN or "").strip())


def sending_ready() -> bool:
    if _state.get("demo") or not _state.get("phone_number"):
        return False
    if _state.get("mode") == "own":
        return True
    return (_state.get("verification") or {}).get("status") == APPROVED


def shop_sender() -> ShopSender | None:
    """Credentials and number to send the shop's texts with, once the line may send."""
    if not sending_ready():
        return None
    token = open_sealed(_state.get("auth_token"))
    return ShopSender(_state["account_sid"], token, _state["phone_number"]) if token else None


def webhook_tokens() -> list[str]:
    """Extra tokens Twilio may sign the shop line's webhooks with."""
    token = None if _state.get("demo") else open_sealed(_state.get("auth_token"))
    return [token] if token else []


def describe() -> dict:
    verification = _state.get("verification") or {}
    return {
        "platform_ready": _platform_ready() or _demo(),
        "mode": _state.get("mode"),
        "account_sid": _state.get("account_sid"),
        "phone_number": _state.get("phone_number"),
        "verification_status": verification.get("status"),
        "verification_submitted_at": verification.get("submitted_at"),
        "verification_reason": verification.get("reason"),
        "sending_ready": sending_ready(),
        "checked_at": _state.get("checked_at"),
        "demo": _demo(),
        "use_cases": sorted(USE_CASES),
        "opt_in_types": sorted(OPT_IN_TYPES),
        "volumes": list(VOLUMES),
    }


async def load(db: AsyncSession) -> None:
    row = await db.get(StudioSetting, SETTING_KEY)
    _state.clear()
    if row and row.value:
        _state.update(json.loads(row.value))


async def _save(db: AsyncSession) -> None:
    row = await db.get(StudioSetting, SETTING_KEY)
    if not _state:
        if row is not None:
            await db.delete(row)
    else:
        if row is None:
            row = StudioSetting(key=SETTING_KEY)
            db.add(row)
        row.value = json.dumps(_state)
    await db.commit()


async def _twilio(method: str, url: str, auth: tuple[str, str], data: dict | None = None, params=None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(method, url, auth=auth, data=data, params=params)
    except httpx.HTTPError as exc:
        raise TextsError(f"Twilio could not be reached: {type(exc).__name__}", 502) from exc
    if response.status_code >= 400:
        try:
            message = response.json().get("message") or response.text
        except ValueError:
            message = response.text
        status = 400 if response.status_code in (400, 401, 404) else 502
        raise TextsError(f"Twilio refused it: {str(message)[:200]}", status)
    return response.json() if response.content else {}


def _platform_auth() -> tuple[str, str]:
    if not _platform_ready():
        raise TextsError("Texts are not set up on this server (no platform Twilio account).", 503)
    settings = get_settings()
    return settings.TWILIO_ACCOUNT_SID.strip(), settings.TWILIO_AUTH_TOKEN.strip()


def _sub_auth() -> tuple[str, str]:
    token = open_sealed(_state.get("auth_token"))
    if not _state.get("account_sid") or not token:
        raise TextsError("Set up the texting line first.", 409)
    return _state["account_sid"], token


def _inbound_url() -> str:
    return f"{get_settings().API_PUBLIC_BASE_URL.rstrip('/')}/api/v1/sms/webhook/inbound"


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def start_platform(db: AsyncSession) -> dict:
    """Open the shop's Twilio sub-account under the platform account."""
    if _state.get("mode"):
        raise TextsError("A texting line is already set up. Disconnect it first.", 409)
    name = (get_settings().STUDIO_NAME or "Ryozan studio").strip()[:64]
    if _demo():
        _state.update(mode="platform", account_sid="AC" + "0" * 32, auth_token=None, demo=True, checked_at=_now())
    else:
        created = await _twilio("POST", f"{API}/Accounts.json", _platform_auth(), {"FriendlyName": name})
        _state.update(
            mode="platform",
            account_sid=created["sid"],
            auth_token=seal(created["auth_token"]),
            demo=False,
            checked_at=_now(),
        )
    await _save(db)
    return describe()


async def find_numbers(area_code: str | None = None) -> list[dict]:
    """Toll-free numbers that can text, from the shop's sub-account."""
    if _state.get("mode") != "platform":
        raise TextsError("Set up the texting line with Ryozan first.", 409)
    if _state.get("demo"):
        return [
            {"phone_number": "+18005550100", "friendly_name": "(800) 555-0100"},
            {"phone_number": "+18885550199", "friendly_name": "(888) 555-0199"},
        ]
    params = {"SmsEnabled": "true", "PageSize": "5"}
    if area_code and area_code.isdigit():
        params["AreaCode"] = area_code
    sid, token = _sub_auth()
    found = await _twilio(
        "GET", f"{API}/Accounts/{sid}/AvailablePhoneNumbers/US/TollFree.json", (sid, token), params=params
    )
    return [
        {"phone_number": n["phone_number"], "friendly_name": n.get("friendly_name")}
        for n in found.get("available_phone_numbers", [])
    ]


async def buy_number(db: AsyncSession, phone_number: str) -> dict:
    """Buy a toll-free number into the shop's sub-account, pointed at Ryozan's inbound webhook."""
    if _state.get("mode") != "platform":
        raise TextsError("Set up the texting line with Ryozan first.", 409)
    if _state.get("phone_number"):
        raise TextsError("This line already has a number.", 409)
    if not E164_RE.match(phone_number or ""):
        raise TextsError("Pick a number from the list.")
    if _state.get("demo"):
        _state.update(phone_number=phone_number, phone_sid="PN" + "0" * 32, checked_at=_now())
    else:
        sid, token = _sub_auth()
        bought = await _twilio(
            "POST",
            f"{API}/Accounts/{sid}/IncomingPhoneNumbers.json",
            (sid, token),
            {"PhoneNumber": phone_number, "SmsUrl": _inbound_url(), "SmsMethod": "POST"},
        )
        _state.update(phone_number=bought["phone_number"], phone_sid=bought["sid"], checked_at=_now())
    await _save(db)
    return describe()


async def submit_verification(db: AsyncSession, form: dict) -> dict:
    """Submit the shop's toll-free verification, which carriers require before it may text the public."""
    if _state.get("mode") != "platform" or not _state.get("phone_sid"):
        raise TextsError("Get a number first.", 409)
    if form["use_case"] not in USE_CASES or form["opt_in_type"] not in OPT_IN_TYPES or form["volume"] not in VOLUMES:
        raise TextsError("Pick a use case, an opt-in type and a monthly volume from the lists.")
    if _state.get("demo"):
        _state["verification"] = {"sid": "HH" + "0" * 32, "status": "PENDING_REVIEW", "submitted_at": _now()}
    else:
        data = {
            "TollfreePhoneNumberSid": _state["phone_sid"],
            "BusinessName": form["business_name"],
            "BusinessWebsite": form["website"],
            "NotificationEmail": form["notification_email"],
            "UseCaseCategories": form["use_case"],
            "UseCaseSummary": form["use_case_summary"],
            "ProductionMessageSample": form["message_sample"],
            "OptInImageUrls": form["opt_in_image_url"],
            "OptInType": form["opt_in_type"],
            "MessageVolume": form["volume"],
            "BusinessType": form["business_type"],
            "BusinessContactFirstName": form["contact_first_name"],
            "BusinessContactLastName": form["contact_last_name"],
            "BusinessContactEmail": form["notification_email"],
            "BusinessContactPhone": form["contact_phone"],
            "BusinessStreetAddress": form["street"],
            "BusinessCity": form["city"],
            "BusinessStateProvinceRegion": form["state"],
            "BusinessPostalCode": form["postal_code"],
            "BusinessCountry": "US",
        }
        if form.get("ein"):
            data.update(
                BusinessRegistrationNumber=form["ein"],
                BusinessRegistrationAuthority="EIN",
                BusinessRegistrationCountry="US",
            )
        created = await _twilio("POST", f"{MESSAGING}/Tollfree/Verifications", _sub_auth(), data)
        _state["verification"] = {"sid": created["sid"], "status": created.get("status"), "submitted_at": _now()}
    _state["checked_at"] = _now()
    await _save(db)
    return describe()


async def check(db: AsyncSession) -> dict:
    """Read the verification status (platform line) or the account (own line) again."""
    if not _state.get("mode"):
        raise TextsError("No texting line is set up.", 404)
    verification = _state.get("verification") or {}
    if not _state.get("demo") and _state["mode"] == "platform" and verification.get("sid"):
        found = await _twilio("GET", f"{MESSAGING}/Tollfree/Verifications/{verification['sid']}", _sub_auth())
        verification.update(status=found.get("status"), reason=found.get("rejection_reason"))
        _state["verification"] = verification
    elif not _state.get("demo") and _state["mode"] == "own":
        sid, token = _sub_auth()
        await _twilio("GET", f"{API}/Accounts/{sid}.json", (sid, token))
    _state["checked_at"] = _now()
    await _save(db)
    return describe()


async def connect_own(db: AsyncSession, account_sid: str, auth_token: str, phone_number: str) -> dict:
    """Use the shop's own Twilio account, after checking the credentials with Twilio."""
    if _demo():
        raise TextsError("The demo shop does not take Twilio credentials. Your own shop can.", 403)
    if _state.get("mode"):
        raise TextsError("A texting line is already set up. Disconnect it first.", 409)
    account_sid, phone_number = (account_sid or "").strip(), (phone_number or "").strip()
    if not ACCOUNT_SID_RE.match(account_sid) or not E164_RE.match(phone_number) or len(auth_token or "") < 16:
        raise TextsError("Enter the Account SID (AC...), its Auth Token and the number as +1XXXXXXXXXX.")
    await _twilio("GET", f"{API}/Accounts/{account_sid}.json", (account_sid, auth_token.strip()))
    _state.update(
        mode="own",
        account_sid=account_sid,
        auth_token=seal(auth_token.strip()),
        phone_number=phone_number,
        demo=False,
        checked_at=_now(),
    )
    await _save(db)
    return describe()


async def disconnect(db: AsyncSession) -> dict:
    """Stop using the line. A platform number is released so it stops costing the platform."""
    if _state.get("mode") == "platform" and _state.get("phone_sid") and not _state.get("demo"):
        sid, token = _sub_auth()
        await _twilio("DELETE", f"{API}/Accounts/{sid}/IncomingPhoneNumbers/{_state['phone_sid']}.json", (sid, token))
    _state.clear()
    await _save(db)
    return describe()
