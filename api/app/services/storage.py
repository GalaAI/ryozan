"""
File storage service — Cloudflare R2 (S3-compatible).

All media (avatars, portfolios, flash sheets, references, waivers)
is stored in the R2 bucket named by R2_BUCKET_NAME.

All functions follow the never-raise pattern — returns None on failure,
logs the error. This keeps callers simple (check for None, handle gracefully).
"""

import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.core.config import get_settings

logger = logging.getLogger("ryozan.storage")


def build_object_key(folder_path: str, extension: str) -> str:
    """
    Build a collision-resistant R2 object key.

    Raw client filenames were used directly as keys, so two artists uploading
    'flash.jpg' into the same folder overwrote each other and re-uploads of the same
    name clobbered prior objects. This composes the folder prefix with a UTC datestamp
    and a random token, so every upload gets a unique, path-safe key. The original
    filename is preserved as sheet metadata, not as the storage key.

    Args:
        folder_path: Slash-separated prefix (e.g. 'flash/artist-slug').
        extension: Safe extension without a leading dot (e.g. 'jpg').

    Returns:
        A key like 'flash/artist-slug/20260715-a1b2c3d4e5f6.jpg'.
    """
    prefix = folder_path.strip("/")
    datestamp = datetime.now(UTC).strftime("%Y%m%d")
    token = secrets.token_hex(6)
    safe_ext = (extension or "bin").lstrip(".").lower()
    name = f"{datestamp}-{token}.{safe_ext}"
    return f"{prefix}/{name}" if prefix else name


# ═══════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════


async def upload_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    folder_path: str,
) -> str | None:
    """
    Upload a file to Cloudflare R2.

    Args:
        file_bytes: Raw file content
        filename: Name for the uploaded file (e.g. 'photo_id.jpg')
        mime_type: MIME type (e.g. 'image/jpeg')
        folder_path: Slash-separated path prefix (e.g. 'avatars/user-id')

    Returns:
        Public URL on success, None on failure.
        Never raises.
    """
    return await _upload_r2(file_bytes, filename, mime_type, folder_path)


async def delete_file(url: str, *, allowed_prefix: str | None = None) -> bool:
    """
    Delete a file by its URL.

    Returns True on success, False on failure. Never raises.
    """
    hostname = (urlsplit(url).hostname or "").lower()
    if hostname.endswith(".r2.cloudflarestorage.com") or hostname.endswith(".r2.dev") or _is_r2_custom_domain(url):
        if allowed_prefix:
            object_key = _r2_object_key(url)
            prefix = f"{allowed_prefix.strip('/')}/"
            if not object_key or not object_key.startswith(prefix):
                logger.warning("R2 delete rejected because the object is outside the allowed prefix")
                return False
        return await _delete_r2(url)
    logger.warning("Unknown storage URL, cannot delete: %s", url[:100])
    return False


def _is_r2_custom_domain(url: str) -> bool:
    """Check if URL is from our R2 custom domain."""
    settings = get_settings()
    configured = (settings.R2_PUBLIC_DOMAIN or "").strip()
    if not configured:
        return False
    configured_host = urlsplit(configured if "://" in configured else f"//{configured}").hostname
    return bool(configured_host and (urlsplit(url).hostname or "").lower() == configured_host.lower())


# ═══════════════════════════════════════════════════════
#  Cloudflare R2 backend (S3-compatible)
# ═══════════════════════════════════════════════════════


def _r2_sign(method: str, path: str, headers: dict, secret_key: str) -> str:
    """Generate AWS Signature V4 for R2 (simplified for PUT/DELETE)."""
    now = datetime.now(UTC)
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    headers["x-amz-date"] = amz_date
    headers["x-amz-content-sha256"] = "UNSIGNED-PAYLOAD"

    settings = get_settings()
    region = "auto"
    service = "s3"

    # Canonical request
    signed_headers = sorted(k.lower() for k in headers)
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in signed_headers)
    signed_headers_str = ";".join(signed_headers)

    canonical_request = f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers_str}\nUNSIGNED-PAYLOAD"

    # String to sign
    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n" + hashlib.sha256(canonical_request.encode()).hexdigest()
    )

    # Signing key
    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _sign(f"AWS4{secret_key}".encode(), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    return (
        f"AWS4-HMAC-SHA256 Credential={settings.R2_ACCESS_KEY_ID}/{credential_scope}, "
        f"SignedHeaders={signed_headers_str}, Signature={signature}"
    )


async def _upload_r2(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    folder_path: str,
) -> str | None:
    settings = get_settings()

    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not settings.R2_ACCESS_KEY_ID or not settings.R2_SECRET_ACCESS_KEY or not bucket:
        logger.warning("Cloudflare R2 not configured (missing R2_ACCESS_KEY_ID or R2_BUCKET_NAME)")
        return None

    account_id = settings.R2_ACCOUNT_ID
    pathname = f"{folder_path.strip('/')}/{filename}"
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    headers = {
        "host": f"{account_id}.r2.cloudflarestorage.com",
        "content-type": mime_type,
    }

    path = f"/{bucket}/{quote(pathname, safe='/')}"
    auth = _r2_sign("PUT", path, headers, settings.R2_SECRET_ACCESS_KEY)
    headers["Authorization"] = auth

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{endpoint}{path}",
                content=file_bytes,
                headers=headers,
            )

            if resp.status_code not in (200, 201):
                logger.error(
                    "R2 upload failed for %s: %s %s",
                    pathname,
                    resp.status_code,
                    resp.text[:200],
                )
                return None

            # R2 public URL (requires custom domain or R2.dev subdomain)
            public_url = _r2_public_url(pathname)

            logger.info("Uploaded to R2: %s -> %s", pathname, public_url)
            return public_url

    except Exception as exc:
        logger.error("R2 upload failed for %s: %s", pathname, exc)
        return None


async def _delete_r2(url: str) -> bool:
    settings = get_settings()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not (settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY and bucket):
        logger.warning("Cloudflare R2 delete unavailable because credentials are incomplete")
        return False

    account_id = settings.R2_ACCOUNT_ID
    object_key = _r2_object_key(url)
    if not object_key:
        return False

    encoded_key = quote(object_key, safe="/-_.~")
    path = f"/{bucket}/{encoded_key}"
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    headers = {"host": f"{account_id}.r2.cloudflarestorage.com"}
    headers["Authorization"] = _r2_sign("DELETE", path, headers, settings.R2_SECRET_ACCESS_KEY)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(f"{endpoint}{path}", headers=headers)
        if 200 <= response.status_code < 300:
            logger.info("Deleted R2 object: %s", object_key)
            return True
        logger.error("R2 delete failed for %s: HTTP %s", object_key, response.status_code)
    except Exception as exc:
        logger.error("R2 delete failed for %s: %s", object_key, exc)
    return False


def _r2_object_key(url: str) -> str | None:
    """Resolve a configured R2 URL to its literal object key."""
    settings = get_settings()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    account_id = settings.R2_ACCOUNT_ID
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    public_domain = (settings.R2_PUBLIC_DOMAIN or "").strip()
    public_host = (
        urlsplit(public_domain if "://" in public_domain else f"//{public_domain}").hostname if public_domain else None
    )
    decoded_path = unquote(parsed.path).lstrip("/")

    if public_host and hostname == public_host.lower():
        object_key = decoded_path
    elif hostname == f"pub-{account_id}.r2.dev":
        bucket_prefix = f"{bucket}/"
        object_key = decoded_path[len(bucket_prefix) :] if decoded_path.startswith(bucket_prefix) else decoded_path
    elif hostname == f"{account_id}.r2.cloudflarestorage.com":
        bucket_prefix = f"{bucket}/"
        if not decoded_path.startswith(bucket_prefix):
            logger.warning("R2 delete URL does not target the configured bucket")
            return False
        object_key = decoded_path[len(bucket_prefix) :]
    else:
        logger.warning("R2 delete URL does not match the configured account or public domain")
        return None

    if not object_key or ".." in object_key.split("/"):
        logger.warning("R2 delete URL did not contain an object key")
        return None
    return object_key


def _r2_public_url(object_key: str) -> str:
    """The reference upload_file has always returned for an object key."""
    settings = get_settings()
    if settings.R2_PUBLIC_DOMAIN:
        return f"https://{settings.R2_PUBLIC_DOMAIN}/{object_key}"
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    return f"https://pub-{settings.R2_ACCOUNT_ID}.r2.dev/{bucket}/{object_key}"


# ═══════════════════════════════════════════════════════
#  Private government ID objects (signed GET links)
# ═══════════════════════════════════════════════════════

# Waiver ID photos uploaded by the public walk-in flow.
PRIVATE_ID_PREFIXES = ("waivers/pending-ids/",)
# Printable waiver packets (waivers/<month>/<name>_<ref>/<name>_<ref>.pdf) embed the client's ID image.
_WAIVER_ROOT = "waivers/"
# Artist Scroll government IDs live beside public-safe certificates under
# scrolls/<user>/<submission>/, so they are matched by file stem instead.
_SCROLL_ID_ROOT = "scrolls/"
_SCROLL_ID_STEM = "govt-id"
# S3 caps a SigV4 query-string signature at seven days.
_MAX_PRESIGN_SECONDS = 7 * 24 * 3600


def is_private_id_key(object_key: str) -> bool:
    """True when an R2 key holds a government ID (image, or a PDF embedding one) that must never be public."""
    if not object_key or ".." in object_key.split("/"):
        return False
    if object_key.startswith(PRIVATE_ID_PREFIXES):
        return True
    if object_key.startswith(_WAIVER_ROOT) and object_key.lower().endswith(".pdf"):
        return True
    return object_key.startswith(_SCROLL_ID_ROOT) and object_key.rsplit("/", 1)[-1].startswith(_SCROLL_ID_STEM)


def _r2_key_from_reference(reference: str) -> str | None:
    """
    Resolve a stored URL on this install's own R2 hosts to its object key.

    Only the configured public domain, this account's r2.dev host and this account's S3 endpoint
    (with the configured bucket) resolve. Foreign URLs, other buckets and an unconfigured bucket
    return None. Never logs, because the reference names a person's ID.
    """
    settings = get_settings()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    account_id = (settings.R2_ACCOUNT_ID or "").strip().lower()
    if not bucket:
        return None
    try:
        parsed = urlsplit(reference)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not hostname:
        return None
    path = unquote(parsed.path).lstrip("/")
    if _is_r2_custom_domain(reference):
        key = path
    elif account_id and hostname in (f"pub-{account_id}.r2.dev", f"{account_id}.r2.cloudflarestorage.com"):
        if not path.startswith(f"{bucket}/"):
            return None
        key = path[len(bucket) + 1 :]
    else:
        return None
    if not key or ".." in key.split("/"):
        return None
    return key


def is_own_storage_url(reference: str | None) -> bool:
    """True when `reference` is an object on this install's own R2 bucket. The server only fetches these."""
    if not reference or not isinstance(reference, str):
        return False
    return _r2_key_from_reference(reference.strip()) is not None


def private_id_key(reference: str | None) -> str | None:
    """The object key when `reference` points at a private government ID on our R2 bucket, else None."""
    if not reference or not isinstance(reference, str):
        return None
    key = _r2_key_from_reference(reference.strip())
    return key if key and is_private_id_key(key) else None


def canonical_id_reference(reference: str | None) -> str | None:
    """
    Normalize a submitted ID reference to the stored format.

    The browser may hand back the short-lived signed link it was shown. Storing that would persist an
    expiring URL, so private ID keys are rewritten to the same reference upload_file returns. Anything
    else is returned unchanged.
    """
    key = private_id_key(reference)
    return _r2_public_url(key) if key else reference


def _presign(
    *,
    host: str,
    canonical_uri: str,
    region: str,
    access_key: str,
    secret_key: str,
    now: datetime,
    expires_seconds: int,
) -> str:
    """SigV4 query-string signing for an unsigned-payload GET (AWS 'presigned URL')."""
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    credential_scope = f"{datestamp}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    canonical_request = f"GET\n{canonical_uri}\n{canonical_query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
    string_to_sign = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n" + hashlib.sha256(canonical_request.encode()).hexdigest()
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_signing = _sign(_sign(_sign(_sign(f"AWS4{secret_key}".encode(), datestamp), region), "s3"), "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


def presign_get_url(object_key: str, expires_seconds: int = 300, *, now: datetime | None = None) -> str | None:
    """
    Short-lived signed GET link for one R2 object, served from the S3 API host.

    Works whether or not the object is publicly reachable, so private keys stay viewable by the app.
    Returns None when R2 credentials or the bucket are missing, or the key is unsafe. Never raises.
    """
    settings = get_settings()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    if not (settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY and bucket):
        logger.warning("R2 presign unavailable because credentials or the bucket name are incomplete")
        return None
    key = (object_key or "").lstrip("/")
    if not key or ".." in key.split("/"):
        return None
    expires = max(1, min(int(expires_seconds), _MAX_PRESIGN_SECONDS))
    return _presign(
        host=f"{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        canonical_uri=f"/{quote(bucket, safe='-_.~')}/{quote(key, safe='/-_.~')}",
        region="auto",
        access_key=settings.R2_ACCESS_KEY_ID,
        secret_key=settings.R2_SECRET_ACCESS_KEY,
        now=now or datetime.now(UTC),
        expires_seconds=expires,
    )


def signed_id_url(reference: str | None, expires_seconds: int = 300) -> str | None:
    """
    Turn a stored ID reference into a link a client can open.

    Private government ID keys on our R2 bucket get a fresh signed link. Anything else (foreign URLs,
    non-ID keys, empty values) passes through unchanged, as does a private key when signing is not
    configured, so local development keeps working.
    """
    key = private_id_key(reference)
    if not key:
        return reference
    return presign_get_url(key, expires_seconds) or reference


# Document data keys that can hold a government ID reference (waiver photo ID, Artist Scroll ID).
ID_REFERENCE_KEYS = ("photo_id_url", "photo_id", "govt_id_url")


def signed_id_fields(data: dict, expires_seconds: int = 300) -> dict:
    """
    A shallow copy of stored document data with every government ID reference signed.

    Covers the waiver photo ID keys, the Scroll govt_id_url and certs_stored.govt_id.url.
    The input is never mutated, so the stored record keeps its stable references.
    """
    view = dict(data)
    for key in ID_REFERENCE_KEYS:
        if isinstance(view.get(key), str) and view[key]:
            view[key] = signed_id_url(view[key], expires_seconds)
    certs = view.get("certs_stored")
    if isinstance(certs, dict) and isinstance(certs.get("govt_id"), dict) and certs["govt_id"].get("url"):
        view["certs_stored"] = {
            **certs,
            "govt_id": {**certs["govt_id"], "url": signed_id_url(certs["govt_id"]["url"], expires_seconds)},
        }
    return view


# ── Backward compatibility alias ──
upload_to_drive = upload_file
