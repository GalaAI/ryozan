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
from urllib.parse import parse_qs, quote, unquote, urlsplit

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

# Everything under "waivers/" is a client record: ID photos (waivers/pending-ids/), printable
# packets that embed the ID (waivers/<month>/<name>_<ref>/<name>_<ref>.pdf) and the client's
# signature image (.../signature.png). Artists' saved signatures live under "signatures/".
# Both prefixes are private by key; staff open them through short-lived signed links.
PRIVATE_ID_PREFIXES = ("waivers/", "signatures/")
# Everything under "scrolls/" is private by key: Artist Scroll government IDs (govt-id.*), every
# generated scroll PDF (artist-scroll.id.pdf and artist-scroll.noid.pdf alike, since each one prints
# the artist signature, any owner countersignature and the artist's details; or the legacy
# artist-scroll.pdf), the signature images, and every older certificate, signature and category
# upload, which was never inspected and may hold an ID. New files with no ID, signature or client
# record (unmarked category uploads, certificates) are written under the separate "scrolls-public/"
# prefix (public_scroll_prefix). Generated PDFs written there before that rule are moved to
# "scrolls/" at boot (scroll_archive.move_public_scroll_pdfs). A new file that is, or embeds, an ID
# keeps the ".id.<ext>" marker name (scroll_id_filename) so it reads as an ID wherever it is listed;
# the PDF names (.id/.noid) record only whether the ID image is embedded, privacy comes from the prefix.
_SCROLL_ID_ROOT = "scrolls/"
SCROLL_PUBLIC_ROOT = "scrolls-public/"
SCROLL_ID_PDF_FILENAME = "artist-scroll.id.pdf"
SCROLL_NOID_PDF_FILENAME = "artist-scroll.noid.pdf"
_SCROLL_ID_MARKER = ".id"
# S3 caps a SigV4 query-string signature at seven days.
_MAX_PRESIGN_SECONDS = 7 * 24 * 3600


def is_private_id_key(object_key: str) -> bool:
    """True when an R2 key holds, or may hold, a government ID or a signature and must never be public."""
    if not object_key or ".." in object_key.split("/"):
        return False
    return object_key.startswith((*PRIVATE_ID_PREFIXES, _SCROLL_ID_ROOT))


def public_scroll_prefix(private_prefix: str) -> str:
    """The "scrolls-public/" twin of a "scrolls/..." folder, for new files that are safe to serve publicly."""
    prefix = private_prefix.strip("/")
    if prefix.startswith(_SCROLL_ID_ROOT):
        return SCROLL_PUBLIC_ROOT + prefix[len(_SCROLL_ID_ROOT) :]
    if prefix == _SCROLL_ID_ROOT.rstrip("/"):
        return SCROLL_PUBLIC_ROOT.rstrip("/")
    return prefix


def private_scroll_prefix(public_prefix: str) -> str:
    """The "scrolls/" twin of a "scrolls-public/..." folder: the private half of the same submission folder."""
    prefix = public_prefix.strip("/")
    if prefix.startswith(SCROLL_PUBLIC_ROOT):
        return _SCROLL_ID_ROOT + prefix[len(SCROLL_PUBLIC_ROOT) :]
    if prefix == SCROLL_PUBLIC_ROOT.rstrip("/"):
        return _SCROLL_ID_ROOT.rstrip("/")
    return prefix


def scroll_id_filename(stem: str, extension: str) -> str:
    """The ".id.<ext>" marker name for a scroll file that is (or embeds) a government ID."""
    ext = (extension or "").lstrip(".").lower() or "bin"
    return f"{stem}{_SCROLL_ID_MARKER}.{ext}"


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


def storage_key(reference: str | None) -> str | None:
    """The object key when `reference` is an object on this install's own R2 bucket, else None. Never logs."""
    if not reference or not isinstance(reference, str):
        return None
    return _r2_key_from_reference(reference.strip())


def is_own_storage_url(reference: str | None) -> bool:
    """True when `reference` is an object on this install's own R2 bucket. The server only fetches these."""
    return storage_key(reference) is not None


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


async def fetch_object(object_key: str) -> bytes | None:
    """
    The bytes of one R2 object, read through a short-lived signed link on the S3 host.

    Works for private and public keys alike. Returns None when R2 is not configured, the object is
    missing or the read fails. Never raises, never logs the key.
    """
    url = presign_get_url(object_key, 120)
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - never raises
        logger.error("R2 read failed: %s", type(exc).__name__)
        return None
    if response.status_code != 200:
        logger.error("R2 read failed: HTTP %s", response.status_code)
        return None
    return response.content


# Only the public walk-in upload writes here, so only these keys can come back as an upload capability.
UPLOAD_ID_PREFIX = "waivers/pending-ids/"
_PRESIGN_PARAMS = frozenset(
    ("X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires", "X-Amz-SignedHeaders", "X-Amz-Signature")
)
# Tolerated clock drift for a link dated slightly ahead of this server's clock.
_PRESIGN_SKEW = 300


def verified_upload_key(reference: str | None, *, now: datetime | None = None) -> str | None:
    """
    The upload key when `reference` is an unexpired signed link this server issued for it, else None.

    A public caller proves it uploaded an ID by handing back the signed preview link /waiver/upload-id
    returned. The SigV4 signature is recomputed for the same host, bucket, key, date and expiry with this
    install's R2 credentials and compared in constant time, so a bare URL, an expired, tampered or
    foreign-signed link, or a link to any key outside waivers/pending-ids/ never resolves. Never raises
    and never logs, because the reference names a person's ID.
    """
    if not reference or not isinstance(reference, str):
        return None
    settings = get_settings()
    bucket = (settings.R2_BUCKET_NAME or "").strip()
    account_id = (settings.R2_ACCOUNT_ID or "").strip()
    access_key = settings.R2_ACCESS_KEY_ID or ""
    secret_key = settings.R2_SECRET_ACCESS_KEY or ""
    if not (bucket and account_id and access_key and secret_key):
        return None
    host = f"{account_id}.r2.cloudflarestorage.com"
    try:
        parsed = urlsplit(reference.strip())
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
        params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if parsed.scheme != "https" or hostname != host.lower() or port is not None or parsed.fragment:
        return None
    if set(params) != _PRESIGN_PARAMS or any(len(values) != 1 for values in params.values()):
        return None
    query = {name: values[0] for name, values in params.items()}
    path = unquote(parsed.path)
    if not path.startswith(f"/{bucket}/"):
        return None
    key = path[len(bucket) + 2 :]
    if not key.startswith(UPLOAD_ID_PREFIX) or len(key) == len(UPLOAD_ID_PREFIX) or ".." in key.split("/"):
        return None
    try:
        signed_at = datetime.strptime(query["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        expires = int(query["X-Amz-Expires"])
    except ValueError:
        return None
    if not 1 <= expires <= _MAX_PRESIGN_SECONDS or not query["X-Amz-Expires"].isdigit():
        return None
    if query["X-Amz-Credential"] != f"{access_key}/{signed_at:%Y%m%d}/auto/s3/aws4_request":
        return None
    expected = _presign(
        host=host,
        canonical_uri=f"/{quote(bucket, safe='-_.~')}/{quote(key, safe='/-_.~')}",
        region="auto",
        access_key=access_key,
        secret_key=secret_key,
        now=signed_at,
        expires_seconds=expires,
    )
    expected_signature = parse_qs(urlsplit(expected).query)["X-Amz-Signature"][0]
    if not hmac.compare_digest(expected_signature.encode(), query["X-Amz-Signature"].encode()):
        return None
    current = now or datetime.now(UTC)
    if signed_at.timestamp() - _PRESIGN_SKEW > current.timestamp():
        return None
    if current.timestamp() >= signed_at.timestamp() + expires:
        return None
    return key


def uploaded_id_reference(reference: str | None, *, now: datetime | None = None) -> str | None:
    """The stored reference for an ID the caller proved it uploaded (see verified_upload_key), else None."""
    key = verified_upload_key(reference, now=now)
    return _r2_public_url(key) if key else None


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
# Substrings that tell a raw data_json may hold a private reference (cheap prefilter): the ID keys, the
# Artist Scroll PDF (which embeds the ID or prints the signatures) and every private prefix.
PRIVATE_REFERENCE_HINTS = (*ID_REFERENCE_KEYS, "scroll_pdf_url", *PRIVATE_ID_PREFIXES, _SCROLL_ID_ROOT)
# The owner archive copy nests the artist's scroll data under "fields"; one level is all it uses.
_MAX_NESTED_FIELDS_DEPTH = 2


def signed_id_fields(data: dict, expires_seconds: int = 300, *, _depth: int = 0) -> dict:
    """
    A copy of stored document data with every private storage reference signed.

    Every top-level string that is a private key on our own bucket gets a signed link (waiver photo
    IDs, the Scroll govt_id_url and scroll_pdf_url, and every scroll certificate, signature or file
    under "scrolls/"), as does every certs_stored.<doc>.url and the same inside a nested "fields"
    dict (owner archive copies). Everything else (text, "scrolls-public/" files, foreign URLs)
    passes through unchanged. The input is never mutated, so the stored record keeps its stable
    references.
    """
    view = dict(data)
    for key, value in data.items():
        if isinstance(value, str) and value:
            view[key] = signed_id_url(value, expires_seconds)
    certs = view.get("certs_stored")
    if isinstance(certs, dict):
        view["certs_stored"] = {
            name: (
                {**entry, "url": signed_id_url(entry["url"], expires_seconds)}
                if isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"]
                else entry
            )
            for name, entry in certs.items()
        }
    nested = view.get("fields")
    if isinstance(nested, dict) and _depth < _MAX_NESTED_FIELDS_DEPTH:
        view["fields"] = signed_id_fields(nested, expires_seconds, _depth=_depth + 1)
    return view


# ═══════════════════════════════════════════════════════
#  Client-supplied references (write side)
# ═══════════════════════════════════════════════════════

# Folders on our bucket that hold one person's records. Waivers (IDs, signatures, packets) have no
# per-person folder. Signatures, Scrolls and uploaded documents sit under the owner's user id:
# signatures/<id>/, scrolls/<id>/<submission>/, scrolls/<category>/<id>/, documents/<id>/.
PRIVATE_PREFIXES = ("waivers/", "signatures/", "scrolls/", "documents/")


class UnauthorizedReferenceError(ValueError):
    """A client-supplied value names a stored object the caller has not proven it may reference."""

    def __init__(self, path: tuple):
        super().__init__("Upload the file again")
        self.path = path


def client_reference_key(value: object) -> str | None:
    """
    The object key when `value` names an object on this install's own bucket, else None.

    Covers every own host form (assets domain, r2.dev, the S3 endpoint with our bucket, with or without a
    signed query, scheme-relative) and a bare key under a private prefix. Foreign URLs, other buckets, inline
    data: images and plain text return None.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    key = _r2_key_from_reference(f"https:{text}" if text.startswith("//") else text)
    if key:
        return key
    if "://" in text or text.lower().startswith("data:"):
        return None
    bare = text.lstrip("/")
    if bare.startswith(PRIVATE_PREFIXES) and ".." not in bare.split("/"):
        return bare.split("?", 1)[0].split("#", 1)[0]
    return None


def _caller_owns_key(key: str, caller_id: str | None) -> bool:
    """True when `key` sits in one of the caller's own folders (<root>/<id>/..., scrolls/<category>/<id>/...)."""
    if not caller_id:
        return False
    parts = key.split("/")
    if len(parts) >= 3 and parts[1] == caller_id:
        return True
    return parts[0] == "scrolls" and len(parts) >= 4 and parts[2] == caller_id


def _caller_id(caller: object) -> str | None:
    if caller is None:
        return None
    raw = getattr(caller, "id", caller)
    text = str(raw).strip() if raw is not None else ""
    return text or None


def authorize_client_references(
    value: object,
    *,
    caller: object = None,
    server_allowed: tuple | list | set = (),
    now: datetime | None = None,
):
    """
    A copy of client-supplied data in which every reference to our bucket is one the caller may make.

    Read paths sign private references found in stored data, so every write path that stores client data
    runs it through here first. Dicts, lists and strings are walked recursively. A string naming an object
    on our bucket is kept only when:

    - it is an unexpired upload link this server signed (verified_upload_key); the stable reference is
      stored instead of the expiring link,
    - the object sits in the caller's own folder (`caller` is the signed-in user or their id; None for a
      public caller, who has no folder), or
    - it is one of `server_allowed`, references the server copied itself in this request (an ID reused
      from a prior waiver, an adopted or saved signature).

    Anything else raises UnauthorizedReferenceError naming the offending path, so the caller refuses the request
    and stores nothing. Foreign URLs, inline data: images and plain text pass through unchanged. The input is
    never mutated. Never logs, because a reference can name a person's ID.
    """
    caller_id = _caller_id(caller)
    allowed_refs = {ref.strip() for ref in server_allowed if isinstance(ref, str) and ref.strip()}
    allowed_keys = {key for key in (client_reference_key(ref) for ref in allowed_refs) if key}

    def _string(text: str, path: tuple) -> str:
        stripped = text.strip()
        if stripped in allowed_refs:
            return text
        upload_key = verified_upload_key(stripped, now=now)
        if upload_key:
            return _r2_public_url(upload_key)
        key = client_reference_key(stripped)
        if key is None:
            return text
        if key in allowed_keys or _caller_owns_key(key, caller_id):
            # A signed link the browser echoed back is stored as the stable reference, never the expiring URL.
            return _r2_public_url(key) if "://" in stripped and ("?" in stripped or "#" in stripped) else text
        raise UnauthorizedReferenceError(path)

    def _walk(item: object, path: tuple):
        if isinstance(item, str):
            return _string(item, path)
        if isinstance(item, dict):
            return {name: _walk(child, (*path, name)) for name, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [_walk(child, (*path, index)) for index, child in enumerate(item)]
        return item

    return _walk(value, ())


# ── Backward compatibility alias ──
upload_to_drive = upload_file
