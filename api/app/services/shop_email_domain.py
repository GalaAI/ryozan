"""The shop's own sending domain for email, on the platform's Resend account.

By default a shop's mail goes out from the platform sender (RESEND_FROM_EMAIL)
with the studio's name as the sender name, so a new shop sends with nothing to
set up. A shop may instead add its own domain: Ryozan creates it through
Resend's Domains API on the platform key, shows the DNS records the shop adds
at its registrar, checks verification, and once Resend reports the domain
verified, the shop's mail goes out from <local>@<its domain>. The shop never
needs a Resend account.

The state lives in one studio setting and is cached here for the sender. On the
demo shop nothing reaches Resend: the records are a sample, and the nightly
reset clears the setting.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from app.core.config import get_settings
from app.models.studio_setting import StudioSetting

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SETTING_KEY = "email_domain"
RESEND_API = "https://api.resend.com"
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
LOCAL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_state: dict = {}


class EmailDomainError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _demo() -> bool:
    from app.services.demo_shop import demo_shop_enabled

    return demo_shop_enabled()


def own_sender() -> str | None:
    """The shop's own from address once its domain is verified, else None."""
    if _state.get("status") == "verified" and not _state.get("demo"):
        return f"{_state['from_local']}@{_state['domain']}"
    return None


def describe() -> dict:
    """What the Connections screen shows. Never includes the platform key."""
    settings = get_settings()
    platform = (settings.RESEND_FROM_EMAIL or "").strip() or (settings.SHOP_EMAIL or "").strip()
    return {
        "platform_ready": bool((settings.RESEND_API_KEY or "").strip() and platform),
        "platform_sender": platform or None,
        "sender_name": (settings.RESEND_FROM_NAME or "").strip() or (settings.STUDIO_NAME or "").strip() or None,
        "domain": _state.get("domain"),
        "from_address": f"{_state['from_local']}@{_state['domain']}" if _state.get("domain") else None,
        "status": _state.get("status"),
        "records": _state.get("records", []),
        "checked_at": _state.get("checked_at"),
        "sending_from": own_sender() or platform or None,
        "demo": _demo(),
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


async def _resend(method: str, path: str, body: dict | None = None) -> dict:
    key = (get_settings().RESEND_API_KEY or "").strip()
    if not key:
        raise EmailDomainError("Email is not set up on this server (no RESEND_API_KEY).", 503)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method, RESEND_API + path, headers={"Authorization": f"Bearer {key}"}, json=body
            )
    except httpx.HTTPError as exc:
        raise EmailDomainError(f"Resend could not be reached: {type(exc).__name__}", 502) from exc
    if response.status_code >= 400:
        try:
            message = response.json().get("message") or response.text
        except ValueError:
            message = response.text
        raise EmailDomainError(f"Resend refused it: {str(message)[:200]}", 502)
    return response.json() if response.content else {}


def _records(raw: list[dict]) -> list[dict]:
    keep = ("record", "type", "name", "value", "priority", "ttl", "status")
    return [{name: item.get(name) for name in keep if item.get(name) is not None} for item in raw or []]


def _sample_records(domain: str) -> list[dict]:
    return [
        {
            "record": "DKIM",
            "type": "TXT",
            "name": "resend._domainkey",
            "value": "p=MIGf...demo",
            "status": "not_started",
        },
        {
            "record": "SPF",
            "type": "MX",
            "name": "send",
            "value": "feedback-smtp.us-east-1.amazonses.com",
            "priority": 10,
            "status": "not_started",
        },
        {
            "record": "SPF",
            "type": "TXT",
            "name": "send",
            "value": "v=spf1 include:amazonses.com ~all",
            "status": "not_started",
        },
    ]


async def add_domain(db: AsyncSession, domain: str, from_local: str = "hello") -> dict:
    domain = (domain or "").strip().lower().rstrip(".")
    from_local = (from_local or "hello").strip().lower()
    if not DOMAIN_RE.match(domain):
        raise EmailDomainError("Enter a domain like yourstudio.com.")
    if not LOCAL_RE.match(from_local):
        raise EmailDomainError("The sender name before the @ can use letters, numbers, dots, dashes and underscores.")
    if _state.get("domain"):
        raise EmailDomainError("Remove the current domain first.", 409)
    now = datetime.now(UTC).isoformat()
    if _demo():
        _state.update(
            domain=domain,
            from_local=from_local,
            resend_id=None,
            status="not_started",
            records=_sample_records(domain),
            checked_at=now,
            demo=True,
        )
    else:
        created = await _resend("POST", "/domains", {"name": domain})
        _state.update(
            domain=domain,
            from_local=from_local,
            resend_id=created.get("id"),
            status=created.get("status") or "not_started",
            records=_records(created.get("records")),
            checked_at=now,
            demo=False,
        )
    await _save(db)
    return describe()


async def check_domain(db: AsyncSession) -> dict:
    if not _state.get("domain"):
        raise EmailDomainError("No domain has been added.", 404)
    if not _state.get("demo"):
        await _resend("POST", f"/domains/{_state['resend_id']}/verify")
        found = await _resend("GET", f"/domains/{_state['resend_id']}")
        _state["status"] = found.get("status") or _state.get("status")
        _state["records"] = _records(found.get("records")) or _state.get("records", [])
    _state["checked_at"] = datetime.now(UTC).isoformat()
    await _save(db)
    return describe()


async def remove_domain(db: AsyncSession) -> dict:
    if _state.get("resend_id") and not _state.get("demo"):
        await _resend("DELETE", f"/domains/{_state['resend_id']}")
    _state.clear()
    await _save(db)
    return describe()
