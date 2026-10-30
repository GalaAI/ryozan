"""Check-in one-time code contract.

Net-new because the public check-in lookup is a new surface: typing someone else's email or phone must
reveal nothing on file until the code sent to that contact comes back. These pure-function tests pin the
normalization, the single-use code, expiry, the attempt cap, and tamper rejection.
"""

import os
import unittest
from types import SimpleNamespace

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException  # noqa: E402

from app.api.v1.endpoints.documents import (  # noqa: E402
    WAIVER_SERVER_KEYS,
    _prior_prefill_allowed,
    _require_waiver_intake_answers,
    _waiver_phone_matches,
)
from app.services import checkin_verify as cv  # noqa: E402

SECRET = "test-secret"


class NormalizeTests(unittest.TestCase):
    def test_email_and_us_phone_forms(self) -> None:
        self.assertEqual(cv.normalize_contact("  Jane@Example.COM "), cv.Contact("email", "jane@example.com"))
        self.assertEqual(cv.normalize_contact("(864) 555-0134"), cv.Contact("phone", "8645550134"))
        self.assertEqual(cv.normalize_contact("+1 864.555.0134"), cv.Contact("phone", "8645550134"))
        self.assertIsNone(cv.normalize_contact("555-0134"))
        self.assertIsNone(cv.normalize_contact("not-an-email@"))

    def test_mask_never_shows_the_whole_contact(self) -> None:
        self.assertEqual(cv.mask_contact(cv.Contact("phone", "8645550134")), "(•••) ••• 0134")
        masked = cv.mask_contact(cv.Contact("email", "jane@example.com"))
        self.assertTrue(masked.startswith("j") and masked.endswith("@example.com") and "ane" not in masked)


class ChallengeTests(unittest.TestCase):
    def setUp(self) -> None:
        cv._attempts.clear()
        cv._used.clear()
        self.contact = cv.Contact("email", "jane@example.com")

    def test_right_code_verifies_once(self) -> None:
        token, code = cv.issue_challenge(SECRET, self.contact, now=1000)
        self.assertEqual(cv.verify_challenge(SECRET, token, code, now=1001), self.contact)
        with self.assertRaises(cv.ChallengeError):
            cv.verify_challenge(SECRET, token, code, now=1002)

    def test_wrong_code_counts_and_locks_after_the_cap(self) -> None:
        token, code = cv.issue_challenge(SECRET, self.contact, now=1000)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(cv.MAX_ATTEMPTS):
            with self.assertRaises(cv.ChallengeError):
                cv.verify_challenge(SECRET, token, wrong, now=1001)
        with self.assertRaises(cv.ChallengeError) as locked:
            cv.verify_challenge(SECRET, token, code, now=1002)
        self.assertIn("Too many", str(locked.exception))

    def test_expired_code_is_rejected(self) -> None:
        token, code = cv.issue_challenge(SECRET, self.contact, now=1000)
        with self.assertRaises(cv.ChallengeError):
            cv.verify_challenge(SECRET, token, code, now=1000 + cv.CODE_TTL_SECONDS + 1)

    def test_tampered_or_foreign_token_is_rejected(self) -> None:
        token, code = cv.issue_challenge(SECRET, self.contact, now=1000)
        with self.assertRaises(cv.ChallengeError):
            cv.verify_challenge("other-secret", token, code, now=1001)
        body, _, sig = token.partition(".")
        with self.assertRaises(cv.ChallengeError):
            cv.verify_challenge(SECRET, f"{body}x.{sig}", code, now=1001)


class PrefillGateTests(unittest.TestCase):
    def test_prior_prefill_needs_a_staff_send_or_a_verified_checkin(self) -> None:
        walk_in = SimpleNamespace(sent_by=None)
        self.assertFalse(_prior_prefill_allowed(walk_in, {"artist_name": "justin"}))
        self.assertTrue(_prior_prefill_allowed(walk_in, {"checkin_verified": True}))
        self.assertFalse(_prior_prefill_allowed(walk_in, {"checkin_verified": "true"}))
        self.assertTrue(_prior_prefill_allowed(SimpleNamespace(sent_by="staff-id"), {}))

    def test_server_keys_cover_the_booking_link(self) -> None:
        self.assertIn("booking_id", WAIVER_SERVER_KEYS)
        self.assertIn("checkin_verified", WAIVER_SERVER_KEYS)


class PhoneMatchTests(unittest.TestCase):
    """A walk-in is found by the number on their own waiver, in whatever shape they typed it."""

    def test_formats_of_the_same_number_match(self) -> None:
        for stored in ("(864) 555-0134", "864-555-0134", "+1 864 555 0134", "18645550134", "8645550134"):
            with self.subTest(stored=stored):
                self.assertTrue(_waiver_phone_matches({"phone": stored}, "8645550134"))

    def test_other_numbers_and_bad_shapes_do_not_match(self) -> None:
        for data in ({"phone": "864-555-0135"}, {"phone": ""}, {}, {"emergency_contact": "864-555-0134"}, None, []):
            with self.subTest(data=data):
                self.assertFalse(_waiver_phone_matches(data, "8645550134"))
        self.assertFalse(_waiver_phone_matches({"phone": "555-0134"}, "5550134"))


class IntakeAnswerTests(unittest.TestCase):
    def _complete(self, **overrides: object) -> dict:
        data = {
            "tattoo_description": "Traditional boat",
            "quoted_price": "$250",
            "intoxicated": "No",
            "health_and_eligibility": True,
            "risks_and_consent": True,
            "accept_terms": True,
        }
        data.update(overrides)
        return data

    def test_complete_answers_pass_and_price_is_normalized(self) -> None:
        data = self._complete(quoted_price="$1,250")
        _require_waiver_intake_answers(data)
        self.assertEqual(data["quoted_price"], "1250")

    def test_every_required_answer_is_enforced(self) -> None:
        for bad in (
            {"tattoo_description": " "},
            {"quoted_price": ""},
            {"quoted_price": "free"},
            {"quoted_price": "0"},
            {"intoxicated": "Yes"},
            {"intoxicated": None},
            {"accept_terms": "true"},
            {"risks_and_consent": False},
        ):
            with self.subTest(bad=bad), self.assertRaises(HTTPException):
                _require_waiver_intake_answers(self._complete(**bad))


if __name__ == "__main__":
    unittest.main()
