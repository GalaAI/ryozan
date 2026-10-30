"""
Generate VAPID key pair for Web Push notifications.

Run: python generate_vapid_keys.py

Copy the output into your .env file or app/core/config.py.
"""

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
import base64


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


private_key = ec.generate_private_key(ec.SECP256R1())
priv_numbers = private_key.private_numbers()
priv_bytes = priv_numbers.private_value.to_bytes(32, "big")

pub_bytes = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint,
)

print("Add these to your .env file:\n")
print(f"VAPID_PUBLIC_KEY={b64url(pub_bytes)}")
print(f"VAPID_PRIVATE_KEY={b64url(priv_bytes)}")
print(f'VAPID_SUBJECT=mailto:hackathon-judge@arkomnitek.com')
