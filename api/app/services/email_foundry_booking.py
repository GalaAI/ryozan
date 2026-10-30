"""Artist booking alert in Email Foundry's language.

The locked @bttco/email-foundry catalog has no template for the staff "new booking inquiry" alert. Like
the waiver-flow renderers, this lives beside the catalog and reuses its exact markup without touching
package bytes: it is the catalog's Booking Confirmation card (starseed ground, tiger mark header, oxblood
card stub with Booky title, spec cells, reference images, barcode and 虎 seal, state chip, coral pill,
footer) seen from the artist's side. Colours come only from design-lock.json. Every interpolated value
is escaped.
"""

from datetime import date
from html import escape

from app.services.email import _format_datetime_eastern, _format_time_12hr
from app.services.email_foundry import FoundryEmail
from app.services.email_foundry_waiver import (
    _BODY_FONT,
    _DISPLAY_FONT,
    _MONO_FONT,
    _shell,
)

# design-lock.json values, as the catalog templates spell them.
_WORDMARK = "#c8a15e"
_CREAM = "#e2dac2"
_BRICK = "#a33a31"
_OXBLOOD = "#5e2222"
_STUB_META = "#8a8577"
_STUB_PROSE = "#a89d8a"
_STATE_PENDING = "#e2dac2"
_STATE_OK = "#70c27b"
_CHIP_UNSELECTED = "rgba(207,197,176,.5)"
_CORAL = "#d38a68"


def _header(eyebrow: str) -> str:
    """Tiger mark header exactly as the catalog templates draw it."""
    return (
        '<tr><td align="center" style="padding:34px 24px 22px;border-bottom:1px solid #2a2316;">'
        '<img src="https://assets.ryozan.app/email/tiger-mark.png" alt="Ryozan Studio" width="72" '
        'style="display:block;margin:0 auto 14px;">'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:900;font-size:19px;letter-spacing:.18em;'
        f'color:{_WORDMARK};text-transform:uppercase;">RYOZAN STUDIO</div>'
        f'<div style="font-family:{_MONO_FONT};font-size:9.5px;letter-spacing:.22em;color:rgba(226,218,194,.5);'
        f'text-transform:uppercase;padding-top:8px;">{escape(eyebrow)}</div></td></tr>'
    )


def _prose(html: str, *, top: int = 24) -> str:
    """A catalog prose row. `html` must already be escaped."""
    return (
        f'<tr><td style="padding:{top}px 26px 0;font-family:{_BODY_FONT};font-size:14px;line-height:1.6;'
        f'color:{_CREAM};">{html}</td></tr>'
    )


def _cell(label: str, value: str) -> str:
    return (
        '<td class="bttco-email-cell" width="50%" bgcolor="#141514" style="background-color:#141514;'
        'background-image:linear-gradient(#141514,#141514);padding:11px 12px;vertical-align:top;">'
        f'<div style="font-family:{_MONO_FONT};font-size:8.5px;font-weight:700;letter-spacing:.16em;'
        f'color:{_STUB_META};text-transform:uppercase;">{escape(label)}</div>'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:800;font-size:12.5px;letter-spacing:.02em;'
        f'color:{_CREAM};text-transform:uppercase;padding-top:4px;">{escape(value)}</div></td>'
    )


def _spec_cells(pairs: list[tuple[str, str]]) -> str:
    shown = [(label, value.strip()) for label, value in pairs if value and value.strip()]
    rows = []
    for start in range(0, len(shown), 2):
        pair = shown[start : start + 2]
        cells = "".join(_cell(label, value) for label, value in pair)
        if len(pair) == 1:
            cells += (
                '<td class="bttco-email-cell" width="50%" bgcolor="#141514" style="background-color:#141514;'
                'background-image:linear-gradient(#141514,#141514);padding:11px 12px;">&nbsp;</td>'
            )
        rows.append(f"<tr>{cells}</tr>")
    return (
        '<tr><td style="padding:15px 18px 0;"><table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="1" border="0" bgcolor="#33342f" style="background-color:#33342f;border-radius:8px;">'
        + "".join(rows)
        + "</table></td></tr>"
    )


def _enlarge(url: str) -> str:
    """Unselected-chip pill (design-lock chips.unselected) that opens the full image."""
    return (
        f'<a href="{url}" target="_blank" style="display:inline-block;margin-top:8px;padding:6px 12px;'
        f"border:1px solid {_CHIP_UNSELECTED};border-radius:99px;font-family:{_MONO_FONT};font-size:9.5px;"
        f"font-weight:700;letter-spacing:.16em;color:{_CHIP_UNSELECTED};text-transform:uppercase;"
        'text-decoration:none;">&#8599;&nbsp;Enlarge</a>'
    )


def _reference_images(urls: list[str]) -> str:
    """The catalog's reference-images block; each thumbnail and its Enlarge pill open the full image."""
    images = [url for url in urls if url.startswith("https://")]
    if not images:
        return ""
    rows = []
    for start in range(0, len(images), 2):
        cells = []
        for index, url in enumerate(images[start : start + 2], start=start + 1):
            safe = escape(url, quote=True)
            if cells:
                cells.append('<td width="14" style="font-size:0;line-height:0;">&nbsp;</td>')
            cells.append(
                '<td width="50%" align="center" style="vertical-align:top;padding-bottom:10px;">'
                f'<a href="{safe}" target="_blank" style="text-decoration:none;"><img src="{safe}" '
                f'alt="Reference {index}" style="display:block;width:100%;max-height:120px;object-fit:contain;">'
                f"</a>{_enlarge(safe)}</td>"
            )
        if len(cells) == 1:
            cells.append('<td width="14" style="font-size:0;line-height:0;">&nbsp;</td><td width="50%"></td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<tr><td style="padding:16px 18px 0;">'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:800;font-size:11.5px;letter-spacing:.18em;'
        f'color:{_CREAM};text-transform:uppercase;padding-bottom:10px;">Reference images</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + "".join(rows)
        + "</table></td></tr>"
    )


def _description(text: str) -> str:
    body = text.strip()
    if not body:
        return ""
    return (
        f'<tr><td style="padding:13px 18px 0;font-family:{_BODY_FONT};font-size:12.5px;line-height:1.65;'
        f'color:{_STUB_PROSE};">{escape(body).replace(chr(10), "<br>")}</td></tr>'
    )


def _card_stub(*, eyebrow: str, title: tuple[str, str], body: str) -> str:
    """The catalog card stub: oxblood-edged well, Booky title, spec body, barcode and 虎 seal."""
    return (
        '<tr><td style="padding:22px 26px 0;"><table class="bttco-email-well" role="presentation" width="100%" '
        'cellpadding="0" cellspacing="0" border="0" bgcolor="#101110" style="background-color:#101110;'
        f'background-image:linear-gradient(#101110,#101110);border:1px solid {_OXBLOOD};border-radius:14px;">'
        '<tr><td style="padding:18px 18px 0;">'
        f'<div style="font-family:{_MONO_FONT};font-size:9.5px;font-weight:700;letter-spacing:.18em;'
        f'color:{_STUB_META};text-transform:uppercase;padding-bottom:12px;">{escape(eyebrow)}</div>'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:800;font-size:23px;line-height:1.08;'
        f'letter-spacing:.01em;color:{_BRICK};text-transform:uppercase;">{escape(title[0])}</div>'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:800;font-size:23px;line-height:1.08;'
        f'letter-spacing:.01em;color:{_CREAM};text-transform:uppercase;">{escape(title[1])}</div>'
        '<div style="height:1px;background-color:#2c2d2b;margin-top:15px;font-size:0;line-height:0;">&nbsp;</div>'
        "</td></tr>"
        + body
        + '<tr><td style="padding:16px 18px 16px;"><table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td style="vertical-align:middle;"><div style="height:26px;'
        "background:repeating-linear-gradient(90deg,#8a8577 0 2px,transparent 2px 4px,#8a8577 4px 5px,"
        'transparent 5px 9px,#8a8577 9px 11px,transparent 11px 13px);opacity:.7;"></div></td>'
        '<td width="60" align="right" style="vertical-align:middle;"><div style="width:45px;height:45px;'
        'border:1px solid #4a4b45;border-radius:99px;text-align:center;"><div style="font-size:17px;'
        'line-height:45px;color:#c9c0ac;">&#34382;</div></div></td></tr></table>'
        f'<div style="font-family:{_MONO_FONT};font-size:9px;letter-spacing:.14em;color:#6f6a5c;'
        'text-transform:uppercase;padding-top:8px;">Ancient as time, modern as tomorrow</div></td></tr>'
        "</table></td></tr>"
    )


def _state_chip(text: str, color: str) -> str:
    return (
        '<tr><td align="center" style="padding:22px 26px 0;"><span style="display:inline-block;padding:5px 12px;'
        f"border:1px solid {color};border-radius:99px;font-family:{_MONO_FONT};font-size:9.5px;line-height:1;"
        f"font-weight:700;letter-spacing:.16em;color:{color};text-transform:uppercase;"
        'box-shadow:0 0 14px 1px rgba(236,205,143,.22);"><span style="font-size:7px;line-height:1;'
        f'vertical-align:middle;">&#9679;</span><span style="vertical-align:middle;">&nbsp;&nbsp;{escape(text)}'
        "</span></span></td></tr>"
    )


def _pill(label: str, url: str) -> str:
    """The coral action pill (design-lock primaryCta.pill)."""
    return (
        '<td style="padding:5px;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td bgcolor="{_CORAL}" style="background-color:{_CORAL};border-radius:99px;'
        'box-shadow:0 0 22px 2px rgba(211,138,104,.45);">'
        f'<a href="{escape(url, quote=True)}" style="display:block;padding:13px 34px;font-family:{_MONO_FONT};'
        "font-size:11px;font-weight:700;letter-spacing:.14em;text-indent:.14em;text-align:center;"
        f'text-transform:uppercase;color:#29140e;text-decoration:none;">{escape(label)}</a></td></tr></table></td>'
    )


def _chip_link(label: str, url: str) -> str:
    """A secondary action drawn as the lock's unselected chip."""
    return (
        f'<td style="padding:5px;"><a href="{escape(url, quote=True)}" style="display:block;padding:12px 22px;'
        f"border:1px solid {_CHIP_UNSELECTED};border-radius:99px;font-family:{_MONO_FONT};font-size:11px;"
        "font-weight:700;letter-spacing:.14em;text-indent:.14em;text-align:center;text-transform:uppercase;"
        f'color:{_CHIP_UNSELECTED};text-decoration:none;">{escape(label)}</a></td>'
    )


def _actions(*cells: str) -> str:
    return (
        '<tr><td align="center" style="padding:22px 20px 30px;"><table role="presentation" cellpadding="0" '
        'cellspacing="0" border="0"><tr>' + "".join(cells) + "</tr></table></td></tr>"
    )


def render_artist_booking_alert(
    *,
    client_name: str,
    client_email: str,
    client_phone: str | None,
    artist_name: str,
    description: str,
    placement: str | None,
    size: str | None,
    budget: str | None,
    timeline: str | None,
    reference_code: str,
    accept_url: str,
    message_url: str,
    decline_url: str,
    style_preference: str | None = None,
    submitted_at: str | None = None,
    reference_image_urls: list[str] | None = None,
    confirmed: bool = False,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    booking_kind: str | None = None,
    consultation_method: str | None = None,
    preferred_date: str | None = None,
    preferred_time: str | None = None,
    preferred_slots: list[dict[str, str]] | None = None,
    client_notes: str | None = None,
) -> FoundryEmail:
    """Staff alert for a new public booking request, or a consultation the client booked directly."""
    client = client_name.strip() or "A client"
    email = client_email.strip()
    phone = (client_phone or "").strip()
    schedule = " at ".join(
        part
        for part in (
            scheduled_date,
            _format_time_12hr(scheduled_time) or scheduled_time,
        )
        if part
    )

    reach = []
    if email:
        reach.append(f'<a href="mailto:{escape(email, quote=True)}" style="color:{_WORDMARK};">{escape(email)}</a>')
    if phone:
        reach.append(f'<a href="tel:{escape(phone, quote=True)}" style="color:{_WORDMARK};">{escape(phone)}</a>')
    contact = f" Reply to this email or reach them at {' or '.join(reach)}." if reach else ""
    who = f"{escape(client)} wants to book with {escape(artist_name or 'you')}."
    if confirmed:
        who = f"{escape(client)} booked a consultation with {escape(artist_name or 'you')}."

    cells = _spec_cells(
        [
            ("Client", client),
            ("Artist", artist_name or ""),
            ("Request type", (booking_kind or "").replace("_", " ")),
            (
                "Consultation method",
                {
                    "meet": "Google Meet",
                    "in_person": "In person",
                    "whatsapp": "WhatsApp",
                }.get(consultation_method or "", consultation_method or ""),
            ),
            ("Scheduled", schedule if confirmed else ""),
            ("Requested date", preferred_date or ""),
            (
                "Requested time",
                _format_time_12hr(preferred_time) or preferred_time or "",
            ),
            *[
                (
                    f"Requested option {index}",
                    " at ".join(
                        part
                        for part in (
                            slot.get("date"),
                            _format_time_12hr(slot.get("time")) or slot.get("time"),
                        )
                        if part
                    ),
                )
                for index, slot in enumerate(preferred_slots or [], start=1)
            ],
            ("Style", style_preference or ""),
            ("Placement", placement or ""),
            ("Size", size or ""),
            ("Budget", budget or ""),
            ("Timeline", timeline or ""),
            ("Submitted", submitted_at or _format_datetime_eastern()),
            ("Ref", reference_code),
        ]
    )
    stub_body = (
        _description(description or "")
        + (
            _prose(
                "Client notes:<br>" + escape(client_notes).replace(chr(10), "<br>"),
                top=13,
            )
            if client_notes
            else ""
        )
        + cells
        + _reference_images([str(url).strip() for url in (reference_image_urls or []) if str(url).strip()])
    )
    if confirmed:
        eyebrow, title, chip = (
            "Consultation // Booked",
            ("Consult", "Booked"),
            _state_chip("Booked", _STATE_OK),
        )
        stub_eyebrow = f"{reference_code} · Booked"
        preheader = f"{client} booked a consultation. Ref {reference_code}."
        next_step = "Booky already put it on the books. Open the thread to say hi or ask anything before the consult."
        actions = _actions(_pill("Message", message_url))
    else:
        eyebrow, title, chip = (
            "Booking Request // New",
            ("New", "Inquiry"),
            _state_chip("New request", _STATE_PENDING),
        )
        stub_eyebrow = f"{reference_code} · On the table"
        preheader = f"{client} sent a booking request. Ref {reference_code}."
        next_step = "Accept to deal a quote card, or open the thread to ask first. Nothing moves until you do."
        actions = _actions(
            _pill("Accept", accept_url),
            _chip_link("Message", message_url),
            _chip_link("Decline", decline_url),
        )

    rows = "".join(
        [
            _prose(who + contact),
            _card_stub(eyebrow=stub_eyebrow, title=title, body=stub_body),
            chip,
            _prose(next_step),
            actions,
        ]
    )
    subject = f"Consultation Booked with {client}" if confirmed else f"New Booking Request from {client}"
    return FoundryEmail(
        slug="artist_booking_alert",
        subject=subject,
        html_body=_shell(
            title="Consultation Booked" if confirmed else "New Booking Inquiry",
            preheader=preheader,
            header=_header(eyebrow),
            rows=rows,
        ),
    )


def render_request_approved(
    *,
    client_name: str,
    artist_name: str,
    reference_code: str,
    scheduled_date: str,
    scheduled_time: str,
    duration_minutes: int | None,
    deposit_amount: float,
    deposit_url: str | None,
    artist_message: str | None = None,
    booking_kind: str | None = None,
) -> FoundryEmail:
    """Collector email for an approved request: time held for them, deposit still pending.

    Deliberately not the booking confirmation. With a deposit due and a deposit link (the artist's
    Stripe is connected) the coral pill pays it; without a link the email says the deposit request
    follows. A zero deposit approval reads as booked, with no payment language.
    """
    client = client_name.strip() or "there"
    artist = artist_name.strip() or "your artist"
    try:
        parsed = date.fromisoformat(scheduled_date)
        day = f"{parsed.strftime('%a, %b')} {parsed.day}, {parsed.year}"
    except ValueError:
        day = scheduled_date
    when = " at ".join(part for part in (day, _format_time_12hr(scheduled_time) or scheduled_time) if part)
    hours = ""
    if duration_minutes:
        hours = f"{duration_minutes // 60}h" if duration_minutes % 60 == 0 else f"{duration_minutes} min"
    amount = f"${deposit_amount:,.2f}".replace(".00", "")
    needs_deposit = deposit_amount > 0
    kind = "consultation" if booking_kind == "consultation" else "session"

    if needs_deposit and deposit_url:
        intro = (
            f"Hey {escape(client)}, {escape(artist)} approved your request and picked a time. "
            f"Pay the {escape(amount)} deposit to lock it in."
        )
        next_step = "The time is not on the books until the deposit goes through."
        chip = _state_chip("Deposit pending", _STATE_PENDING)
        actions = _actions(_pill(f"Pay {amount} deposit", deposit_url))
    elif needs_deposit:
        intro = (
            f"Hey {escape(client)}, {escape(artist)} approved your request and picked a time. "
            f"A {escape(amount)} deposit holds it."
        )
        next_step = (
            "Your artist will reach out about paying the deposit. "
            "The time is not on the books until the deposit goes through."
        )
        chip = _state_chip("Deposit pending", _STATE_PENDING)
        actions = ""
    else:
        intro = f"Hey {escape(client)}, {escape(artist)} approved your request. No deposit is needed for this {kind}."
        next_step = "You're on the books. Reply to this email if anything changes."
        chip = _state_chip("Approved", _STATE_OK)
        actions = ""

    stub_body = (
        _prose(escape(artist_message.strip()).replace(chr(10), "<br>"), top=13) if artist_message else ""
    ) + _spec_cells(
        [
            ("Artist", artist),
            ("Time", when),
            ("Length", hours),
            ("Deposit", amount if needs_deposit else "None"),
            ("Ref", reference_code),
        ]
    )
    rows = "".join(
        [
            _prose(intro),
            _card_stub(
                eyebrow=f"{reference_code} · Approved",
                title=("Request", "Approved"),
                body=stub_body,
            ),
            chip,
            _prose(escape(next_step)),
            actions or '<tr><td style="padding:0 0 30px;font-size:0;line-height:0;">&nbsp;</td></tr>',
        ]
    )
    subject = f"Request approved: deposit pending with {artist}" if needs_deposit else f"Request approved with {artist}"
    return FoundryEmail(
        slug="request_approved",
        subject=subject,
        html_body=_shell(
            title="Request Approved",
            preheader=f"{artist} approved your request for {when}."
            + (f" Deposit {amount} pending." if needs_deposit else ""),
            header=_header("Booking Request // Approved"),
            rows=rows,
        ),
    )
