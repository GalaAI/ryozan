"""
Image validation and normalization for artist uploads (flash sheets, references).

This is a net-new service because the repo had no image-processing layer: storage.py
is pure R2 transport and flash.py is HTTP/DB orchestration. Neither verifies image
content, corrects EXIF orientation, or guards against decompression bombs. Centralizing
that here keeps the browser-reported MIME type untrusted and gives every upload path a
single, testable normalization contract.

Contract:
  validate_and_normalize_image(raw_bytes, declared_filename) -> NormalizedImage
    - Verifies the bytes are a real, decodable JPEG/PNG/WebP/MPO (Pillow, not the MIME header)
    - Recovers the known UTF-8/Latin-1 binary recoding failure before decoding
    - Converts an MPO primary frame to a browser-safe JPEG
    - Rejects unsupported formats (HEIC, GIF, TIFF, BMP, SVG, ...) with a stable code
    - Applies a decompression-bomb guard and a max-dimension cap
    - Corrects EXIF orientation and strips remaining metadata
    - Re-encodes to a browser-safe JPEG/PNG/WebP with the correct MIME type
    - Returns the normalized bytes, mime type, safe extension, and dimensions

Errors are raised as ImageValidationError with a machine code so the endpoint can map
them to stable API error codes without this module importing FastAPI.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger("bttco.image")

# Formats we accept on input and can serve safely to browsers.
# Pillow format name -> (canonical mime, safe file extension)
SUPPORTED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", "jpg"),
    # MPO is a multi-picture JPEG container. Browsers do not reliably render it, so
    # only its primary frame is retained and re-encoded as an ordinary JPEG.
    "MPO": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}

# Human-facing list for error messages, kept in sync with SUPPORTED_FORMATS.
SUPPORTED_LABEL = "JPEG, PNG, or WebP"

# Hard byte ceiling. Mirrors the 20 MB product limit; enforced here so the check is
# shared by every caller instead of living only in the flash endpoint.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Decompression-bomb guard. A malicious file can be small on disk but claim enormous
# pixel dimensions. Pillow raises DecompressionBombError past MAX_IMAGE_PIXELS; we set a
# firm cap well under Pillow's ~178M default and reject early on declared dimensions too.
MAX_IMAGE_PIXELS = 50_000_000  # 50 megapixels (e.g. ~7000x7000)
# Longest edge we will keep. Larger images are downscaled, not rejected, so a legitimate
# high-resolution flash scan still uploads instead of erroring.
MAX_EDGE_PX = 4096

# Apply the pixel guard process-wide so Image.open() itself enforces it.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageValidationError(Exception):
    """Raised when an upload is not a usable image. Carries a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class NormalizedImage:
    """A verified, re-encoded image ready for storage."""

    data: bytes
    mime_type: str
    extension: str  # safe extension without a leading dot, e.g. "jpg"
    width: int
    height: int
    source_format: str  # Pillow format name of the original, e.g. "JPEG"
    recovery_actions: tuple[str, ...] = ()


@dataclass
class CroppedPreview:
    """An immutable browser-safe design crop plus source-space coordinates."""

    data: bytes
    mime_type: str
    extension: str
    width: int
    height: int
    pixel_box: dict[str, int]
    percent_box: dict[str, float]


def _guess_extension_from_name(declared_filename: str | None) -> str | None:
    if not declared_filename or "." not in declared_filename:
        return None
    return declared_filename.rsplit(".", 1)[-1].strip().lower() or None


def _sniff_browser_image(raw_bytes: bytes) -> bool:
    return (
        raw_bytes.startswith(b"\xff\xd8\xff")
        or raw_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        or (len(raw_bytes) >= 12 and raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP")
    )


def _recover_text_reencoded_image(raw_bytes: bytes) -> bytes | None:
    """Reverse a binary -> Latin-1 text -> UTF-8 corruption when it is unambiguous.

    This exact failure occurred in production when JPEG bytes were accidentally handled
    as text before R2 storage. Recovery is deliberately narrow: the entire payload must
    be valid UTF-8, every character must fit Latin-1, and the recovered bytes must begin
    with a supported browser-image signature. Arbitrary unreadable input is never guessed.
    """
    try:
        recovered = raw_bytes.decode("utf-8").encode("latin-1")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    if recovered == raw_bytes or not _sniff_browser_image(recovered):
        return None
    return recovered


def _verified_image_bytes(raw_bytes: bytes) -> tuple[bytes, str, tuple[str, ...]]:
    candidates: list[tuple[bytes, tuple[str, ...]]] = [(raw_bytes, ())]
    recovered = _recover_text_reencoded_image(raw_bytes)
    if recovered is not None:
        candidates.append((recovered, ("reversed_utf8_latin1_recode",)))

    last_error: Exception | None = None
    for candidate, recovery_actions in candidates:
        try:
            probe = Image.open(io.BytesIO(candidate))
            source_format = (probe.format or "").upper()
            probe.verify()
            return candidate, source_format, recovery_actions
        except Image.DecompressionBombError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            last_error = exc
    raise UnidentifiedImageError("No supported image decoder accepted the payload") from last_error


def validate_and_normalize_image(
    raw_bytes: bytes,
    declared_filename: str | None = None,
) -> NormalizedImage:
    """
    Verify and normalize an uploaded image.

    Trusts the bytes, never the browser MIME type or file extension. The extension is
    used only as a hint in error messages, never to decide validity.

    Raises ImageValidationError with one of:
      - FLASH_EMPTY_FILE        no bytes
      - FLASH_FILE_TOO_LARGE    over MAX_UPLOAD_BYTES
      - FLASH_UNREADABLE_IMAGE  bytes are not a decodable image at all
      - FLASH_UNSUPPORTED_FORMAT decodable but not JPEG/PNG/WebP (e.g. HEIC, GIF)
      - FLASH_IMAGE_TOO_LARGE   pixel dimensions exceed the decompression-bomb guard
    """
    if not raw_bytes:
        raise ImageValidationError("FLASH_EMPTY_FILE", "The uploaded file was empty.")

    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ImageValidationError(
            "FLASH_FILE_TOO_LARGE",
            "That file is too large. Uploads must be 20 MB or smaller.",
        )

    ext_hint = _guess_extension_from_name(declared_filename)

    try:
        # verify() checks structural integrity without fully decoding pixels. The
        # recovery helper also handles the known production binary-as-text corruption.
        image_bytes, source_format, recovery_actions = _verified_image_bytes(raw_bytes)
    except Image.DecompressionBombError as exc:
        raise ImageValidationError(
            "FLASH_IMAGE_TOO_LARGE",
            "That image is too large in pixel dimensions to process safely.",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        # HEIC (common iPhone default) usually lands here because Pillow can't decode it
        # without a plugin. Give the format-specific message so the artist knows to convert.
        if ext_hint in {"heic", "heif"}:
            raise ImageValidationError(
                "FLASH_UNSUPPORTED_FORMAT",
                f"HEIC images are not supported yet. Upload a {SUPPORTED_LABEL} image "
                "(on iPhone, take the shot as 'Most Compatible' or export as JPEG).",
            ) from exc
        raise ImageValidationError(
            "FLASH_UNREADABLE_IMAGE",
            f"That file is not a readable image. Upload a {SUPPORTED_LABEL} image.",
        ) from exc

    if source_format not in SUPPORTED_FORMATS:
        raise ImageValidationError(
            "FLASH_UNSUPPORTED_FORMAT",
            f"{source_format or 'That'} images are not supported. Upload a {SUPPORTED_LABEL} image.",
        )

    # verify() leaves the file object unusable; reopen for the real decode + transform.
    try:
        image = Image.open(io.BytesIO(image_bytes))
        if source_format == "MPO":
            image.seek(0)
        # exif_transpose bakes the EXIF orientation into pixels then drops the tag, so the
        # image renders upright everywhere (mobile Safari respects EXIF, many <img> do not).
        image = ImageOps.exif_transpose(image)
    except Image.DecompressionBombError as exc:
        raise ImageValidationError(
            "FLASH_IMAGE_TOO_LARGE",
            "That image is too large in pixel dimensions to process safely.",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageValidationError(
            "FLASH_UNREADABLE_IMAGE",
            f"That file is not a readable image. Upload a {SUPPORTED_LABEL} image.",
        ) from exc

    # Downscale oversized images instead of rejecting them, so a legit large scan uploads.
    if max(image.size) > MAX_EDGE_PX:
        image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.LANCZOS)

    normalized = _reencode(image, source_format, recovery_actions)

    logger.info(
        "Normalized upload: %s %dx%d -> %s %d bytes",
        source_format,
        normalized.width,
        normalized.height,
        normalized.mime_type,
        len(normalized.data),
    )
    return normalized


def _reencode(
    image: Image.Image,
    source_format: str,
    recovery_actions: tuple[str, ...] = (),
) -> NormalizedImage:
    """
    Re-encode into a browser-safe file with metadata stripped.

    Building a fresh output image with only the pixel data (no info/exif dict) is what
    strips GPS, camera, and other metadata. Alpha is preserved for PNG/WebP and flattened
    onto white for JPEG (JPEG has no alpha channel).
    """
    mime_type, extension = SUPPORTED_FORMATS[source_format]

    if source_format in {"JPEG", "MPO"}:
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=88, optimize=True)
        width, height = rgb.size
    elif source_format == "PNG":
        rgba = image.convert("RGBA") if image.mode in ("P", "LA") else image
        buffer = io.BytesIO()
        rgba.save(buffer, format="PNG", optimize=True)
        width, height = rgba.size
    else:  # WEBP
        out = image.convert("RGBA") if image.mode in ("P", "LA") else image
        buffer = io.BytesIO()
        out.save(buffer, format="WEBP", quality=90, method=4)
        width, height = out.size

    return NormalizedImage(
        data=buffer.getvalue(),
        mime_type=mime_type,
        extension=extension,
        width=width,
        height=height,
        source_format=source_format,
        recovery_actions=recovery_actions + (("selected_mpo_primary_frame",) if source_format == "MPO" else ()),
    )


def create_design_preview(
    image_bytes: bytes,
    box: dict,
    *,
    max_edge_px: int = 1600,
) -> CroppedPreview:
    """Crop one detector box into a metadata-free WebP preview.

    Detector coordinates are pixels on ``image_bytes``. The returned percentage box
    remains the source-of-truth geometry for editors while the WebP is the immutable,
    browser-ready display asset used by storefronts and future UIs.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        if (image.format or "").upper() == "MPO":
            image.seek(0)
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageValidationError(
            "FLASH_UNREADABLE_IMAGE",
            "The stored sheet could not be decoded to create design previews.",
        ) from exc

    width, height = image.size
    xmin = max(0, min(width - 1, int(round(float(box.get("xmin", 0))))))
    ymin = max(0, min(height - 1, int(round(float(box.get("ymin", 0))))))
    xmax = max(xmin + 1, min(width, int(round(float(box.get("xmax", width))))))
    ymax = max(ymin + 1, min(height, int(round(float(box.get("ymax", height))))))
    if xmax <= xmin or ymax <= ymin:
        raise ImageValidationError("FLASH_INVALID_CROP", "A detected design had an invalid crop region.")

    crop = image.crop((xmin, ymin, xmax, ymax))
    if max(crop.size) > max_edge_px:
        crop.thumbnail((max_edge_px, max_edge_px), Image.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, format="WEBP", quality=90, method=4)

    return CroppedPreview(
        data=buffer.getvalue(),
        mime_type="image/webp",
        extension="webp",
        width=crop.width,
        height=crop.height,
        pixel_box={"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
        percent_box={
            "x": round(xmin / width * 100, 4),
            "y": round(ymin / height * 100, 4),
            "w": round((xmax - xmin) / width * 100, 4),
            "h": round((ymax - ymin) / height * 100, 4),
        },
    )


def create_design_cutout_preview(
    cutout_png_bytes: bytes,
    box: dict,
    *,
    source_width: int,
    source_height: int,
    max_edge_px: int = 1600,
) -> CroppedPreview:
    """Validate and normalize one model-produced transparent PNG cutout."""
    if not cutout_png_bytes or len(cutout_png_bytes) > MAX_UPLOAD_BYTES:
        raise ImageValidationError("FLASH_INVALID_CUTOUT", "The model cutout was empty or too large.")
    try:
        probe = Image.open(io.BytesIO(cutout_png_bytes))
        if (probe.format or "").upper() != "PNG":
            raise ValueError("cutout is not PNG")
        probe.verify()
        image = Image.open(io.BytesIO(cutout_png_bytes)).convert("RGBA")
        image.load()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageValidationError("FLASH_INVALID_CUTOUT", "The model cutout was not a readable PNG.") from exc

    xmin = max(0, min(source_width - 1, int(round(float(box.get("xmin", 0))))))
    ymin = max(0, min(source_height - 1, int(round(float(box.get("ymin", 0))))))
    xmax = max(xmin + 1, min(source_width, int(round(float(box.get("xmax", source_width))))))
    ymax = max(ymin + 1, min(source_height, int(round(float(box.get("ymax", source_height))))))
    expected_size = (xmax - xmin, ymax - ymin)
    if image.size != expected_size:
        raise ImageValidationError("FLASH_INVALID_CUTOUT", "The model cutout did not match its source region.")
    alpha = image.getchannel("A")
    alpha_min, alpha_max = alpha.getextrema()
    if alpha_min == alpha_max:
        raise ImageValidationError("FLASH_INVALID_CUTOUT", "The model cutout did not contain a usable alpha mask.")

    if max(image.size) > max_edge_px:
        image.thumbnail((max_edge_px, max_edge_px), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return CroppedPreview(
        data=buffer.getvalue(),
        mime_type="image/png",
        extension="png",
        width=image.width,
        height=image.height,
        pixel_box={"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
        percent_box={
            "x": round(xmin / source_width * 100, 4),
            "y": round(ymin / source_height * 100, 4),
            "w": round((xmax - xmin) / source_width * 100, 4),
            "h": round((ymax - ymin) / source_height * 100, 4),
        },
    )
