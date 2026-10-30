"""Provider-neutral flash-sheet segmentation through Ark Model Gateway.

BTTCO owns upload and preview orchestration. Ark owns model/provider routing at
``POST /v1/vision/segment``. A backend outage is distinct from a valid empty result,
and contextual marks are classified separately so only tattoo designs become inventory.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("bttco.flash_vision")

FLASH_SEGMENT_MODEL = "ark/bttco/flash-segment"
CONTEXT_LABEL_TOKENS = {
    "barcode",
    "border",
    "caption",
    "currency",
    "dollar",
    "label",
    "logo",
    "price",
    "qr",
    "signature",
    "stamp",
    "sticker",
    "text",
    "watermark",
}


class SegmentationUnavailableError(Exception):
    """The configured segmentation service did not produce a trustworthy result."""


def classify_detection_role(label: str | None) -> tuple[str, str | None]:
    """Classify model labels without granting them inventory authority.

    The gateway may explicitly prefix labels with ``design:`` or ``context:``. Older
    responses are still accepted: known contextual tokens are excluded and every other
    bounded object remains a design candidate for artist review.
    """
    normalized = (label or "").strip().lower()
    if normalized.startswith("context:"):
        context_type = normalized.split(":", 1)[1].strip() or "other"
        return "context", context_type
    if normalized.startswith("design:"):
        return "design", None
    for token in CONTEXT_LABEL_TOKENS:
        if token in normalized:
            return "context", token
    return "design", None


def _normalize_detection(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    box = value.get("box")
    if not isinstance(box, dict):
        return None
    try:
        score = float(value.get("score", 0))
        normalized_box = {key: float(box[key]) for key in ("xmin", "ymin", "xmax", "ymax")}
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= score <= 1:
        return None
    if normalized_box["xmax"] <= normalized_box["xmin"] or normalized_box["ymax"] <= normalized_box["ymin"]:
        return None
    label = str(value.get("label") or "design")[:255]
    role, context_type = classify_detection_role(label)
    normalized = {
        "label": label,
        "score": round(score, 4),
        "box": normalized_box,
        "role": role,
        "context_type": context_type,
    }
    cutout_png_b64 = value.get("cutout_png_b64")
    if isinstance(cutout_png_b64, str) and cutout_png_b64:
        try:
            cutout_png_bytes = base64.b64decode(cutout_png_b64, validate=True)
        except (binascii.Error, ValueError, TypeError):
            cutout_png_bytes = b""
        if cutout_png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            normalized["cutout_png_bytes"] = cutout_png_bytes
    return normalized


async def segment_flash_sheet(image_bytes: bytes, *, image_width: int, image_height: int) -> list[dict]:
    """Return normalized design and context detections from Ark Model Gateway."""
    settings = get_settings()
    if not settings.ARK_MODEL_GATEWAY_KEY:
        raise SegmentationUnavailableError("Ark Model Gateway is not configured for flash segmentation.")

    base_url = settings.ARK_MODEL_GATEWAY_BASE_URL.rstrip("/")
    payload = {
        "model": FLASH_SEGMENT_MODEL,
        "image_b64": base64.b64encode(image_bytes).decode("ascii"),
        "image_width": image_width,
        "image_height": image_height,
        "min_score": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {settings.ARK_MODEL_GATEWAY_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{base_url}/vision/segment", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.error("Ark vision request failed: %s", exc)
        raise SegmentationUnavailableError("Ark Model Gateway did not respond.") from exc

    if response.status_code != 200:
        logger.error("Ark vision request rejected: status=%s body=%s", response.status_code, response.text[:200])
        raise SegmentationUnavailableError(f"Ark Model Gateway returned {response.status_code}.")

    try:
        body = response.json()
    except ValueError as exc:
        raise SegmentationUnavailableError("Ark Model Gateway returned a non-JSON response.") from exc
    detections = body.get("detections") if isinstance(body, dict) else None
    if not isinstance(detections, list):
        raise SegmentationUnavailableError("Ark Model Gateway returned a malformed detection response.")

    normalized = [item for item in (_normalize_detection(value) for value in detections) if item is not None]
    model_version = body.get("model") if isinstance(body.get("model"), str) else None
    if model_version:
        for item in normalized:
            item["model_version"] = model_version
    normalized.sort(key=lambda item: (item["box"]["ymin"], item["box"]["xmin"]))
    logger.info(
        "Ark vision request %s returned %d designs and %d context regions",
        body.get("request_id"),
        sum(item["role"] == "design" for item in normalized),
        sum(item["role"] == "context" for item in normalized),
    )
    return normalized


async def suggest_design_name(image_bytes: bytes, crop_box: dict | None = None) -> str | None:
    """Naming remains artist-owned until a typed Ark vision naming route exists."""
    return None
