"""scripts/publish_shop_pack.py signs and sends the same URI-encoded path, with @ as %40."""

import hashlib
import hmac
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "publish_shop_pack.py"
_spec = importlib.util.spec_from_file_location("publish_shop_pack", SCRIPT)
publish = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = publish
_spec.loader.exec_module(publish)

ACCOUNT = "acct0123"
ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
KEY = "shops/example/@exampleshop/email-foundry/1.0.0/adapter.js"
_REAL_CLIENT = httpx.Client


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def reference_signature(
    method: str,
    canonical_uri: str,
    headers: dict[str, str],
    signed: list[str],
    payload_sha256: str,
    amz_date: str,
    region: str,
    secret_key: str,
) -> str:
    """Independent SigV4 per the AWS docs, as a verifier: the canonical URI is the path as sent."""
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            "",
            "".join(f"{name}:{headers[name].strip()}\n" for name in signed),
            ";".join(signed),
            payload_sha256,
        ]
    )
    datestamp = amz_date[:8]
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), datestamp), region), "s3"), "aws4_request")
    return hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()


def _signature_of(authorization: str) -> tuple[list[str], str]:
    fields = dict(part.strip().split("=", 1) for part in authorization.split(" ", 1)[1].split(","))
    return fields["SignedHeaders"].split(";"), fields["Signature"]


class ReferenceSignatureTests(unittest.TestCase):
    def test_reference_matches_the_aws_documented_put_example(self):
        # AWS S3 "Signature Calculations for the Authorization Header", PUT Object example:
        # a reserved character in the key ($) is signed encoded (%24).
        payload = hashlib.sha256(b"Welcome to Amazon S3.").hexdigest()
        self.assertEqual(payload, "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072")
        headers = {
            "date": "Fri, 24 May 2013 00:00:00 GMT",
            "host": "examplebucket.s3.amazonaws.com",
            "x-amz-content-sha256": payload,
            "x-amz-date": "20130524T000000Z",
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
        }
        signature = reference_signature(
            "PUT", "/test%24file.text", headers, sorted(headers), payload, "20130524T000000Z", "us-east-1", SECRET_KEY
        )
        self.assertEqual(signature, "98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")


class ObjectPathTests(unittest.TestCase):
    def test_at_sign_is_encoded(self):
        path = publish.object_path("ryozan-packs", KEY)
        self.assertEqual(path, "/ryozan-packs/shops/example/%40exampleshop/email-foundry/1.0.0/adapter.js")
        self.assertNotIn("@", path)

    def test_unreserved_and_separators_are_kept(self):
        self.assertEqual(publish.object_path("b", "a-b_c.d~e/f g"), "/b/a-b_c.d~e/f%20g")

    def test_signature_uses_the_encoded_path(self):
        payload = hashlib.sha256(b"x").hexdigest()
        now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
        path = publish.object_path("ryozan-packs", KEY)
        headers = publish.signed_put_headers(ACCOUNT, ACCESS_KEY, SECRET_KEY, path, "text/javascript", payload, now)
        signed, signature = _signature_of(headers["authorization"])
        self.assertEqual(signed, ["content-type", "host", "x-amz-content-sha256", "x-amz-date"])
        expected = reference_signature("PUT", path, headers, signed, payload, "20261110T120000Z", "auto", SECRET_KEY)
        self.assertEqual(signature, expected)
        # Signing the raw @ (the old behaviour) gives a different signature, which R2 rejects.
        raw = path.replace("%40", "@")
        self.assertNotEqual(
            signature, reference_signature("PUT", raw, headers, signed, payload, "20261110T120000Z", "auto", SECRET_KEY)
        )


class UploadRequestTests(unittest.TestCase):
    def test_upload_sends_and_signs_percent_40(self):
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(200)

        def client(*args, **kwargs):
            return _REAL_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "adapter.js"
            file.write_bytes(b"export function installFoundryAdapter() {}\n")
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            pack_path = "@exampleshop/email-foundry/1.0.0/adapter.js"
            env = {"R2_ACCOUNT_ID": ACCOUNT, "R2_ACCESS_KEY_ID": ACCESS_KEY, "R2_SECRET_ACCESS_KEY": SECRET_KEY}
            with patch.dict(os.environ, env), patch.object(httpx, "Client", client):
                failures = publish.upload(
                    [(pack_path, file, file.stat().st_size, digest)], "ryozan-packs", "shops/example"
                )

        self.assertEqual(failures, 0)
        self.assertEqual(len(sent), 1)
        request = sent[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.url.host, f"{ACCOUNT}.r2.cloudflarestorage.com")
        raw_path = request.url.raw_path.decode()
        self.assertEqual(raw_path, "/ryozan-packs/shops/example/%40exampleshop/email-foundry/1.0.0/adapter.js")
        # Verify the way R2 does: recompute from the request as received.
        signed, signature = _signature_of(request.headers["authorization"])
        received = {name: request.headers[name] for name in signed}
        expected = reference_signature(
            "PUT",
            raw_path,
            received,
            signed,
            request.headers["x-amz-content-sha256"],
            request.headers["x-amz-date"],
            "auto",
            SECRET_KEY,
        )
        self.assertEqual(signature, expected)
        self.assertEqual(request.headers["x-amz-content-sha256"], digest)


if __name__ == "__main__":
    unittest.main()
