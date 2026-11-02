"""Waiver-flow transactional mail in Email Foundry's language.

The locked @bttco/email-foundry catalog has no template for check-in codes, waiver links, or
immediate aftercare. Like ``render_gift_card_receipt``, these renderers live beside the catalog and
reuse its exact markup (starseed ground, panel, tiger mark or Doc Bot header, cream DO/DON'T card,
coral pill CTA, footer) without touching package bytes. Every interpolated value is escaped.
"""

from html import escape

from app.services.email import placement_tips
from app.services.email_foundry import (
    STARSEED_TILE_URL,
    TIGER_MARK_URL,
    FoundryEmail,
    studio_address_html,
    studio_identity_fields,
)

_BODY_FONT = "'Space Grotesk','Helvetica Neue',Arial,sans-serif"
_MONO_FONT = "'JetBrains Mono','Courier New',Courier,monospace"
_DISPLAY_FONT = "'Poppins','Arial Black','Helvetica Neue',Arial,sans-serif"
_DOC_BOT = "#7fc3b1"
_CORAL = "#d38a68"


def _plain(text: str) -> str:
    """Client copy uses no em or en dashes: 'a — b' becomes 'a. B'."""
    parts = [part.strip() for part in text.replace("–", "—").split("—")]
    out = parts[0]
    for part in parts[1:]:
        if part:
            out = f"{out.rstrip('.')}. {part[0].upper()}{part[1:]}"
    return out


def _shell(*, title: str, preheader: str, header: str, rows: str) -> str:
    footer_address = studio_address_html(
        f"margin:0 0 4px;font-family:{_MONO_FONT};font-size:9.5px;letter-spacing:.16em;color:#756b5c;text-transform:uppercase;"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{escape(title)}</title>
<!--[if !mso]><!-->
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@700;800;900&family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<!--<![endif]-->
<style>:root{{color-scheme:dark;supported-color-schemes:dark;}}body{{margin:0;padding:0;background-color:#0b0c0b!important;}}a{{color:#c8a15e;}}img{{border:0;}}u+.bttco-email-body .bttco-email-panel{{background-image:linear-gradient(#191a19,#191a19)!important;}}u+.bttco-email-body .bttco-email-well{{background-image:linear-gradient(#101110,#101110)!important;}}u+.bttco-email-body .bttco-email-cell{{background-image:linear-gradient(#141514,#141514)!important;}}</style>
</head>
<body class="bttco-email-body" style="margin:0;padding:0;background-color:#0b0c0b;">
<span style="display:none;font-size:1px;color:#0b0c0b;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">{escape(preheader)}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0b0c0b" background="{STARSEED_TILE_URL}" style="background-color:#0b0c0b;background-image:url('{STARSEED_TILE_URL}');background-repeat:repeat;">
<tr><td align="center" style="padding:26px 14px;">
<table class="bttco-email-panel" role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#191a19" style="max-width:600px;width:100%;background-color:#191a19;background-image:linear-gradient(#191a19,#191a19);border:1px solid #3a301e;border-radius:16px;">
{header}{rows}
<tr><td align="center" style="padding:26px 24px 40px;border-top:1px solid #2a2316;">
  <p style="margin:0 0 10px;font-family:{_BODY_FONT};font-size:12.5px;color:#a89d8a;">Questions? Just reply. A human reads this inbox.</p>
  {footer_address}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _tiger_header(eyebrow: str) -> str:
    studio = studio_identity_fields()["studio_name"]
    return (
        '<tr><td align="center" style="padding:32px 24px 20px;border-bottom:1px solid rgba(226,218,194,.12);">'
        f'<img src="{TIGER_MARK_URL}" alt="{escape(studio, quote=True)}" width="72" '
        'style="display:block;margin:0 auto 14px;">'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:900;font-size:19px;letter-spacing:.18em;color:#e2dac2;'
        f'text-transform:uppercase;">{escape(studio)}</div>'
        f'<div style="font-family:{_MONO_FONT};font-size:9.5px;letter-spacing:.22em;color:rgba(226,218,194,.5);'
        f'text-transform:uppercase;padding-top:8px;">{escape(eyebrow)}</div></td></tr>'
    )


def _doc_bot_header(eyebrow: str) -> str:
    return (
        '<tr><td align="center" style="padding:32px 24px 20px;border-bottom:1px solid #314641;">'
        f'<div style="font-size:34px;line-height:1;color:{_DOC_BOT};text-shadow:0 0 22px rgba(127,195,177,.55),'
        '0 0 8px rgba(127,195,177,.4);">&#10010;</div>'
        f'<div style="font-family:{_DISPLAY_FONT};font-weight:900;font-size:19px;letter-spacing:.18em;color:{_DOC_BOT};'
        'text-transform:uppercase;padding-top:12px;">Doc Bot</div>'
        f'<div style="font-family:{_MONO_FONT};font-size:9.5px;letter-spacing:.22em;color:rgba(226,218,194,.5);'
        f'text-transform:uppercase;padding-top:8px;">{escape(eyebrow)}</div></td></tr>'
    )


def _copy(html: str, *, top: int = 24) -> str:
    """A prose row. `html` must already be escaped."""
    return (
        f'<tr><td style="padding:{top}px 26px 0;font-family:{_BODY_FONT};font-size:14px;line-height:1.6;'
        f'color:#e2dac2;">{html}</td></tr>'
    )


def _label(text: str) -> str:
    return (
        f'<tr><td style="padding:24px 26px 0;font-family:{_MONO_FONT};font-size:9.5px;font-weight:700;'
        f'letter-spacing:.22em;color:rgba(226,218,194,.5);text-transform:uppercase;">{escape(text)}</td></tr>'
    )


def _do_card(rows: list[tuple[bool, str]]) -> str:
    """The Foundry cream DO/DON'T card. `True` rows are DO, `False` rows are DON'T."""
    cells = []
    for index, (is_do, text) in enumerate(rows):
        rule = "" if index == 0 else "border-top:1px solid #d8cdb8;"
        pad_top = "14px" if index == 0 else "11px"
        pad_bottom = "0" if index == len(rows) - 1 else "11px"
        color, word = ("#3f7f6d", "DO") if is_do else ("#a03d33", "DON’T")
        cells.append(
            f'<tr><td width="70" style="padding:11px 0 0;{rule}font-family:{_MONO_FONT};font-size:10.5px;'
            f"font-weight:700;letter-spacing:.16em;color:{color};text-transform:uppercase;vertical-align:top;"
            f'padding-top:{pad_top};">{word}</td>'
            f'<td style="padding:11px 0 0;{rule}font-family:{_BODY_FONT};font-size:13.5px;font-weight:600;'
            f'color:#161512;vertical-align:top;padding-top:{pad_top};padding-bottom:{pad_bottom};">{escape(text)}</td></tr>'
        )
    return (
        '<tr><td style="padding:16px 26px 0;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ece3cf" '
        'style="background-color:#ece3cf;border-radius:12px;"><tr><td style="padding:6px 18px 14px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        + "".join(cells)
        + "</table></td></tr></table></td></tr>"
    )


def _spec_well(pairs: list[tuple[str, str]]) -> str:
    """Dark well with mono labels and cream values (the Foundry card stub surface)."""
    shown = [(k, v) for k, v in pairs if v]
    cells = []
    for index, (key, value) in enumerate(shown):
        rule = "" if index == 0 else "border-top:1px dashed rgba(226,218,194,.14);"
        cells.append(
            f'<tr><td style="padding:10px 0;{rule}font-family:{_MONO_FONT};font-size:9.5px;font-weight:700;'
            f'letter-spacing:.16em;color:rgba(226,218,194,.5);text-transform:uppercase;vertical-align:top;" width="96">'
            f"{escape(key)}</td>"
            f'<td align="right" style="padding:10px 0;{rule}font-family:{_BODY_FONT};font-size:14px;font-weight:600;'
            f'color:#e2dac2;">{escape(value)}</td></tr>'
        )
    return (
        '<tr><td style="padding:16px 26px 0;"><table class="bttco-email-well" role="presentation" width="100%" '
        'cellpadding="0" cellspacing="0" border="0" bgcolor="#101110" style="background-color:#101110;'
        'background-image:linear-gradient(#101110,#101110);border:1px solid rgba(226,218,194,.14);border-radius:12px;">'
        '<tr><td style="padding:6px 18px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0">' + "".join(cells) + "</table></td></tr></table></td></tr>"
    )


def _cta(label: str, url: str) -> str:
    return (
        '<tr><td align="center" style="padding:22px 26px 30px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td bgcolor="{_CORAL}" style="background-color:{_CORAL};border-radius:99px;'
        'box-shadow:0 0 22px 2px rgba(211,138,104,.45);">'
        f'<a href="{escape(url, quote=True)}" style="display:block;padding:13px 34px;font-family:{_MONO_FONT};'
        "font-size:11px;font-weight:700;letter-spacing:.14em;text-indent:.14em;text-align:center;"
        f'text-transform:uppercase;color:#29140e;text-decoration:none;">{escape(label)}</a>'
        "</td></tr></table></td></tr>"
    )


def _first(name: str) -> str:
    return (name or "").strip().split(" ")[0] or "there"


def render_aftercare_instructions(
    *,
    client_name: str,
    artist_name: str,
    tattoo_description: str,
    tattoo_placement: str,
    reference_code: str,
    portal_url: str,
    member: bool = False,
) -> FoundryEmail:
    """Immediate aftercare, sent when the waiver is filed. Doc Bot voice, day 0."""
    studio = studio_identity_fields()["studio_name"]
    first = escape(_first(client_name))
    artist = escape(artist_name or "your artist")
    placement = tattoo_placement.strip()
    tips = [_plain(tip) for tip in placement_tips(placement)]
    tip_rows = "".join(
        f'<tr><td style="padding:8px 0 0;font-family:{_BODY_FONT};font-size:13.5px;line-height:1.55;'
        f'color:#e2dac2;"><span style="color:{_DOC_BOT};">&#10010;</span>&nbsp; {escape(tip)}</td></tr>'
        for tip in tips
    )
    rows = "".join(
        [
            _copy(
                f'Hey {first},<br><br>Your waiver is filed. This is <strong style="color:{_DOC_BOT};">Doc Bot</strong>, '
                f"{artist}'s healing drone. Once you're done in the chair, here's how to keep the new piece sharp. "
                "Keep this email handy."
            ),
            _spec_well(
                [
                    ("Piece", tattoo_description.strip()),
                    ("Where", placement),
                    ("Artist", artist_name or ""),
                    ("Waiver", reference_code),
                ]
            ),
            _label("First 2 to 4 hours"),
            _do_card(
                [
                    (True, "Leave the wrap on as long as your artist said (overnight for second skin)"),
                    (True, "Wash your hands before you touch it"),
                    (True, "Rinse with lukewarm water and fragrance-free soap"),
                    (True, "Pat dry with a clean paper towel"),
                    (False, "Rub it dry with a cloth towel"),
                ]
            ),
            _label("Days 1 to 14"),
            _do_card(
                [
                    (True, "Wash 2 to 3× daily, fragrance-free soap"),
                    (True, "Thin layer of Aquaphor for 3 days, then unscented lotion"),
                    (False, "Pick, scratch, or peel the flake"),
                    (False, "Pools, tubs, ocean, direct sun"),
                ]
            ),
            _label(f"For your {placement.lower()}" if placement else "For your placement"),
            '<tr><td style="padding:4px 26px 0;"><table role="presentation" width="100%" cellpadding="0" '
            f'cellspacing="0" border="0">{tip_rows}</table></td></tr>',
            _copy(
                "Healed for good? SPF 30+ every time it sees sun keeps the lines crisp. Watch for spreading redness, "
                "swelling, pus, or fever. If you see any of that, reply with a photo and call a doctor.",
                top=22,
            ),
            _copy(
                '<span style="font-size:12.5px;color:#a89d8a;">A fresh tattoo can mean a waiting period before you '
                "donate blood. Check with your blood bank.</span>",
                top=14,
            ),
            _cta("Open my portal" if member else "Save my details for next time", portal_url),
        ]
    )
    return FoundryEmail(
        slug="aftercare_instructions",
        subject=f"Your aftercare from Doc Bot | {studio}",
        html_body=_shell(
            title="Tattoo Aftercare",
            preheader="Day 0. Keep it clean, keep it moisturized, keep your hands off.",
            header=_doc_bot_header(f"{studio} // Aftercare // Day 0"),
            rows=rows,
        ),
    )


def render_checkin_code(*, code: str) -> FoundryEmail:
    """One-time check-in code for the waiver intake."""
    safe_code = escape(code)
    rows = "".join(
        [
            _copy("Here's your check-in code. Type it on the check-in screen to pull up what we have on file."),
            '<tr><td style="padding:18px 26px 0;"><table class="bttco-email-well" role="presentation" width="100%" '
            'cellpadding="0" cellspacing="0" border="0" bgcolor="#101110" style="background-color:#101110;'
            "background-image:linear-gradient(#101110,#101110);border:1px solid rgba(211,138,104,.55);"
            'border-radius:16px;"><tr><td align="center" style="padding:24px 18px;">'
            f'<div style="font-family:{_MONO_FONT};font-size:30px;font-weight:700;letter-spacing:.3em;'
            f'text-indent:.3em;color:#e2dac2;">{safe_code}</div>'
            f'<div style="padding-top:10px;font-family:{_MONO_FONT};font-size:9.5px;font-weight:700;letter-spacing:.2em;'
            'color:rgba(226,218,194,.5);text-transform:uppercase;">Expires in 10 minutes</div>'
            "</td></tr></table></td></tr>",
            _copy(
                '<span style="font-size:12.5px;color:#a89d8a;">Didn\'t ask for this? Ignore it. Nobody gets in '
                "without the code.</span>",
                top=18,
            ),
            '<tr><td style="padding:0 0 26px;"></td></tr>',
        ]
    )
    return FoundryEmail(
        slug="checkin_code",
        subject=f"{code} is your Ryozan check-in code",
        html_body=_shell(
            title="Check-in Code",
            preheader=f"{code} expires in 10 minutes.",
            header=_tiger_header("Check-in // Code"),
            rows=rows,
        ),
    )


def render_waiver_link(
    *,
    client_name: str,
    artist_name: str,
    document_title: str,
    form_url: str,
    hours_remaining: int | None = None,
) -> FoundryEmail:
    """Waiver link from the shop, or its reminder when `hours_remaining` is given."""
    first = escape(_first(client_name))
    reminder = hours_remaining is not None
    who = escape(artist_name) if artist_name else "The shop"
    if reminder:
        lead = (
            f"Hey {first},<br><br>Your {escape(document_title.lower())} is still waiting on you. It takes about three "
            "minutes: your details, a few health questions, and a signature."
        )
        if hours_remaining:
            lead += f" The link works for about {int(hours_remaining)} more hours."
    else:
        lead = (
            f"Hey {first},<br><br>{who} sent your {escape(document_title.lower())} ahead of your session. Fill it out "
            "on your phone before you come in and you'll walk straight to the chair. Have your photo ID handy."
        )
    rows = "".join([_copy(lead), _cta("Fill out my waiver", form_url)])
    subject = (
        f"Reminder: your {document_title.lower()} still needs a signature"
        if reminder
        else f"Your {document_title.lower()} from {studio_identity_fields()['studio_name']}"
    )
    return FoundryEmail(
        slug="waiver_reminder" if reminder else "waiver_link",
        subject=subject,
        html_body=_shell(
            title=document_title,
            preheader="About three minutes. ID, health questions, signature.",
            header=_tiger_header("Waiver // Reminder" if reminder else "Waiver // Before your session"),
            rows=rows,
        ),
    )
