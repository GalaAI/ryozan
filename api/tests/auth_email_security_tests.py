import os
import unittest

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from pydantic import ValidationError

from app.api.v1.endpoints.auth import DevImpersonateRequest, _normalize_email, _user_email_matches
from app.models.user import BTTCOUser


class AuthEmailSecurityTests(unittest.TestCase):
    def test_normalize_email_trims_and_lowercases(self):
        self.assertEqual(
            _normalize_email(" Admin@Ryozan.APP "),
            "admin@ryozan.app",
        )

    def test_user_email_lookup_is_case_insensitive(self):
        stmt = select(BTTCOUser).where(_user_email_matches("ADMIN@ryozan.app"))

        compiled = stmt.compile(dialect=postgresql.dialect())

        self.assertIn("lower(bttco_users.email)", str(compiled))
        self.assertEqual(compiled.params["lower_1"], "admin@ryozan.app")

    def test_dev_impersonation_accepts_mirrored_test_domain_accounts(self):
        request = DevImpersonateRequest(email="client123@example.test")
        self.assertEqual(request.email, "client123@example.test")

    def test_dev_impersonation_still_rejects_invalid_email_syntax(self):
        with self.assertRaises(ValidationError):
            DevImpersonateRequest(email="not-an-email")


if __name__ == "__main__":
    unittest.main()
