"""
Ark Model Gateway routing tests for the shared Ryozan model client.

Kept separate from endpoint and auth tests because the client contract — where it
POSTs, how it authenticates, and how it walks the primary -> fallback slug chain —
is its own unit and should regress independently of any single endpoint.
"""

import os
import unittest
from unittest.mock import patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import HTTPException

from app.core.config import Settings
from app.services import ai_routing


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient, recording POSTs and replaying scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _settings():
    return Settings(
        ARK_MODEL_GATEWAY_BASE_URL="http://127.0.0.1:8788/v1",
        ARK_MODEL_GATEWAY_KEY="test-key",
    )


def _completion(content="hi", finish_reason="stop", tool_calls=None):
    message = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return _FakeResponse(
        payload={
            "choices": [{"finish_reason": finish_reason, "message": message}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
    )


class ArkGatewayRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_to_gateway_with_bearer_and_slug(self):
        client = _FakeAsyncClient([_completion("draft text")])
        with patch.object(ai_routing.httpx, "AsyncClient", return_value=client):
            data = await ai_routing.call_ark_gateway_chat_completion(
                settings=_settings(),
                system_prompt="sys",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                primary_model="ark/bttco/gemini",
                fallback_models=["ark/deepseek"],
            )
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["url"], "http://127.0.0.1:8788/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(call["json"]["model"], "ark/bttco/gemini")
        self.assertEqual(data["choices"][0]["message"]["content"], "draft text")

    async def test_falls_back_to_next_slug_on_upstream_error(self):
        client = _FakeAsyncClient([_FakeResponse(status_code=500, text="boom"), _completion("recovered")])
        with patch.object(ai_routing.httpx, "AsyncClient", return_value=client):
            data = await ai_routing.call_ark_gateway_chat_completion(
                settings=_settings(),
                system_prompt="s",
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                primary_model="ark/bttco/gemini",
                fallback_models=["ark/deepseek"],
            )
        self.assertEqual([c["json"]["model"] for c in client.calls], ["ark/bttco/gemini", "ark/deepseek"])
        self.assertEqual(data["choices"][0]["message"]["content"], "recovered")

    async def test_text_turn_skips_vision_model(self):
        client = _FakeAsyncClient([_completion("fast")])
        with patch.object(ai_routing.httpx, "AsyncClient", return_value=client):
            await ai_routing.call_ark_gateway_chat_completion(
                settings=_settings(),
                system_prompt="s",
                messages=[{"role": "user", "content": "Do you have my bookings?"}],
                tools=[],
                primary_model="nvidia/Nemotron-3.5-Super-VL",
                fallback_models=["nvidia/nemotron-3-super-120b-a12b"],
            )
        self.assertEqual([c["json"]["model"] for c in client.calls], ["nvidia/nemotron-3-super-120b-a12b"])

    async def test_image_turn_keeps_vision_model(self):
        client = _FakeAsyncClient([_completion("looks clean")])
        image_turn = [{"role": "user", "content": [
            {"type": "text", "text": "Critique this flash"},
            {"type": "image_url", "image_url": {"url": "https://example.org/flash.png"}},
        ]}]
        with patch.object(ai_routing.httpx, "AsyncClient", return_value=client):
            await ai_routing.call_ark_gateway_chat_completion(
                settings=_settings(),
                system_prompt="s",
                messages=image_turn,
                tools=[],
                primary_model="nvidia/Nemotron-3.5-Super-VL",
                fallback_models=["nvidia/nemotron-3-super-120b-a12b"],
            )
        self.assertEqual([c["json"]["model"] for c in client.calls], ["nvidia/Nemotron-3.5-Super-VL"])

    async def test_auth_failure_fails_fast_without_walking_fallbacks(self):
        # 401 is not retryable: the same key is reused for every slug, so the client
        # must stop after the first attempt instead of repeating the auth failure.
        client = _FakeAsyncClient([_FakeResponse(status_code=401, text="unauthorized")])
        with (
            patch.object(ai_routing.httpx, "AsyncClient", return_value=client),
            self.assertRaises(HTTPException) as ctx,
        ):
            await ai_routing.call_ark_gateway_chat_completion(
                settings=_settings(),
                system_prompt="s",
                messages=[{"role": "user", "content": "x"}],
                tools=[],
                primary_model="ark/bttco/gemini",
                fallback_models=["ark/deepseek"],
            )
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_tool_call_turn_with_empty_content_is_success(self):
        tool_calls = [{"id": "t1", "function": {"name": "do", "arguments": "{}"}}]
        client = _FakeAsyncClient([_completion(content="", finish_reason="tool_calls", tool_calls=tool_calls)])
        with patch.object(ai_routing.httpx, "AsyncClient", return_value=client):
            data = await ai_routing.call_ark_gateway_chat_completion(
                settings=_settings(),
                system_prompt="s",
                messages=[{"role": "user", "content": "x"}],
                tools=[{"type": "function"}],
                primary_model="ark/bttco/gemini",
                fallback_models=[],
            )
        self.assertEqual(data["choices"][0]["message"]["tool_calls"], tool_calls)
        self.assertEqual(len(client.calls), 1)

    async def test_text_helper_returns_text_and_usage(self):
        client = _FakeAsyncClient([_completion("hello")])
        with patch.object(ai_routing.httpx, "AsyncClient", return_value=client):
            text, usage = await ai_routing.call_ark_gateway_text(
                settings=_settings(),
                system_prompt="s",
                user_prompt="hi",
                primary_model="ark/bttco/gemini",
            )
        self.assertEqual(text, "hello")
        self.assertEqual(usage["completion_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
