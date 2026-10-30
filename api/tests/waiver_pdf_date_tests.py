"""
Waiver PDF date-of-record tests.

The printed waiver is a legal record of a tattoo session, and the only thing that
ties it to a date is the signed timestamp. This suite renders the PDF for real with
reportlab and asserts against the text a human would read off the paper, so the
guarantee under test is "the date is on the page", not "a flowable was appended".

Text extraction is done with the standard library (ASCII85 + zlib over the content
streams, then the `(...) Tj` show-text operators). reportlab is already a runtime
dependency; pypdf is not, and CI installs only requirements.txt, so adding a PDF
reader purely for tests would put a new package in the production image.
"""

import base64
import contextlib
import json
import os
import re
import unittest
import uuid
import zlib
from datetime import UTC, datetime
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import waiver  # noqa: E402

# ── PDF text extraction (stdlib only) ─────────────────

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_SHOW_TEXT_RE = re.compile(rb"\((?:\\.|[^\\()])*\)\s*Tj")
_ESCAPE_RE = re.compile(rb"\\([nrtbf()\\]|[0-7]{1,3})")
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _unescape_pdf_string(raw: bytes) -> bytes:
    def sub(match: re.Match) -> bytes:
        body = match.group(1)
        if body in _ESCAPES:
            return _ESCAPES[body]
        if body in (b"(", b")", b"\\"):
            return body
        return bytes([int(body, 8) & 0xFF])

    return _ESCAPE_RE.sub(sub, raw)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Return the visible text of a reportlab-generated PDF, one show-op per line."""
    lines: list[str] = []
    for match in _STREAM_RE.finditer(pdf_bytes):
        payload = match.group(1).strip()
        # ReportLab emits ASCII85 streams with the trailing Adobe marker but,
        # on some builds, omits the optional leading marker. Python's decoder
        # requires the pair when adobe=True.
        if payload.endswith(b"~>") and not payload.startswith(b"<~"):
            payload = b"<~" + payload
        with contextlib.suppress(Exception):
            payload = base64.a85decode(payload, adobe=True)
        try:
            payload = zlib.decompress(payload)
        except Exception:
            continue
        for show in _SHOW_TEXT_RE.finditer(payload):
            body = show.group(0)
            body = body[body.index(b"(") + 1 : body.rindex(b")")]
            lines.append(_unescape_pdf_string(body).decode("latin-1"))
    return "\n".join(lines)


# ── Fixtures ──────────────────────────────────────────

# 9:30 PM Eastern on 2026-07-04 is 01:30 UTC on 2026-07-05. Rendering this in UTC
# prints the wrong calendar day on the record, which is the drift this suite pins.
SIGNED_AT = datetime(2026, 7, 5, 1, 30, tzinfo=UTC)
CREATED_AT = datetime(2026, 7, 5, 1, 25, tzinfo=UTC)
SIGNATURE_URL = "https://cdn.example.test/sig/abc.png"

ARTIST_ID = uuid.uuid4()
WAIVER_ID = uuid.uuid4()


class _FakeUser:
    def __init__(self):
        self.id = ARTIST_ID
        self.name = "Test Artist"
        self.role = "admin"


class _FakeInstance:
    def __init__(self, *, signed_at=SIGNED_AT, created_at=CREATED_AT, signature_url=SIGNATURE_URL, extra_data=None):
        self.id = WAIVER_ID
        self.template_id = None
        self.category = "waiver"
        self.signed_at = signed_at
        self.created_at = created_at
        self.artist_signed_at = None
        self.artist_signature = None
        self.artist_id_reviewed = ARTIST_ID
        payload = {
            "reference_code": "WVR-TEST01",
            "full_name": "Jane Client",
            "artist_name": "Test Artist",
            "tattoo_description": "Blackwork tiger",
        }
        if signature_url:
            payload["signature_url"] = signature_url
        if extra_data:
            payload.update(extra_data)
        self.data_json = json.dumps(payload)


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, instance):
        self._instance = instance

    async def execute(self, _statement):
        return _FakeResult(self._instance)


class _FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


class _FakeHTTPClient:
    """Stands in for httpx.AsyncClient. `behaviour` drives what every GET does."""

    def __init__(self, behaviour):
        self._behaviour = behaviour

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url):
        if self._behaviour == "ok":
            return _FakeResponse(200, _png_bytes())
        if self._behaviour == "raise":
            raise RuntimeError("signature fetch exploded")
        if self._behaviour == "non_200":
            return _FakeResponse(503)
        raise AssertionError(f"unhandled behaviour {self._behaviour!r}")


def _png_bytes() -> bytes:
    """A 1x1 PNG, so the success path really decodes an image."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


async def _render(instance, *, fetch="raise"):
    """Run the export endpoint and return the extracted text of the PDF it produced."""
    captured: dict[str, bytes] = {}

    async def _fake_upload(*, file_bytes, filename, mime_type, folder_path):
        captured["pdf"] = file_bytes
        return f"https://r2.example.test/{folder_path}/{filename}"

    def client_factory(**_kwargs):
        return _FakeHTTPClient(fetch)

    with (
        patch.object(waiver, "upload_to_drive", _fake_upload),
        patch("httpx.AsyncClient", client_factory),
    ):
        await waiver.export_waiver_pdf(
            waiver_id=WAIVER_ID,
            db=_FakeSession(instance),
            current_user=_FakeUser(),
        )

    return extract_pdf_text(captured["pdf"])


# ── Tests ─────────────────────────────────────────────

# 2026-07-04 is inside daylight saving, so the zone label is EDT, not EST.
EXPECTED_DATE = "July 04, 2026 at 09:30 PM EDT"


class WaiverPdfDateTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_date_prints_when_signature_downloads(self):
        text = await _render(_FakeInstance(), fetch="ok")
        self.assertIn(EXPECTED_DATE, text)

    async def test_signed_date_prints_when_signature_fetch_raises(self):
        text = await _render(_FakeInstance(), fetch="raise")
        self.assertIn(EXPECTED_DATE, text)

    async def test_signed_date_prints_when_signature_fetch_returns_non_200(self):
        text = await _render(_FakeInstance(), fetch="non_200")
        self.assertIn(EXPECTED_DATE, text)

    async def test_signed_date_prints_when_signature_url_missing(self):
        text = await _render(_FakeInstance(signature_url=None), fetch="raise")
        self.assertIn(EXPECTED_DATE, text)

    async def test_null_signed_at_states_it_explicitly_and_labels_the_substitute(self):
        text = await _render(_FakeInstance(signed_at=None), fetch="raise")
        self.assertIn("Not recorded", text)
        # The created_at timestamp may appear, but only under its own label — never
        # bare, where a reader would take it for the signing date.
        created_stamp = "July 04, 2026 at 09:25 PM EDT"
        if created_stamp in text:
            self.assertIn(f"Not recorded (waiver record created {created_stamp})", text)
        # And the real signing timestamp must not be invented from anywhere.
        self.assertNotIn(EXPECTED_DATE, text)

    async def test_null_signed_at_and_null_created_at_prints_not_recorded(self):
        text = await _render(_FakeInstance(signed_at=None, created_at=None), fetch="raise")
        self.assertIn("Not recorded", text)

    async def test_rendered_date_is_signed_at_in_eastern_not_now(self):
        text = await _render(_FakeInstance(), fetch="ok")
        # Eastern, with the zone spelled out on the page.
        self.assertIn(EXPECTED_DATE, text)
        # Not the UTC rendering, which rolls the record onto the following day.
        self.assertNotIn("July 05, 2026", text)
        self.assertNotIn("UTC", text)
        # Not the clock: nothing on the page may come from datetime.now().
        today = datetime.now(waiver.SHOP_TIMEZONE)
        self.assertNotIn(today.strftime("%B %d, %Y"), text.replace(EXPECTED_DATE, ""))

    async def test_date_appears_in_the_document_body_not_only_the_signature_block(self):
        """The date must survive losing the whole signature section."""
        text = await _render(_FakeInstance(signature_url=None), fetch="raise")
        self.assertGreaterEqual(text.count(EXPECTED_DATE), 1)
        self.assertIn("Date Signed", text)

    async def test_photo_id_url_is_rendered_into_the_printable_packet(self):
        text = await _render(
            _FakeInstance(extra_data={"photo_id_url": "https://r2.example.test/id.png"}),
            fetch="ok",
        )
        self.assertIn("Photo ID", text)

    async def test_string_no_medical_answers_do_not_print_as_yes(self):
        text = await _render(
            _FakeInstance(
                extra_data={
                    "pregnant": "No",
                    "blood_disorder": "No",
                    "skin_condition": "Yes",
                }
            ),
            fetch="raise",
        )
        self.assertIn("Skin Condition", text)
        self.assertIn("Yes", text)
        self.assertGreaterEqual(text.count("No"), 2)


class SignedDateFormattingTests(unittest.TestCase):
    def test_naive_timestamps_are_read_as_utc(self):
        naive = datetime(2026, 7, 5, 1, 30)
        self.assertEqual(
            waiver._format_signed_datetime(naive),
            waiver._format_signed_datetime(naive.replace(tzinfo=UTC)),
        )

    def test_winter_timestamp_is_labelled_est(self):
        winter = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
        self.assertEqual(waiver._format_signed_datetime(winter), "January 15, 2026 at 03:00 PM EST")

    def test_none_formats_to_none(self):
        self.assertIsNone(waiver._format_signed_datetime(None))


if __name__ == "__main__":
    unittest.main()
