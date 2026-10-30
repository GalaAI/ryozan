import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from app.core import deploy_state
from main import app


class DeployStateTests(unittest.TestCase):
    def tearDown(self):
        deploy_state.get_deploy_state.cache_clear()

    def test_prefers_railway_env_metadata(self):
        with (
            patch.dict(
                os.environ,
                {
                    "RAILWAY_GIT_COMMIT_SHA": "4a37f4ea349ff055036db49a035baaa188d0e481",
                    "RAILWAY_GIT_BRANCH": "main",
                    "RAILWAY_ENVIRONMENT_NAME": "production",
                },
                clear=False,
            ),
            patch.object(deploy_state, "_git_output", return_value=None),
        ):
            deploy_state.get_deploy_state.cache_clear()

            state = deploy_state.get_deploy_state()

        self.assertEqual(state["platform"], "railway")
        self.assertEqual(state["environment"], "production")
        self.assertEqual(state["branch"], "main")
        self.assertEqual(state["commit"], "4a37f4ea349ff055036db49a035baaa188d0e481")
        self.assertEqual(state["commit_short"], "4a37f4e")
        self.assertTrue(state["commit_known"])
        self.assertTrue(state["branch_known"])

    def test_falls_back_to_git_metadata(self):
        outputs = {
            ("rev-parse", "HEAD"): "31776377d02bd0f637acda764eeebaac34a6d015",
            ("rev-parse", "--abbrev-ref", "HEAD"): "copilot/codexcourier-rail-action-modal",
        }

        def fake_git_output(*args: str):
            return outputs.get(args)

        with (
            patch.dict(
                os.environ,
                {
                    "RAILWAY_GIT_COMMIT_SHA": "",
                    "RAILWAY_GIT_BRANCH": "",
                    "RAILWAY_ENVIRONMENT_NAME": "",
                    "VERCEL_GIT_COMMIT_SHA": "",
                    "VERCEL_GIT_COMMIT_REF": "",
                    "VERCEL_ENV": "",
                    "APP_ENV": "",
                },
                clear=False,
            ),
            patch.object(deploy_state, "_git_output", side_effect=fake_git_output),
        ):
            deploy_state.get_deploy_state.cache_clear()

            state = deploy_state.get_deploy_state()

        self.assertEqual(state["platform"], "git")
        self.assertEqual(state["environment"], "local")
        self.assertEqual(state["branch"], "copilot/codexcourier-rail-action-modal")
        self.assertEqual(state["commit_short"], "3177637")

    def test_health_deploy_route_returns_metadata(self):
        payload = {
            "platform": "railway",
            "environment": "production",
            "branch": "main",
            "commit": "4a37f4ea349ff055036db49a035baaa188d0e481",
            "commit_short": "4a37f4e",
            "commit_known": True,
            "branch_known": True,
        }
        client = TestClient(app)

        with patch.object(main, "get_deploy_state", return_value=payload):
            response = client.get("/health/deploy")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
