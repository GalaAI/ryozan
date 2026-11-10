"""
Publish a shop brand pack to a Cloudflare R2 bucket.

A pack is shop content (docs/SHOP_BRAND_PACKS.md): it is published per install and never
committed to this repository. This script lists what would be uploaded and, only with
--apply, uploads it.

Usage (from the repo root, with the API virtualenv, which has httpx):

    python scripts/publish_shop_pack.py PACK_DIR --scope SCOPE --bucket BUCKET [--prefix PREFIX] [--apply]

    PACK_DIR   the folder that holds @SCOPE/, for example a checkout with
               @exampleshop/email-foundry/1.0.0/...
    --scope    the pack's @scope without the @ (the install's SHOP_PACK_SCOPE)
    --bucket   the R2 bucket to upload to
    --prefix   optional key prefix inside the bucket, for example shops/example

Default is a dry run: it prints the manifest (key, size, sha256) and changes nothing.
--apply uploads every listed file to <bucket>/<prefix>/@<scope>/... with the same
Content-Type the API serves it with. --apply reads the credentials from the environment,
under the names the API uses: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.

Only files the API would serve are listed (the allowlist in api/app/services/shop_pack.py);
everything else, such as README.md or hidden files, is reported as skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.services.shop_pack import PACK_CONTENT_TYPES, PACK_MAX_BYTES, pack_file_path  # noqa: E402

CREDENTIAL_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def collect(pack_dir: Path, scope: str) -> tuple[list[tuple[str, Path, int, str]], list[str]]:
    """Every servable file under pack_dir/@scope as (pack path, file, size, sha256), and the skipped paths."""
    root = pack_dir / f"@{scope}"
    if not root.is_dir():
        raise SystemExit(f"{root} is not a folder; PACK_DIR must hold @{scope}/")
    files: list[tuple[str, Path, int, str]] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        pack_path = path.relative_to(pack_dir).as_posix()
        size = path.lstat().st_size
        if path.is_symlink() or pack_file_path(pack_path, scope) is None or size > PACK_MAX_BYTES:
            skipped.append(pack_path)
            continue
        files.append((pack_path, path, size, hashlib.sha256(path.read_bytes()).hexdigest()))
    return files, skipped


def object_key(prefix: str, pack_path: str) -> str:
    prefix = prefix.strip("/")
    return f"{prefix}/{pack_path}" if prefix else pack_path


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def signed_put_headers(
    account_id: str, access_key: str, secret_key: str, canonical_path: str, content_type: str, payload_sha256: str
) -> dict[str, str]:
    """AWS Signature V4 headers for one R2 PUT (S3-compatible, region auto)."""
    now = datetime.now(UTC)
    datestamp, amz_date = now.strftime("%Y%m%d"), now.strftime("%Y%m%dT%H%M%SZ")
    headers = {
        "content-type": content_type,
        "host": f"{account_id}.r2.cloudflarestorage.com",
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
    }
    names = sorted(headers)
    canonical_request = "\n".join(
        [
            "PUT",
            canonical_path,
            "",
            "".join(f"{name}:{headers[name]}\n" for name in names),
            ";".join(names),
            payload_sha256,
        ]
    )
    scope = f"{datestamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    key = _sign(_sign(_sign(_sign(f"AWS4{secret_key}".encode(), datestamp), "auto"), "s3"), "aws4_request")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return headers


def upload(files: list[tuple[str, Path, int, str]], bucket: str, prefix: str) -> int:
    import httpx

    missing = [name for name in CREDENTIAL_ENV if not os.environ.get(name, "").strip()]
    if missing:
        raise SystemExit(f"--apply needs {', '.join(missing)} in the environment")
    account_id, access_key, secret_key = (os.environ[name].strip() for name in CREDENTIAL_ENV)
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    failures = 0
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for pack_path, path, _size, digest in files:
            key = object_key(prefix, pack_path)
            canonical_path = f"/{quote(bucket, safe='')}/{quote(key, safe='/@-._~')}"
            content_type = PACK_CONTENT_TYPES[Path(pack_path).suffix.lower()]
            headers = signed_put_headers(account_id, access_key, secret_key, canonical_path, content_type, digest)
            response = client.put(f"{endpoint}{canonical_path}", content=path.read_bytes(), headers=headers)
            if response.status_code in (200, 201):
                print(f"uploaded  {key}")
            else:
                failures += 1
                print(f"FAILED    {key}: {response.status_code} {response.text[:200]}", file=sys.stderr)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a shop brand pack to R2 (dry run unless --apply).")
    parser.add_argument("pack_dir", type=Path, help="folder that holds @SCOPE/")
    parser.add_argument("--scope", required=True, help="the pack @scope without the @ (SHOP_PACK_SCOPE)")
    parser.add_argument("--bucket", required=True, help="R2 bucket name")
    parser.add_argument("--prefix", default="", help="key prefix inside the bucket")
    parser.add_argument("--apply", action="store_true", help="upload; without it nothing is changed")
    args = parser.parse_args(argv)

    scope = args.scope.strip().removeprefix("@")
    files, skipped = collect(args.pack_dir.resolve(), scope)

    print(f"# shop pack @{scope} -> r2://{args.bucket}/{object_key(args.prefix, f'@{scope}/')}")
    print(f"# {'APPLY' if args.apply else 'DRY RUN (pass --apply to upload)'}")
    for pack_path, _path, size, digest in files:
        print(f"{digest}  {size:>10}  {object_key(args.prefix, pack_path)}")
    for pack_path in skipped:
        print(f"skipped   {pack_path} (not served by /dependencies)")
    print(f"# {len(files)} files, {sum(size for _p, _f, size, _d in files)} bytes, {len(skipped)} skipped")

    if not args.apply:
        return 0
    return 1 if upload(files, args.bucket, args.prefix) else 0


if __name__ == "__main__":
    raise SystemExit(main())
