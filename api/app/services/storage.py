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
            if settings.R2_PUBLIC_DOMAIN:
                public_url = f"https://{settings.R2_PUBLIC_DOMAIN}/{pathname}"
            else:
                public_url = f"https://pub-{account_id}.r2.dev/{bucket}/{pathname}"

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


# ── Backward compatibility alias ──
upload_to_drive = upload_file
