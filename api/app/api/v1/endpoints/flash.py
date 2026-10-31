"""
Flash sheet + design endpoints — upload, segment, manage, publish.

Artist flow:
  POST /flash/upload         — upload a flash sheet image
  POST /flash/{id}/segment   — AI-segment designs on the sheet
  GET  /flash/sheets         — list artist's flash sheets
  GET  /flash/{id}/designs   — list designs on a sheet
  PATCH /flash/designs/{id}  — update design (name, price, time, publish)

Collector flow:
  GET  /flash/store          — browse published designs (all artists)
  POST /flash/designs/{id}/claim — claim a design (creates booking)
"""

import base64
import hashlib
import json
import logging
import random
import string
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as _date
from urllib.parse import urlparse
from uuid import UUID
from uuid import UUID as _UUID

import httpx
import stripe
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.calendar import _artist_booking_rules, _parse_local_time, _slot_is_available
from app.core.config import get_settings
from app.core.database import async_session, get_db
from app.core.security import ROLE_RANK, get_current_user, hash_password, require_role
from app.core.user_lookup import find_user_by_email
from app.models.booking import Booking
from app.models.booking_slot_hold import BookingSlotHold
from app.models.flash import FlashDesign, FlashSheet, ModelFeedbackSample
from app.models.user import User, normalize_email
from app.schemas.flash import (
    FlashBookRequest,
    FlashBookResponse,
    FlashDesignCreate,
    FlashDesignListResponse,
    FlashDesignResponse,
    FlashDesignUpdate,
    FlashSegmentationRequest,
    FlashSheetCropRequest,
    FlashSheetInventoryResponse,
    FlashSheetListResponse,
    FlashSheetResponse,
    PublicSheetWallResponse,
    SegmentResponse,
    SegmentResult,
)
from app.services.account_setup import send_account_setup_email
from app.services.ai_routing import call_ark_gateway_chat_completion
from app.services.calendar_sync import artist_calendar_connected, artist_calendar_id, get_availability
from app.services.email_foundry import dispatch_foundry_audience_trigger
from app.services.hf_vision import SegmentationUnavailableError, segment_flash_sheet
from app.services.image_processing import (
    CroppedPreview,
    ImageValidationError,
    create_design_cutout_preview,
    create_design_preview,
    validate_and_normalize_image,
)
from app.services.payments import apply_customer_receipt, customer_price_cents, resolve_charge_routing
from app.services.storage import build_object_key
from app.services.storage import upload_file as upload_to_drive
from app.services.stripe_client import get_stripe_client

logger = logging.getLogger("ryozan.flash")

router = APIRouter(prefix="/flash", tags=["flash"])

_PUBLIC_FLASH_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_PUBLIC_FLASH_IMAGE_BYTES = 15 * 1024 * 1024
_APEX_DATASET_SYNC_INTERVAL_SECONDS = 300
_last_apex_dataset_attempt = 0.0
_last_apex_dataset_digest: str | None = None


def _can_manage_artist_flash(current_user: User, artist_id: UUID) -> bool:
    """Allow artists to manage their own flash and admin+ roles to manage staff inventory."""
    return current_user.id == artist_id or ROLE_RANK.get(current_user.role, 0) >= ROLE_RANK.get("admin", 3)


async def _resolve_flash_owner(
    db: AsyncSession,
    current_user: User,
    artist_id: UUID | None,
) -> User:
    if artist_id is None or artist_id == current_user.id:
        return current_user

    if ROLE_RANK.get(current_user.role, 0) < ROLE_RANK.get("admin", 3):
        raise HTTPException(status_code=403, detail="Cannot manage another artist's flash inventory")

    result = await db.execute(
        select(User).where(
            User.id == artist_id,
            User.role.in_(["artist", "admin", "founder"]),
        )
    )
    owner = result.scalar_one_or_none()
    if not owner:
        raise HTTPException(status_code=404, detail="Artist not found")
    return owner


def _sheet_to_response(sheet: FlashSheet) -> FlashSheetResponse:
    source_image_url = getattr(sheet, "source_image_url", None) or sheet.image_url
    return FlashSheetResponse(
        id=str(sheet.id),
        artist_id=str(sheet.artist_id),
        image_url=sheet.image_url,
        source_image_url=source_image_url,
        crop_applied=bool(getattr(sheet, "crop_applied", False)),
        crop_x=float(getattr(sheet, "crop_x", 0) or 0),
        crop_y=float(getattr(sheet, "crop_y", 0) or 0),
        crop_w=float(getattr(sheet, "crop_w", 100) or 100),
        crop_h=float(getattr(sheet, "crop_h", 100) or 100),
        asset_version=int(getattr(sheet, "asset_version", 1) or 1),
        title=sheet.title,
        status=sheet.status,
        design_count=sheet.design_count,
        created_at=sheet.created_at.isoformat(),
    )


def _design_to_response(
    d: FlashDesign,
    source_image_url: str | None = None,
) -> FlashDesignResponse:
    stored_image_url = d.image_url
    metadata = d.ai_metadata if isinstance(d.ai_metadata, dict) else {}
    stored_asset_version = metadata.get("asset_version")
    asset_version = stored_asset_version if stored_asset_version in (2, 3) else 1
    crop_units = "percent" if asset_version >= 2 else "pixel"
    preview_url = stored_image_url
    if source_image_url and stored_image_url == source_image_url:
        preview_url = None
    return FlashDesignResponse(
        id=str(d.id),
        sheet_id=str(d.sheet_id),
        artist_id=str(d.artist_id),
        crop_x=float(d.crop_x),
        crop_y=float(d.crop_y),
        crop_w=float(d.crop_w),
        crop_h=float(d.crop_h),
        image_url=preview_url or source_image_url or stored_image_url,
        preview_url=preview_url,
        source_image_url=source_image_url,
        crop_units=crop_units,
        asset_version=asset_version,
        context_regions=metadata.get("context_regions", []) if asset_version >= 2 else [],
        name=d.name,
        ai_suggested_name=d.ai_suggested_name,
        price=float(d.price) if d.price else None,
        estimated_minutes=d.estimated_minutes,
        size_label=d.size_label,
        placement=d.placement,
        style=d.style,
        deposit_amount=float(metadata.get("deposit_amount")) if metadata.get("deposit_amount") is not None else None,
        reclaimable=bool(metadata.get("reclaimable", False)),
        mask_json=d.mask_json,
        canvas_state=d.canvas_state,
        ai_metadata=d.ai_metadata,
        gala_sync_enabled=d.gala_sync_enabled,
        status=d.status,
        claimed_by=str(d.claimed_by) if d.claimed_by else None,
        claimed_at=d.claimed_at.isoformat() if d.claimed_at else None,
        created_at=d.created_at.isoformat(),
    )


def _is_sam2_cutout(design: FlashDesign) -> bool:
    stored_mask = getattr(design, "mask_json", None)
    mask = stored_mask if isinstance(stored_mask, dict) else {}
    return mask.get("source") == "sam2_alpha_cutout"


def _parse_flash_inventory(content: str) -> list[dict[str, str | None]]:
    """Normalize the gateway's strict JSON inventory into bounded storefront rows."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise _flash_error(502, "FLASH_INVENTORY_INVALID", "AI returned an unreadable design list. Try again.") from exc

    raw_items = payload.get("designs") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise _flash_error(502, "FLASH_INVENTORY_INVALID", "AI did not return a design list. Try again.")

    items: list[dict[str, str | None]] = []
    for raw in raw_items[:24]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:255]
        if not name:
            continue
        style = str(raw.get("style") or "").strip().lower()[:100] or None
        items.append({"name": name, "style": style})
    if not items:
        raise _flash_error(422, "FLASH_INVENTORY_EMPTY", "No tattoo designs were identified on this sheet.")
    return items


def _record_model_feedback(
    db: AsyncSession,
    *,
    design: FlashDesign,
    sheet: FlashSheet,
    outcome: str,
    source: str,
    correction: dict | None = None,
    prediction: dict | None = None,
) -> None:
    """Append one review event; training exporters use the latest event per design."""
    metadata = design.ai_metadata if isinstance(design.ai_metadata, dict) else {}
    db.add(
        ModelFeedbackSample(
            model_id=str(metadata.get("model_id") or "sam2-bttco"),
            model_version=str(metadata.get("model_version") or "unknown"),
            input_type="image",
            input_ref=sheet.image_url,
            artifact_ref=design.image_url,
            subject_id=design.id,
            prediction=prediction
            or {
                "box_percent": {
                    "x": float(design.crop_x),
                    "y": float(design.crop_y),
                    "w": float(design.crop_w),
                    "h": float(design.crop_h),
                },
                "mask": design.mask_json,
                "asset_version": metadata.get("asset_version"),
            },
            correction=correction,
            accepted=outcome in {"accepted", "corrected"},
            outcome=outcome,
            source=source,
            module="bttco",
            training_status="pending" if outcome in {"accepted", "corrected"} else "excluded",
        )
    )


async def _design_responses_with_sources(
    db: AsyncSession,
    designs: list[FlashDesign],
) -> list[FlashDesignResponse]:
    if not designs:
        return []
    sheet_ids = {design.sheet_id for design in designs}
    result = await db.execute(select(FlashSheet.id, FlashSheet.image_url).where(FlashSheet.id.in_(sheet_ids)))
    source_urls = {sheet_id: image_url for sheet_id, image_url in result.all()}
    artist_ids = {design.artist_id for design in designs}
    artist_result = await db.execute(select(User.id, User.slug, User.name).where(User.id.in_(artist_ids)))
    artists = {artist_id: (slug, name) for artist_id, slug, name in artist_result.all()}
    responses = []
    for design in designs:
        response = _design_to_response(design, source_urls.get(design.sheet_id))
        artist_slug, artist_name = artists.get(design.artist_id, (None, None))
        responses.append(response.model_copy(update={"artist_slug": artist_slug, "artist_name": artist_name}))
    return responses


# ── Artist Endpoints ─────────────────────────────────


def _flash_error(status_code: int, code: str, message: str) -> HTTPException:
    """
    Build an HTTPException carrying a stable machine code plus a user-facing message.

    The rest of the API uses a plain-string `detail`. This keeps that human string as
    `detail.message` and adds `detail.code` for programmatic handling. The frontend's
    parseDetail() recognizes this {code, message} shape and falls back to plain strings
    for every existing endpoint, so the change is backward compatible.
    """
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# Map image-validation codes to HTTP status. Content problems are 422 (unprocessable);
# size is 413 (payload too large) so clients can distinguish "too big" from "wrong type".
_IMAGE_ERROR_STATUS = {
    "FLASH_EMPTY_FILE": 422,
    "FLASH_UNREADABLE_IMAGE": 422,
    "FLASH_UNSUPPORTED_FORMAT": 422,
    "FLASH_IMAGE_TOO_LARGE": 422,
    "FLASH_FILE_TOO_LARGE": 413,
}


@router.post("/upload", response_model=FlashSheetResponse, status_code=201)
async def upload_flash_sheet(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    artist_id: UUID | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Upload a flash sheet image to Cloudflare R2 and create a sheet record.

    The browser-reported MIME type is never trusted. The raw bytes are verified and
    normalized with Pillow (EXIF-corrected, metadata-stripped, re-encoded to a
    browser-safe JPEG/PNG/WebP) before anything reaches storage.
    """
    owner = await _resolve_flash_owner(db, current_user, artist_id)

    contents = await file.read()

    try:
        normalized = validate_and_normalize_image(contents, file.filename)
    except ImageValidationError as exc:
        raise _flash_error(_IMAGE_ERROR_STATUS.get(exc.code, 422), exc.code, exc.message) from exc

    object_key = build_object_key(
        folder_path=f"flash/{owner.slug or owner.id}",
        extension=normalized.extension,
    )

    # Upload the normalized bytes under a collision-resistant key.
    image_url = await upload_to_drive(
        file_bytes=normalized.data,
        filename=object_key.rsplit("/", 1)[-1],
        mime_type=normalized.mime_type,
        folder_path=object_key.rsplit("/", 1)[0],
    )
    if not image_url:
        raise _flash_error(
            503,
            "FLASH_STORAGE_UNAVAILABLE",
            "Could not save your sheet right now. Storage is unavailable; please try again.",
        )

    sheet = FlashSheet(
        artist_id=owner.id,
        image_url=image_url,
        source_image_url=image_url,
        # Original filename is preserved only as the human-facing title, never as a key.
        title=title or (file.filename.rsplit(".", 1)[0] if file.filename else "Flash sheet"),
        status="uploaded",
    )
    db.add(sheet)
    await db.flush()

    return _sheet_to_response(sheet)


@router.put("/{sheet_id}/crop", response_model=FlashSheetResponse)
async def save_sheet_crop(
    sheet_id: UUID,
    body: FlashSheetCropRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Materialize a reversible overall sheet crop before design segmentation."""
    result = await db.execute(select(FlashSheet).where(FlashSheet.id == sheet_id))
    sheet = result.scalar_one_or_none()
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Not your flash sheet")
    if sheet.design_count or sheet.status != "uploaded":
        raise _flash_error(
            409,
            "FLASH_CROP_AFTER_SEGMENTATION",
            "Crop the sheet before segmentation. Remove its generated designs before changing the frame.",
        )

    source_url = getattr(sheet, "source_image_url", None) or sheet.image_url
    is_full_sheet = (
        body.crop_x <= 0.0001 and body.crop_y <= 0.0001 and body.crop_w >= 99.9999 and body.crop_h >= 99.9999
    )
    if is_full_sheet:
        sheet.source_image_url = source_url
        sheet.image_url = source_url
        sheet.crop_applied = False
        sheet.crop_x = 0
        sheet.crop_y = 0
        sheet.crop_w = 100
        sheet.crop_h = 100
        sheet.asset_version = int(getattr(sheet, "asset_version", 1) or 1) + 1
        await db.flush()
        return _sheet_to_response(sheet)

    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        img_resp = await client.get(source_url)
        if img_resp.status_code != 200:
            raise _flash_error(502, "FLASH_SOURCE_UNAVAILABLE", "Could not fetch the original flash sheet image.")

    try:
        normalized = validate_and_normalize_image(img_resp.content, source_url)
        box = {
            "xmin": round(body.crop_x / 100 * normalized.width),
            "ymin": round(body.crop_y / 100 * normalized.height),
            "xmax": round((body.crop_x + body.crop_w) / 100 * normalized.width),
            "ymax": round((body.crop_y + body.crop_h) / 100 * normalized.height),
        }
        cropped = create_design_preview(normalized.data, box, max_edge_px=3200)
    except ImageValidationError as exc:
        raise _flash_error(422, exc.code, exc.message) from exc

    object_key = build_object_key(
        folder_path=f"flash/{sheet.artist_id}/sheets",
        extension=cropped.extension,
    )
    cropped_url = await upload_to_drive(
        file_bytes=cropped.data,
        filename=object_key.rsplit("/", 1)[-1],
        mime_type=cropped.mime_type,
        folder_path=object_key.rsplit("/", 1)[0],
    )
    if not cropped_url:
        raise _flash_error(
            503,
            "FLASH_CROP_STORAGE_UNAVAILABLE",
            "Could not save the cropped sheet. The original was preserved.",
        )

    sheet.source_image_url = source_url
    sheet.image_url = cropped_url
    sheet.crop_applied = True
    sheet.crop_x = cropped.percent_box["x"]
    sheet.crop_y = cropped.percent_box["y"]
    sheet.crop_w = cropped.percent_box["w"]
    sheet.crop_h = cropped.percent_box["h"]
    sheet.asset_version = int(getattr(sheet, "asset_version", 1) or 1) + 1
    await db.flush()
    return _sheet_to_response(sheet)


@router.post("/{sheet_id}/segment", response_model=SegmentResponse)
async def segment_sheet(
    sheet_id: UUID,
    body: FlashSegmentationRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """
    AI-segment a flash sheet into individual designs through the Ark Model Gateway.

    Returns design regions separately from labels, prices, signatures, and other context.
    Artist can then review, adjust, name, and price each design.
    """
    result = await db.execute(select(FlashSheet).where(FlashSheet.id == sheet_id))
    sheet = result.scalar_one_or_none()
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Not your flash sheet")

    # Fetch the image bytes from the stored URL
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        img_resp = await client.get(sheet.image_url)
        if img_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Could not fetch flash sheet image")
        image_bytes = img_resp.content

    # Re-validate stored bytes before they cross the model boundary. This also repairs
    # the known binary-as-text corruption and converts legacy MPO sources to a single,
    # browser-safe primary frame.
    try:
        normalized = validate_and_normalize_image(image_bytes, sheet.image_url)
    except ImageValidationError as exc:
        raise _flash_error(422, exc.code, exc.message) from exc

    segmentation_data = normalized.data
    segmentation_width = normalized.width
    segmentation_height = normalized.height
    segmentation_crop = {
        "xmin": 0,
        "ymin": 0,
        "xmax": normalized.width,
        "ymax": normalized.height,
    }
    if body is not None:
        segmentation_crop = {
            "xmin": round(body.crop_x / 100 * normalized.width),
            "ymin": round(body.crop_y / 100 * normalized.height),
            "xmax": round((body.crop_x + body.crop_w) / 100 * normalized.width),
            "ymax": round((body.crop_y + body.crop_h) / 100 * normalized.height),
        }
        try:
            prepared_crop = create_design_preview(normalized.data, segmentation_crop, max_edge_px=1600)
        except ImageValidationError as exc:
            raise _flash_error(422, exc.code, exc.message) from exc
        segmentation_data = prepared_crop.data
        segmentation_width = prepared_crop.width
        segmentation_height = prepared_crop.height
        segmentation_crop = prepared_crop.pixel_box

    try:
        detections = await segment_flash_sheet(
            segmentation_data,
            image_width=segmentation_width,
            image_height=segmentation_height,
        )
    except SegmentationUnavailableError as exc:
        raise _flash_error(
            503,
            "FLASH_SEGMENTATION_UNAVAILABLE",
            "Design detection is temporarily unavailable. The sheet was preserved; try again later or adjust the sheet crop.",
        ) from exc

    crop_source_width = segmentation_crop["xmax"] - segmentation_crop["xmin"]
    crop_source_height = segmentation_crop["ymax"] - segmentation_crop["ymin"]

    def map_detection_to_source(detection: dict) -> dict:
        if body is None:
            return detection
        box = detection["box"]
        mapped = dict(detection)
        mapped["box"] = {
            "xmin": segmentation_crop["xmin"] + float(box["xmin"]) / segmentation_width * crop_source_width,
            "ymin": segmentation_crop["ymin"] + float(box["ymin"]) / segmentation_height * crop_source_height,
            "xmax": segmentation_crop["xmin"] + float(box["xmax"]) / segmentation_width * crop_source_width,
            "ymax": segmentation_crop["ymin"] + float(box["ymax"]) / segmentation_height * crop_source_height,
        }
        return mapped

    detection_pairs = [(map_detection_to_source(detection), detection) for detection in detections]
    design_pairs = [pair for pair in detection_pairs if pair[0].get("role") == "design"]
    context_pairs = [pair for pair in detection_pairs if pair[0].get("role") == "context"]
    design_detections = [pair[0] for pair in design_pairs]
    context_detections = [pair[0] for pair in context_pairs]
    if not design_detections:
        raise _flash_error(
            422,
            "FLASH_NO_DESIGNS_DETECTED",
            "No tattoo designs were detected. The sheet was preserved and no inventory was created.",
        )

    def percent_context(detection: dict) -> dict:
        box = detection["box"]
        return {
            "label": detection["label"],
            "score": detection["score"],
            "context_type": detection.get("context_type"),
            "box": {
                "x": round(float(box["xmin"]) / normalized.width * 100, 4),
                "y": round(float(box["ymin"]) / normalized.height * 100, 4),
                "w": round((float(box["xmax"]) - float(box["xmin"])) / normalized.width * 100, 4),
                "h": round((float(box["ymax"]) - float(box["ymin"])) / normalized.height * 100, 4),
            },
        }

    context_regions = [percent_context(detection) for detection in context_detections]

    # Materialize every design crop before changing database state. A partial storage
    # failure therefore cannot delete the artist's existing draft regions or report a
    # segmented sheet with missing preview assets.
    materialized: list[tuple[dict, CroppedPreview, str, bool]] = []
    for detection, model_detection in design_pairs:
        transparent_cutout = False
        cutout_png_bytes = detection.get("cutout_png_bytes")
        if isinstance(cutout_png_bytes, bytes):
            try:
                cutout_preview = create_design_cutout_preview(
                    cutout_png_bytes,
                    model_detection["box"],
                    source_width=segmentation_width,
                    source_height=segmentation_height,
                )
                source_preview = create_design_preview(normalized.data, detection["box"])
                preview = CroppedPreview(
                    data=cutout_preview.data,
                    mime_type=cutout_preview.mime_type,
                    extension=cutout_preview.extension,
                    width=cutout_preview.width,
                    height=cutout_preview.height,
                    pixel_box=source_preview.pixel_box,
                    percent_box=source_preview.percent_box,
                )
                transparent_cutout = True
            except ImageValidationError as exc:
                logger.warning("Invalid SAM 2 cutout, using crop fallback: %s", exc.code)
        if not transparent_cutout:
            try:
                preview = create_design_preview(normalized.data, detection["box"])
            except ImageValidationError as exc:
                raise _flash_error(422, exc.code, exc.message) from exc
        object_key = build_object_key(
            folder_path=f"flash/{sheet.artist_id}/previews",
            extension=preview.extension,
        )
        preview_url = await upload_to_drive(
            file_bytes=preview.data,
            filename=object_key.rsplit("/", 1)[-1],
            mime_type=preview.mime_type,
            folder_path=object_key.rsplit("/", 1)[0],
        )
        if not preview_url:
            raise _flash_error(
                503,
                "FLASH_PREVIEW_STORAGE_UNAVAILABLE",
                "Could not save the design previews. The sheet and existing designs were preserved.",
            )
        materialized.append((detection, preview, preview_url, transparent_cutout))

    # Re-segmentation should replace prior draft regions instead of stacking duplicates.
    await db.execute(
        delete(FlashDesign).where(
            FlashDesign.sheet_id == sheet.id,
            FlashDesign.status == "draft",
        )
    )

    # Create FlashDesign records for each detection.
    # Detector labels are stored as metadata only because they are often too generic for tattoo titles.
    for index, (det, preview, preview_url, transparent_cutout) in enumerate(materialized, start=1):
        percent_box = preview.percent_box
        design = FlashDesign(
            sheet_id=sheet.id,
            artist_id=sheet.artist_id,
            crop_x=percent_box["x"],
            crop_y=percent_box["y"],
            crop_w=percent_box["w"],
            crop_h=percent_box["h"],
            image_url=preview_url,
            mask_json={
                "box": preview.pixel_box,
                "source": "sam2_alpha_cutout" if transparent_cutout else "ark_model_gateway_crop",
                "transparent_cutout": transparent_cutout,
            },
            canvas_state={"status": "ai_proposed", "crop_units": "percent", "box": percent_box},
            ai_metadata={
                "label": det.get("label"),
                "score": det.get("score"),
                "role": "design",
                "model_id": "sam2-bttco" if transparent_cutout else "ark-model-gateway",
                "model_version": det.get("model_version") or "unknown",
                "context_regions": context_regions,
                "segmentation_crop": segmentation_crop if body is not None else None,
                "normalization_actions": list(normalized.recovery_actions),
                "asset_version": 3 if transparent_cutout else 2,
            },
            ai_suggested_name=f"Design {index}",
            status="draft",
        )
        db.add(design)

    sheet.status = "segmented"
    sheet.design_count = len(materialized)
    await db.flush()

    return SegmentResponse(
        sheet_id=str(sheet.id),
        image_url=sheet.image_url,
        designs=[SegmentResult(**detection) for detection in design_detections],
        context_regions=[SegmentResult(**detection) for detection in context_detections],
        design_count=len(materialized),
    )


@router.get("/sheets", response_model=FlashSheetListResponse)
async def list_sheets(
    skip: int = 0,
    limit: int = Query(default=20, le=50),
    artist_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """List flash sheets for the current artist or an admin-selected artist."""
    owner = await _resolve_flash_owner(db, current_user, artist_id)
    count_result = await db.execute(
        select(func.count()).select_from(FlashSheet).where(FlashSheet.artist_id == owner.id)
    )
    total = count_result.scalar()

    result = await db.execute(
        select(FlashSheet)
        .where(FlashSheet.artist_id == owner.id)
        .order_by(FlashSheet.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    sheets = result.scalars().all()

    return FlashSheetListResponse(
        sheets=[_sheet_to_response(s) for s in sheets],
        total=total,
    )


@router.post("/sheets/{sheet_id}/identify", response_model=FlashSheetInventoryResponse)
async def identify_sheet_inventory(
    sheet_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Use AI to draft a named inventory while preserving the whole sheet asset."""
    result = await db.execute(select(FlashSheet).where(FlashSheet.id == sheet_id))
    sheet = result.scalar_one_or_none()
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Not your flash sheet")

    designs_result = await db.execute(select(FlashDesign).where(FlashDesign.sheet_id == sheet.id))
    existing_designs = list(designs_result.scalars().all())
    inventory_designs = [
        design
        for design in existing_designs
        if isinstance(design.ai_metadata, dict) and design.ai_metadata.get("inventory_mode") == "whole_sheet"
    ]
    advanced_designs = [design for design in existing_designs if design not in inventory_designs]
    if advanced_designs:
        raise _flash_error(
            409,
            "FLASH_INVENTORY_CONFLICT",
            "This sheet already has segmented design records. Keep using its advanced review flow.",
        )

    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        image_response = await client.get(sheet.image_url)
        if image_response.status_code != 200:
            raise _flash_error(502, "FLASH_SOURCE_UNAVAILABLE", "Could not fetch the flash sheet image.")

    try:
        normalized = validate_and_normalize_image(image_response.content, sheet.image_url)
        preview = create_design_preview(
            normalized.data,
            {"xmin": 0, "ymin": 0, "xmax": normalized.width, "ymax": normalized.height},
            max_edge_px=1600,
        )
    except ImageValidationError as exc:
        raise _flash_error(422, exc.code, exc.message) from exc

    settings = get_settings()
    image_data_url = f"data:{preview.mime_type};base64,{base64.b64encode(preview.data).decode('ascii')}"
    completion = await call_ark_gateway_chat_completion(
        settings=settings,
        system_prompt=(
            "You inventory tattoo flash sheets. Identify only distinct tattooable designs. "
            "Do not return crop coordinates, masks, outlines, lettering labels, signatures, paper edges, or decorations. "
            'Return strict JSON only as {"designs":[{"name":"short visual name","style":"tattoo style or null"}]}.'
        ),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "List the separate tattoo designs visible on this full flash sheet."},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        tools=[],
        primary_model=settings.AI_PREMIUM_PRIMARY_MODEL,
        fallback_models=[settings.AI_PREMIUM_FALLBACK_MODEL, settings.AI_PAID_FALLBACK_MODEL],
        max_tokens=1200,
    )
    message = ((completion.get("choices") or [{}])[0]).get("message") or {}
    inventory = _parse_flash_inventory(str(message.get("content") or ""))

    for existing in inventory_designs:
        await db.delete(existing)

    created: list[FlashDesign] = []
    for item in inventory:
        design = FlashDesign(
            sheet_id=sheet.id,
            artist_id=sheet.artist_id,
            crop_x=0,
            crop_y=0,
            crop_w=100,
            crop_h=100,
            image_url=None,
            name=item["name"],
            ai_suggested_name=item["name"],
            style=item["style"],
            mask_json=None,
            canvas_state={"status": "sheet_inventory", "crop_units": "percent"},
            ai_metadata={
                "role": "design_inventory_item",
                "inventory_mode": "whole_sheet",
                "model_id": settings.AI_PREMIUM_PRIMARY_MODEL,
                "asset_version": 2,
            },
            gala_sync_enabled=False,
            status="draft",
        )
        db.add(design)
        created.append(design)

    sheet.status = "identified"
    sheet.design_count = len(created)
    await db.flush()
    return FlashSheetInventoryResponse(
        sheet=_sheet_to_response(sheet),
        designs=[_design_to_response(design, sheet.image_url) for design in created],
    )


@router.post("/sheets/{sheet_id}/publish-catalog", response_model=FlashSheetInventoryResponse)
async def publish_sheet_catalog(
    sheet_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Publish a completed whole-sheet catalog after the artist reviews every item."""
    sheet = await db.get(FlashSheet, sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Not your flash sheet")

    result = await db.execute(
        select(FlashDesign).where(FlashDesign.sheet_id == sheet.id).order_by(FlashDesign.created_at)
    )
    designs = list(result.scalars().all())
    inventory = [
        design
        for design in designs
        if isinstance(design.ai_metadata, dict) and design.ai_metadata.get("inventory_mode") == "whole_sheet"
    ]
    if not inventory or len(inventory) != len(designs):
        raise _flash_error(409, "FLASH_CATALOG_INVALID", "This sheet is not a whole-sheet draft catalog.")

    incomplete = []
    for design in inventory:
        metadata = design.ai_metadata or {}
        if (
            not design.name
            or design.price is None
            or not design.estimated_minutes
            or metadata.get("deposit_amount") is None
        ):
            incomplete.append(design.name or design.ai_suggested_name or "Untitled design")
    if incomplete:
        raise _flash_error(
            422,
            "FLASH_CATALOG_INCOMPLETE",
            "Complete name, price, time, and deposit for every design before publishing.",
        )

    for design in inventory:
        design.status = "published"
        design.updated_at = datetime.now(UTC)
    sheet.status = "published"
    sheet.design_count = len(inventory)
    prices = [float(design.price) for design in inventory if design.price is not None]
    price_range = (
        f"${min(prices):,.0f} - ${max(prices):,.0f}"
        if prices and min(prices) != max(prices)
        else f"${prices[0]:,.0f}"
        if prices
        else ""
    )
    images = [design.image_url for design in inventory if design.image_url] or [sheet.image_url]
    try:
        await dispatch_foundry_audience_trigger(
            db,
            "flash_published",
            variables={
                "drop_name": sheet.title or "New Flash Drop",
                "drop_date": f"{datetime.now(UTC).astimezone().strftime('%B')} {datetime.now(UTC).astimezone().day}",
                "piece_count": str(len(inventory)),
                "artist_name": getattr(current_user, "name", None) or "Ryozan Studio",
                "price_range": price_range,
                "flash_image_1": images[0],
                "flash_image_2": images[1] if len(images) > 1 else images[0],
                "claim_url": f"{get_settings().FRONTEND_URL.rstrip('/')}/flash",
            },
            event_id=f"flash-sheet:{sheet.id}:published",
        )
    except Exception:
        logger.exception("Foundry flash-published dispatch failed for sheet %s", sheet.id)
    await db.flush()
    return FlashSheetInventoryResponse(
        sheet=_sheet_to_response(sheet),
        designs=[_design_to_response(design, sheet.image_url) for design in inventory],
    )


async def _delete_sheet_record(
    sheet_id: UUID,
    db: AsyncSession,
    current_user: User,
):
    result = await db.execute(select(FlashSheet).where(FlashSheet.id == sheet_id))
    sheet = result.scalar_one_or_none()
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Not your flash sheet")

    await db.execute(delete(FlashSheet).where(FlashSheet.id == sheet_id))
    await db.flush()


@router.delete("/sheets/{sheet_id}", status_code=204)
async def delete_sheet(
    sheet_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Delete a flash sheet and its segmented designs."""
    await _delete_sheet_record(sheet_id, db, current_user)


@router.post("/sheets/{sheet_id}/delete", status_code=204)
async def delete_sheet_post_alias(
    sheet_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Compatibility alias for clients or proxies that do not pass DELETE cleanly."""
    await _delete_sheet_record(sheet_id, db, current_user)


@router.get("/{sheet_id}/designs", response_model=FlashDesignListResponse)
async def list_sheet_designs(
    sheet_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List designs on a flash sheet."""
    sheet_result = await db.execute(select(FlashSheet).where(FlashSheet.id == sheet_id))
    sheet = sheet_result.scalar_one_or_none()
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await db.execute(
        select(FlashDesign).where(FlashDesign.sheet_id == sheet_id).order_by(FlashDesign.created_at)
    )
    designs = result.scalars().all()

    return FlashDesignListResponse(
        designs=[_design_to_response(d, sheet.image_url) for d in designs],
        total=len(designs),
    )


@router.post("/{sheet_id}/designs", response_model=FlashDesignResponse, status_code=201)
async def create_design_crop(
    sheet_id: UUID,
    body: FlashDesignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Materialize one artist-selected region as an independent WebP design asset."""
    result = await db.execute(select(FlashSheet).where(FlashSheet.id == sheet_id))
    sheet = result.scalar_one_or_none()
    if not sheet:
        raise HTTPException(status_code=404, detail="Flash sheet not found")
    if not _can_manage_artist_flash(current_user, sheet.artist_id):
        raise HTTPException(status_code=403, detail="Not your flash sheet")

    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        img_resp = await client.get(sheet.image_url)
        if img_resp.status_code != 200:
            raise _flash_error(502, "FLASH_SOURCE_UNAVAILABLE", "Could not fetch the flash sheet image.")

    try:
        normalized = validate_and_normalize_image(img_resp.content, sheet.image_url)
        box = {
            "xmin": round(body.crop_x / 100 * normalized.width),
            "ymin": round(body.crop_y / 100 * normalized.height),
            "xmax": round((body.crop_x + body.crop_w) / 100 * normalized.width),
            "ymax": round((body.crop_y + body.crop_h) / 100 * normalized.height),
        }
        preview = create_design_preview(normalized.data, box)
    except ImageValidationError as exc:
        raise _flash_error(422, exc.code, exc.message) from exc

    object_key = build_object_key(
        folder_path=f"flash/{sheet.artist_id}/previews",
        extension=preview.extension,
    )
    preview_url = await upload_to_drive(
        file_bytes=preview.data,
        filename=object_key.rsplit("/", 1)[-1],
        mime_type=preview.mime_type,
        folder_path=object_key.rsplit("/", 1)[0],
    )
    if not preview_url:
        raise _flash_error(
            503,
            "FLASH_PREVIEW_STORAGE_UNAVAILABLE",
            "Could not save the selected design. The source sheet was preserved.",
        )

    design = FlashDesign(
        sheet_id=sheet.id,
        artist_id=sheet.artist_id,
        crop_x=preview.percent_box["x"],
        crop_y=preview.percent_box["y"],
        crop_w=preview.percent_box["w"],
        crop_h=preview.percent_box["h"],
        image_url=preview_url,
        name=body.name,
        ai_suggested_name=f"Design {sheet.design_count + 1}",
        mask_json={"box": preview.pixel_box, "source": "artist_crop"},
        canvas_state={"status": "artist_selected", "crop_units": "percent", "box": preview.percent_box},
        ai_metadata={
            "role": "design",
            "source": "artist_crop",
            "context_regions": [],
            "normalization_actions": list(normalized.recovery_actions),
            "asset_version": 2,
        },
        gala_sync_enabled=False,
        status="draft",
    )
    db.add(design)
    sheet.status = "segmented"
    sheet.design_count += 1
    await db.flush()
    return _design_to_response(design, sheet.image_url)


@router.patch("/designs/{design_id}", response_model=FlashDesignResponse)
async def update_design(
    design_id: UUID,
    body: FlashDesignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Update a flash design (name, price, time estimate, status)."""
    result = await db.execute(select(FlashDesign).where(FlashDesign.id == design_id))
    design = result.scalar_one_or_none()
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    if not _can_manage_artist_flash(current_user, design.artist_id):
        raise HTTPException(status_code=403, detail="Not your design")

    previous_status = design.status
    sheet = await db.get(FlashSheet, design.sheet_id)

    if body.name is not None:
        design.name = body.name
    if body.price is not None:
        design.price = body.price
    if body.estimated_minutes is not None:
        design.estimated_minutes = body.estimated_minutes
    if body.size_label is not None:
        design.size_label = body.size_label
    if body.placement is not None:
        design.placement = body.placement
    if body.style is not None:
        design.style = body.style
    if body.deposit_amount is not None or body.reclaimable is not None:
        metadata = dict(design.ai_metadata or {})
        if body.deposit_amount is not None:
            metadata["deposit_amount"] = body.deposit_amount
        if body.reclaimable is not None:
            metadata["reclaimable"] = body.reclaimable
        design.ai_metadata = metadata
    if body.mask_json is not None:
        design.mask_json = body.mask_json
    if body.canvas_state is not None:
        design.canvas_state = body.canvas_state
    if body.ai_metadata is not None:
        design.ai_metadata = body.ai_metadata
    if body.gala_sync_enabled is not None:
        design.gala_sync_enabled = body.gala_sync_enabled
    if body.status is not None:
        if body.status not in ("draft", "published", "archived"):
            raise HTTPException(status_code=422, detail="Invalid status")
        design.status = body.status
        if body.status == "published" and sheet:
            sheet.status = "published"
            if previous_status != "published" and _is_sam2_cutout(design):
                _record_model_feedback(
                    db,
                    design=design,
                    sheet=sheet,
                    outcome="accepted",
                    source="artist_publish",
                )
            if previous_status != "published":
                image_url = design.image_url or sheet.image_url
                try:
                    await dispatch_foundry_audience_trigger(
                        db,
                        "flash_published",
                        variables={
                            "drop_name": design.name or sheet.title or "New Flash",
                            "drop_date": f"{datetime.now(UTC).astimezone().strftime('%B')} {datetime.now(UTC).astimezone().day}",
                            "piece_count": "1",
                            "artist_name": getattr(current_user, "name", None) or "Ryozan Studio",
                            "price_range": f"${float(design.price):,.0f}" if design.price is not None else "",
                            "flash_image_1": image_url,
                            "flash_image_2": image_url,
                            "claim_url": f"{get_settings().FRONTEND_URL.rstrip('/')}/flash",
                        },
                        event_id=f"flash-design:{design.id}:published",
                    )
                except Exception:
                    logger.exception("Foundry flash-published dispatch failed for design %s", design.id)
    design.updated_at = datetime.now(UTC)

    await db.flush()
    return _design_to_response(design, sheet.image_url if sheet else None)


@router.delete("/designs/{design_id}", status_code=204)
async def delete_design(
    design_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """Delete an incorrect unclaimed design without deleting its source sheet."""
    result = await db.execute(select(FlashDesign).where(FlashDesign.id == design_id))
    design = result.scalar_one_or_none()
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    if not _can_manage_artist_flash(current_user, design.artist_id):
        raise HTTPException(status_code=403, detail="Not your design")
    if design.status == "claimed" or design.claimed_by or design.booking_id:
        raise HTTPException(
            status_code=409,
            detail="Claimed designs cannot be deleted. Archive the booking first.",
        )

    sheet = await db.get(FlashSheet, design.sheet_id)
    if sheet and _is_sam2_cutout(design):
        _record_model_feedback(
            db,
            design=design,
            sheet=sheet,
            outcome="rejected",
            source="artist_delete",
            correction={"deleted": True},
        )
    await db.delete(design)
    await db.flush()
    if sheet:
        remaining = await db.scalar(
            select(func.count()).select_from(FlashDesign).where(FlashDesign.sheet_id == sheet.id)
        )
        sheet.design_count = int(remaining or 0)
        if sheet.design_count == 0:
            sheet.status = "uploaded"
    return Response(status_code=204)


# ── Active Learning: Segment Correction ─────────────


@router.post("/designs/{design_id}/correct")
async def correct_segment(
    design_id: UUID,
    body: FlashDesignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("artist")),
):
    """
    Artist corrects a SAM2 segment prediction.
    Logs the correction to feedback_samples for active learning.
    The original AI prediction vs the artist's correction becomes training data.
    """
    result = await db.execute(select(FlashDesign).where(FlashDesign.id == design_id))
    design = result.scalar_one_or_none()
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    if not _can_manage_artist_flash(current_user, design.artist_id):
        raise HTTPException(status_code=403, detail="Not your design")

    # Store the original prediction before updating
    original = {
        "box_percent": {
            "x": float(design.crop_x),
            "y": float(design.crop_y),
            "w": float(design.crop_w),
            "h": float(design.crop_h),
        },
        "mask": design.mask_json,
        "asset_version": (design.ai_metadata or {}).get("asset_version")
        if isinstance(design.ai_metadata, dict)
        else None,
    }

    # Apply the correction
    if body.name is not None:
        design.name = body.name
    if body.price is not None:
        design.price = body.price
    if body.estimated_minutes is not None:
        design.estimated_minutes = body.estimated_minutes
    if body.size_label is not None:
        design.size_label = body.size_label
    if body.placement is not None:
        design.placement = body.placement
    if body.style is not None:
        design.style = body.style
    if body.mask_json is not None:
        design.mask_json = body.mask_json
    if body.canvas_state is not None:
        design.canvas_state = body.canvas_state
    if body.ai_metadata is not None:
        design.ai_metadata = body.ai_metadata
    if body.gala_sync_enabled is not None:
        design.gala_sync_enabled = body.gala_sync_enabled
    design.updated_at = datetime.now(UTC)
    await db.flush()

    sheet = await db.get(FlashSheet, design.sheet_id)
    if sheet and _is_sam2_cutout(design):
        _record_model_feedback(
            db,
            design=design,
            sheet=sheet,
            outcome="corrected",
            source="artist_correction",
            correction=body.model_dump(exclude_none=True),
            prediction=original,
        )
        await db.flush()
        logger.info("Segment correction logged for design %s (active learning)", design_id)
    return _design_to_response(design, sheet.image_url if sheet else None)


async def _build_training_manifest(db: AsyncSession) -> dict:
    result = await db.execute(
        select(ModelFeedbackSample)
        .where(ModelFeedbackSample.model_id == "sam2-bttco")
        .order_by(ModelFeedbackSample.created_at.desc())
        .limit(5000)
    )
    latest_by_subject: dict[str, ModelFeedbackSample] = {}
    for sample in result.scalars().all():
        subject_key = str(sample.subject_id or sample.id)
        latest_by_subject.setdefault(subject_key, sample)

    usable = [
        sample
        for sample in latest_by_subject.values()
        if sample.outcome in {"accepted", "corrected"} and sample.artifact_ref
    ]
    return {
        "schema_version": "model-feedback/v1",
        "model_id": "sam2-bttco",
        "sample_count": len(usable),
        "samples": [
            {
                "id": str(sample.id),
                "subject_id": str(sample.subject_id) if sample.subject_id else None,
                "model_version": sample.model_version,
                "outcome": sample.outcome,
                "source_image_url": sample.input_ref,
                "target_cutout_url": sample.artifact_ref,
                "prediction": sample.prediction,
                "correction": sample.correction,
                "created_at": sample.created_at.isoformat(),
            }
            for sample in usable
        ],
    }


@router.get("/feedback/training-manifest")
async def get_training_manifest(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Return the latest reviewed event per design for an operator-triggered training run."""
    del current_user
    return await _build_training_manifest(db)


async def _deliver_training_manifest_to_apex(
    db: AsyncSession,
    *,
    force: bool,
) -> dict:
    """Reconcile one immutable reviewed manifest without starting training."""
    global _last_apex_dataset_digest

    settings = get_settings()
    if not settings.ARK_MODEL_GATEWAY_KEY:
        return {"status": "disabled", "reason": "gateway_credential_not_configured"}
    base_url = settings.ARK_MODEL_GATEWAY_BASE_URL.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    manifest = await _build_training_manifest(db)
    if manifest["sample_count"] == 0:
        return {"status": "empty", "sample_count": 0, "training_started": False}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if not force and digest == _last_apex_dataset_digest:
        return {
            "status": "unchanged",
            "sample_count": manifest["sample_count"],
            "content_sha256": digest,
            "training_started": False,
        }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{base_url}/api/apex/datasets/bttco",
                headers={
                    "Authorization": f"Bearer {settings.ARK_MODEL_GATEWAY_KEY}",
                    "Content-Type": "application/json",
                },
                json=manifest,
            )
    except httpx.HTTPError as exc:
        logger.warning("Apex dataset handoff failed: %s", type(exc).__name__)
        return {
            "status": "failed",
            "reason": type(exc).__name__,
            "sample_count": manifest["sample_count"],
            "training_started": False,
        }
    if response.status_code not in {200, 201}:
        logger.warning("Apex dataset handoff returned HTTP %s", response.status_code)
        return {
            "status": "rejected",
            "status_code": response.status_code,
            "sample_count": manifest["sample_count"],
            "training_started": False,
        }
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    _last_apex_dataset_digest = digest
    receipt = payload.get("data") if isinstance(payload, dict) else None
    return {
        "status": "snapshot_created",
        "sample_count": manifest["sample_count"],
        "content_sha256": digest,
        "training_started": False,
        "apex_receipt": receipt,
    }


async def process_apex_training_manifest_once() -> dict:
    """Periodically reconcile reviewed masks through the existing lifespan worker."""
    global _last_apex_dataset_attempt

    observed = time.monotonic()
    if observed - _last_apex_dataset_attempt < _APEX_DATASET_SYNC_INTERVAL_SECONDS:
        return {"status": "not_due", "training_started": False}
    _last_apex_dataset_attempt = observed
    async with async_session() as session:
        return await _deliver_training_manifest_to_apex(session, force=False)


@router.post("/feedback/push-to-apex")
async def push_training_manifest_to_apex(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """Snapshot reviewed masks in Ark Apex without starting a training job."""
    del current_user
    result = await _deliver_training_manifest_to_apex(db, force=True)
    if result["status"] == "disabled":
        raise _flash_error(
            503,
            "FLASH_APEX_NOT_CONFIGURED",
            "Ark Apex is unavailable because the gateway credential is not configured.",
        )
    if result["status"] == "failed":
        raise _flash_error(
            502,
            "FLASH_APEX_UNAVAILABLE",
            "Ark Apex could not receive the reviewed dataset.",
        )
    if result["status"] == "rejected":
        raise _flash_error(
            502,
            "FLASH_APEX_REJECTED",
            "Ark Apex rejected the reviewed dataset snapshot.",
        )
    return result


# ── Collector Endpoints ──────────────────────────────


@router.get("/store", response_model=FlashDesignListResponse)
async def browse_store(
    artist_id: str | None = None,
    style: str | None = None,
    skip: int = 0,
    limit: int = Query(default=30, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Browse published flash designs (collector storefront)."""
    filters = [FlashDesign.status == "published"]
    if artist_id:
        filters.append(FlashDesign.artist_id == artist_id)
    if style:
        filters.append(FlashDesign.style == style)

    count_result = await db.execute(select(func.count()).select_from(FlashDesign).where(*filters))
    total = count_result.scalar()

    result = await db.execute(
        select(FlashDesign).where(*filters).order_by(FlashDesign.created_at.desc()).offset(skip).limit(limit)
    )
    designs = result.scalars().all()

    return FlashDesignListResponse(
        designs=await _design_responses_with_sources(db, designs),
        total=total,
    )


@router.post("/designs/{design_id}/claim")
async def claim_design(
    design_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retired: inventory may only be claimed by the atomic booking and payment flow."""
    del design_id, db, current_user
    raise HTTPException(
        status_code=410,
        detail="Claim-only flash reservations are retired. Continue through the artist booking flow.",
    )


# ── Multi-design checkout ─────────────────────────────


class FlashCheckoutRequest(BaseModel):
    """Claim multiple flash designs in one checkout."""

    design_ids: list[UUID] = Field(min_length=1, max_length=10)


@router.post("/checkout")
async def flash_checkout(
    body: FlashCheckoutRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retired: claiming inventory without a booking or payment artifact is unsafe."""
    del body, db, current_user
    raise HTTPException(
        status_code=410,
        detail="Multi-design claim checkout is retired. Book each available design through its artist.",
    )


@router.get("/store/public", response_model=FlashDesignListResponse)
async def browse_store_public(
    artist_slug: str | None = None,
    style: str | None = None,
    skip: int = 0,
    limit: int = Query(default=30, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Public storefront — browse published flash designs without auth.
    Used by the Shop page for both logged-in and guest visitors.
    """
    filters = [FlashDesign.status == "published"]
    if artist_slug:
        from app.models.user import User

        artist_result = await db.execute(select(User.id).where(User.slug == artist_slug))
        artist_id = artist_result.scalar_one_or_none()
        if not artist_id:
            raise HTTPException(status_code=404, detail="Artist not found")
        filters.append(FlashDesign.artist_id == artist_id)
    if style:
        filters.append(FlashDesign.style == style)

    count_result = await db.execute(select(func.count()).select_from(FlashDesign).where(*filters))
    total = count_result.scalar()

    result = await db.execute(
        select(FlashDesign).where(*filters).order_by(FlashDesign.created_at.desc()).offset(skip).limit(limit)
    )
    designs = result.scalars().all()

    return FlashDesignListResponse(
        designs=await _design_responses_with_sources(db, designs),
        total=total,
    )


def _public_flash_asset_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    configured_host = (get_settings().R2_PUBLIC_DOMAIN or "").strip().lower()
    return parsed.hostname == "assets.ryozan.app" or bool(
        configured_host and parsed.hostname.lower() == configured_host
    )


@router.get("/public/designs/{design_id}/image")
async def get_public_design_image(
    design_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return one published design asset through the existing same-origin API path."""
    result = await db.execute(
        select(FlashDesign, FlashSheet.image_url)
        .join(FlashSheet, FlashSheet.id == FlashDesign.sheet_id)
        .where(FlashDesign.id == design_id, FlashDesign.status == "published")
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Published flash design not found")

    design, sheet_image_url = row
    asset_url = design.image_url or sheet_image_url
    if not asset_url or not _public_flash_asset_allowed(asset_url):
        raise HTTPException(status_code=502, detail="Published flash asset is unavailable")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            asset = await client.get(asset_url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Published flash asset could not be loaded") from exc

    media_type = asset.headers.get("content-type", "").split(";", 1)[0].lower()
    if asset.status_code != 200 or media_type not in _PUBLIC_FLASH_IMAGE_TYPES:
        raise HTTPException(status_code=502, detail="Published flash asset could not be loaded")
    if len(asset.content) > _MAX_PUBLIC_FLASH_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Published flash asset is too large for preview")

    return Response(
        content=asset.content,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/public/sheets/{artist_slug}", response_model=PublicSheetWallResponse)
async def get_public_sheet_wall(
    artist_slug: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: get published flash sheets for an artist, each with their published designs.
    Returns sheet wall data for the artist page flash tab.
    """
    artist_result = await db.execute(
        select(User.id, User.name, User.flash_deposit_amount, User.allow_prepay_full).where(User.slug == artist_slug)
    )
    artist_row = artist_result.first()
    if not artist_row:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist_id, artist_name, flash_deposit_amount, allow_prepay_full = artist_row

    sheets_result = await db.execute(
        select(FlashSheet)
        .join(FlashDesign, FlashDesign.sheet_id == FlashSheet.id)
        .where(FlashSheet.artist_id == artist_id)
        .where(FlashDesign.status == "published")
        .distinct()
        .order_by(FlashSheet.created_at.desc())
    )
    sheets = sheets_result.scalars().all()

    public_sheets = []
    for sheet in sheets:
        designs_result = await db.execute(
            select(FlashDesign)
            .where(FlashDesign.sheet_id == sheet.id)
            .where(FlashDesign.status == "published")
            .order_by(FlashDesign.created_at)
        )
        designs = designs_result.scalars().all()
        public_sheets.append(
            {
                "id": str(sheet.id),
                "image_url": sheet.image_url,
                "title": sheet.title,
                "design_count": len(designs),
                "designs": [_design_to_response(d, sheet.image_url) for d in designs],
            }
        )

    return PublicSheetWallResponse(
        sheets=public_sheets,
        artist_slug=artist_slug,
        artist_name=artist_name or artist_slug,
        flash_deposit_amount=(float(flash_deposit_amount) if flash_deposit_amount is not None else 100.0),
        allow_prepay_full=allow_prepay_full or False,
    )


# ── Flash booking ─────────────────────────────────────


FLASH_REWARD_BASE_RATE = 0.10  # 10% of price in RYO
FLASH_REWARD_MULTIPLIER = 1.5  # flash bookings earn more than custom


@dataclass(frozen=True)
class FlashChargeDecision:
    """One owner for per-design deposit, legacy artist fallback, and optional prepay."""

    deposit_amount: float
    charge_dollars: float
    checkout_required: bool
    is_deposit: bool


def _resolve_flash_charge(
    *,
    configured_design_deposit: float | None,
    flash_deposit_amount: float | None,
    pay_full: bool,
    allow_prepay_full: bool,
    design_price: float,
) -> FlashChargeDecision:
    deposit_amount = (
        float(configured_design_deposit)
        if configured_design_deposit is not None
        else float(flash_deposit_amount)
        if flash_deposit_amount is not None
        else 100.0
    )
    if pay_full and allow_prepay_full and design_price > 0:
        return FlashChargeDecision(
            deposit_amount=deposit_amount,
            charge_dollars=design_price,
            checkout_required=True,
            is_deposit=False,
        )
    if deposit_amount > 0:
        return FlashChargeDecision(
            deposit_amount=deposit_amount,
            charge_dollars=deposit_amount,
            checkout_required=True,
            is_deposit=True,
        )
    return FlashChargeDecision(
        deposit_amount=0,
        charge_dollars=0,
        checkout_required=False,
        is_deposit=False,
    )


async def _resolve_flash_slot_hold(
    db: AsyncSession,
    *,
    hold_token: str | None,
    artist_id: UUID,
    scheduled_date: _date,
    scheduled_time: str,
) -> BookingSlotHold | None:
    """Return the caller's active flash hold for this exact slot, or None.

    A missing, stale, consumed or mismatched token falls back to the plain
    live availability check rather than failing the booking.
    """
    if not hold_token:
        return None
    result = await db.execute(
        select(BookingSlotHold)
        .where(
            BookingSlotHold.hold_token == hold_token,
            BookingSlotHold.artist_id == artist_id,
            BookingSlotHold.scheduled_date == scheduled_date,
            BookingSlotHold.scheduled_time == scheduled_time[:5],
            BookingSlotHold.booking_kind == "flash",
            BookingSlotHold.status == "active",
            BookingSlotHold.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _assert_flash_slot_available(
    db: AsyncSession,
    *,
    artist: User,
    scheduled_date: _date,
    scheduled_time: str,
    duration_minutes: int,
    exclude_hold_token: str | None = None,
) -> None:
    _, buffer_minutes, min_notice_hours, max_advance_days, allow_same_day = _artist_booking_rules(artist)
    busy_blocks: list[dict] = []
    if artist_calendar_connected(artist):
        try:
            busy_blocks = await get_availability(
                start_date=scheduled_date.isoformat(),
                end_date=scheduled_date.isoformat(),
                calendar_id=artist_calendar_id(artist),
                refresh_token=artist.google_refresh_token,
            )
        except Exception as exc:
            logger.error("Flash booking calendar read failed for artist %s: %s", artist.id, exc)
            raise HTTPException(status_code=502, detail="Calendar availability check failed") from exc

    available = await _slot_is_available(
        db,
        artist_id=artist.id,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=duration_minutes,
        buffer_minutes=buffer_minutes,
        minimum_notice_hours=min_notice_hours,
        max_advance_days=max_advance_days,
        allow_same_day=allow_same_day,
        busy_blocks=busy_blocks,
        exclude_hold_token=exclude_hold_token,
    )
    if not available:
        raise HTTPException(status_code=409, detail="Slot is no longer available")


@router.post("/book", response_model=FlashBookResponse)
async def book_flash_design(
    body: FlashBookRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Public: book a flash design.

    Creates a booking record and a Stripe Checkout session.
    Supports deposit-only or full payment (if artist allows prepay).
    Rewards RYO on booking creation (1.5x rate vs custom bookings).
    Auto-creates a collector account if the email is new.
    """
    settings = get_settings()
    stripe.api_key = settings.STRIPE_SECRET_KEY

    # Resolve design
    try:
        design_uuid = _UUID(body.design_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid design_id") from exc

    design_result = await db.execute(select(FlashDesign).where(FlashDesign.id == design_uuid).with_for_update())
    design = design_result.scalar_one_or_none()
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    if design.status != "published":
        raise HTTPException(status_code=422, detail="Design is not available")
    if design.claimed_by:
        raise HTTPException(status_code=409, detail="Design already claimed")

    # Resolve artist + settings
    artist_result = await db.execute(
        select(
            User.id,
            User.name,
            User.slug,
            User.flash_deposit_amount,
            User.allow_prepay_full,
        ).where(User.id == design.artist_id)
    )
    artist_row = artist_result.first()
    if not artist_row:
        raise HTTPException(status_code=404, detail="Artist not found")
    (
        artist_id,
        artist_name,
        artist_slug,
        flash_deposit_amount,
        allow_prepay_full,
    ) = artist_row

    artist = await db.get(User, artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    design_metadata = design.ai_metadata if isinstance(design.ai_metadata, dict) else {}
    configured_design_deposit = design_metadata.get("deposit_amount")
    design_price = float(design.price) if design.price else 0.0
    charge = _resolve_flash_charge(
        configured_design_deposit=configured_design_deposit,
        flash_deposit_amount=flash_deposit_amount,
        pay_full=body.pay_full,
        allow_prepay_full=allow_prepay_full,
        design_price=design_price,
    )
    deposit_amount = charge.deposit_amount
    duration_minutes = design.estimated_minutes or 60

    try:
        sched_date = _date.fromisoformat(body.scheduled_date)
        sched_time = _parse_local_time(body.scheduled_time)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid date or time format") from exc
    # An active hold for this slot belongs to this collector: it must not count
    # as a conflict against its own booking, and it is consumed below so it
    # stops blocking the calendar once the booking exists.
    slot_hold = await _resolve_flash_slot_hold(
        db,
        hold_token=body.hold_token,
        artist_id=artist.id,
        scheduled_date=sched_date,
        scheduled_time=sched_time,
    )
    await _assert_flash_slot_available(
        db,
        artist=artist,
        scheduled_date=sched_date,
        scheduled_time=sched_time,
        duration_minutes=duration_minutes,
        exclude_hold_token=slot_hold.hold_token if slot_hold else None,
    )

    charge_dollars = charge.charge_dollars
    is_deposit = charge.is_deposit
    service_price_cents = int(charge_dollars * 100)
    charge_cents = customer_price_cents(service_price_cents)

    # RYO reward: 10% of design price * 1.5x flash multiplier, minimum 10
    bttcoin_reward = max(10, int(design_price * FLASH_REWARD_BASE_RATE * FLASH_REWARD_MULTIPLIER))

    # Auto-create or find collector account
    normalized_email = normalize_email(body.guest_email)
    client_user = await find_user_by_email(db, normalized_email)
    account_created = client_user is None
    if not client_user:
        temp_password = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        client_user = User(
            email=normalized_email,
            password_hash=hash_password(temp_password),
            name=body.guest_name,
            role="collector",
            must_reset_password=True,
            marketing_opt_in=False,
            bttcoin_balance=0,
        )
        db.add(client_user)
        await db.flush()

    # Generate booking reference
    ref_code = "FLX-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

    booking = Booking(
        client_id=client_user.id,
        artist_id=artist_id,
        guest_name=body.guest_name,
        guest_email=normalized_email,
        guest_phone=body.guest_phone,
        reference_code=ref_code,
        status="pending",
        description=f"Flash: {design.name or 'Flash design'}",
        style_preference=design.style,
        placement=design.placement,
        reference_urls=json.dumps([design.image_url]) if design.image_url else None,
        scheduled_date=sched_date,
        scheduled_time=sched_time,
        duration_minutes=duration_minutes,
        deposit_amount=deposit_amount if deposit_amount > 0 else None,
        estimated_total=design_price if design_price > 0 else None,
    )
    db.add(booking)
    await db.flush()

    # Claim the design
    booking_id = booking.id
    if slot_hold:
        slot_hold.status = "consumed"
        slot_hold.booking_id = booking_id
        slot_hold.updated_at = datetime.now(UTC)
    design.claimed_by = client_user.id
    design.claimed_at = datetime.now(UTC)
    design.status = "claimed"
    design.booking_id = booking_id
    design.updated_at = datetime.now(UTC)

    if not charge.checkout_required:
        await db.commit()
        if account_created:
            background_tasks.add_task(
                send_account_setup_email,
                user_id=client_user.id,
                email=client_user.email,
                password_hash=client_user.password_hash,
                name=client_user.name,
            )
        logger.info(
            "Flash booking request created without a deposit: %s | design=%s",
            ref_code,
            design.id,
        )
        return FlashBookResponse(
            booking_reference=ref_code,
            checkout_url=None,
            checkout_required=False,
            amount_cents=0,
            is_deposit=False,
            bttcoin_reward=0,
            artist_name=artist_name or "",
            design_name=design.name or "Flash Tattoo Design",
            scheduled_date=body.scheduled_date,
            scheduled_time=sched_time,
        )

    # Create Stripe Checkout Session
    design_label = design.name or "Flash Tattoo Design"
    success_url = f"{settings.FRONTEND_URL}/book/confirm?checkout=success&ref={ref_code}"
    cancel_url = f"{settings.FRONTEND_URL}/book/confirm?checkout=cancelled&ref={ref_code}"

    session_params: dict = {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"{'Deposit — ' if is_deposit else ''}{design_label}",
                        "description": (
                            f"Artist: {artist_name} | "
                            f"{'Deposit toward ' if is_deposit else 'Full payment for '}flash tattoo"
                            f"{f' — {design.style}' if design.style else ''}"
                        ),
                    },
                    "unit_amount": charge_cents,
                },
                "quantity": 1,
            }
        ],
        "metadata": {
            "flash_booking": "true",
            "checkout_type": "flash_deposit",
            "booking_id": str(booking_id),
            "reference_code": ref_code,
            "design_id": str(design.id),
            "artist_id": str(artist_id),
            "artist_slug": artist_slug or "",
            "bttcoin_reward": str(bttcoin_reward),
            "is_deposit": str(is_deposit).lower(),
        },
        "customer_email": normalized_email,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }

    routing = resolve_charge_routing(
        artist,
        charge_cents,
        artist_share_base_cents=service_price_cents,
    )
    session_params = routing.apply_to(session_params)
    session_params = apply_customer_receipt(
        session_params,
        customer_email=normalized_email,
        description=(f"Ryozan {'tattoo deposit' if is_deposit else 'flash tattoo payment'} - {ref_code}"),
        metadata={
            "checkout_type": "flash_deposit",
            "booking_id": str(booking_id),
            "reference_code": ref_code,
            "artist_id": str(artist_id),
            "routing_destination": routing.destination,
            "routing_reason": routing.reason,
            **routing.receipt_metadata(),
        },
    )

    try:
        session = get_stripe_client().v1.checkout.sessions.create(
            session_params,
            routing.request_options(idempotency_key=f"flash-booking-deposit-{booking_id}"),
        )
        booking.stripe_checkout_session_id = session.id
        # Checkout sessions expose the payment intent as soon as Stripe
        # creates one; storing it lets payment_intent.succeeded find the
        # booking even if checkout.session.completed is missed.
        if getattr(session, "payment_intent", None):
            booking.stripe_payment_intent_id = session.payment_intent
    except Exception as exc:
        logger.error("Stripe session creation failed for flash booking %s: %s", ref_code, exc)
        await db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Payment checkout could not be created. The flash design was not claimed.",
        ) from exc

    await db.commit()

    logger.info(
        "Flash booking created: %s | design=%s | $%.2f | RYO+%d",
        ref_code,
        design.id,
        charge_dollars,
        bttcoin_reward,
    )
    if account_created:
        background_tasks.add_task(
            send_account_setup_email,
            user_id=client_user.id,
            email=client_user.email,
            password_hash=client_user.password_hash,
            name=client_user.name,
        )

    return FlashBookResponse(
        booking_reference=ref_code,
        checkout_url=session.url,
        checkout_required=True,
        amount_cents=charge_cents,
        is_deposit=is_deposit,
        bttcoin_reward=bttcoin_reward,
        artist_name=artist_name or "",
        design_name=design_label,
        scheduled_date=body.scheduled_date,
        scheduled_time=sched_time,
    )
