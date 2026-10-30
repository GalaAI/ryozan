"""Contract checks for the locked Email Foundry server projection."""

import hashlib
import json
import re
from pathlib import Path

from app.services.email_foundry import apply_foundry_element_overlays, foundry_source_sha256, render_foundry_email

CATALOG = Path(__file__).resolve().parents[1] / "app" / "services" / "email_foundry_catalog.json"


def test_foundry_catalog_hash_matches_its_templates():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(catalog["templates"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert foundry_source_sha256() == digest


def test_foundry_renderer_uses_exact_template_and_clears_unset_merge_tags():
    rendered = render_foundry_email(
        "deposit_request",
        {
            "artist_name": "Justin Walker",
            "client_name": "Casey",
            "deposit_amount": "$100.00",
            "quote_url": "https://example.test/quote",
        },
    )

    assert rendered is not None
    assert rendered.subject == "Your quote from Justin Walker — Ryozan Studio"
    assert "Casey" in rendered.html_body
    assert "$100.00" in rendered.html_body
    assert not re.search(r"{{[^{}]+}}", rendered.subject)
    assert not re.search(r"{{[^{}]+}}", rendered.html_body)


def test_unknown_foundry_slug_returns_none():
    assert render_foundry_email("legacy_only", {}) is None


def test_every_locked_foundry_template_renders_without_merge_tags():
    slugs = {
        "booking_confirmation",
        "deposit_request",
        "deposit_paid",
        "transaction_receipt",
        "appointment_reminder",
        "aftercare_followup",
        "ezine_scroll",
        "flash_drop",
        "campaign_broadcast",
        "herald_news",
    }

    for slug in slugs:
        rendered = render_foundry_email(slug, {})
        assert rendered is not None
        assert not re.search(r"{{[^{}]+}}", rendered.subject)
        assert not re.search(r"{{[^{}]+}}", rendered.html_body)


def test_booking_confirmation_uses_current_booky_palette_and_live_reference_assets():
    first_reference = "https://assets.ryozan.app/bookings/uploads/reference-one.png"
    second_reference = "https://assets.ryozan.app/bookings/uploads/reference-two.png"
    rendered = render_foundry_email(
        "booking_confirmation",
        {"ref_image_1": first_reference, "ref_image_2": second_reference},
    )

    assert rendered is not None
    assert '<meta name="color-scheme" content="dark">' in rendered.html_body
    assert "#a33a31" in rendered.html_body
    assert "#5e2222" in rendered.html_body
    assert "#e2dac2" in rendered.html_body
    assert first_reference in rendered.html_body
    assert second_reference in rendered.html_body
    assert "assets.ryozan.app/flash/tiger-winged.png" not in rendered.html_body
    assert "assets.ryozan.app/flash/panther-winged.png" not in rendered.html_body


def test_booking_confirmation_omits_empty_reference_slots():
    rendered = render_foundry_email("booking_confirmation", {"ref_image_1": "", "ref_image_2": ""})

    assert rendered is not None
    assert "Reference images" not in rendered.html_body
    assert 'src=""' not in rendered.html_body


def test_element_overlay_changes_content_attributes_and_styles_without_editing_source():
    source = (
        "<!DOCTYPE html><html><body><table><tr><td>"
        '<a href="https://example.test/original" style="color:#111;font-size:12px">Original</a>'
        "</td></tr></table></body></html>"
    )
    rendered = apply_foundry_element_overlays(
        source,
        {
            "body/table[1]/tr[1]/td[1]/a[1]": {
                "tag": "a",
                "text": "Confirm appointment",
                "attributes": {"href": "https://ryozan.app/portal"},
                "styles": {"color": "#e8dfcf", "fontSize": "16px"},
                "locked": True,
            }
        },
    )

    assert "Confirm appointment" in rendered
    assert 'href="https://ryozan.app/portal"' in rendered
    assert "color:#e8dfcf" in rendered
    assert "font-size:16px" in rendered
    assert "Original" not in rendered
    assert "https://example.test/original" not in rendered
    assert source.endswith("</body></html>")


def test_element_overlay_rejects_unsafe_paths_urls_and_style_injection():
    source = '<html><body><a href="https://example.test">Safe</a></body></html>'
    rendered = apply_foundry_element_overlays(
        source,
        {
            "body/a[1]": {
                "tag": "a",
                "text": "<strong>Still text</strong>",
                "attributes": {"href": "javascript:alert(1)"},
                "styles": {"color": "red;display:none", "backgroundColor": "#101110"},
            },
            "body/a[1],script": {"tag": "script", "text": "alert(1)"},
        },
    )

    assert "&lt;strong&gt;Still text&lt;/strong&gt;" in rendered
    assert 'href="https://example.test"' in rendered
    assert "javascript:" not in rendered
    assert "display:none" not in rendered
    assert "background-color:#101110" in rendered


def test_element_overlay_limits_attributes_to_compatible_elements():
    source = '<html><body><div title="Original">Context</div><a href="https://example.test">Safe</a></body></html>'
    rendered = apply_foundry_element_overlays(
        source,
        {
            "body/div[1]": {
                "tag": "div",
                "attributes": {"href": "", "src": "", "alt": "not an image", "title": "Updated"},
            },
            "body/a[1]": {"tag": "a", "attributes": {"href": ""}},
        },
    )

    assert '<div title="Updated">Context</div>' in rendered
    assert '<a href="">Safe</a>' in rendered
    assert "not an image" not in rendered
