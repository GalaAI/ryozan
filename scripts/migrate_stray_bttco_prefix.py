"""
One-shot migration for stray BTTCO/ prefixed objects in the R2 bucket.

Background:
    Between the original folder_path refactor and the commit that shipped it,
    production was still uploading to the legacy BTTCO/ prefix. This script
    moves those stray objects to their canonical lowercase paths so the bucket
    is internally consistent.

Usage:
    cd <repo root>
    # make sure these env vars are set (same names the API uses):
    #   R2_ACCOUNT_ID
    #   R2_ACCESS_KEY_ID
    #   R2_SECRET_ACCESS_KEY
    #   R2_BUCKET_NAME        (default: bttco-assets)
    # Optional:
    #   DATABASE_URL          (if set, also rewrites bttco_users.avatar_url
    #                          and bookings.reference_images JSON array)
    #   DRY_RUN=1             (list only, no copies or deletes)

    python scripts/migrate_stray_bttco_prefix.py

Safe to re-run: idempotent. Skips any object whose key does not start with BTTCO/.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable

import boto3
from botocore.client import Config


PREFIX_MAP = {
    "BTTCO/Avatars/": "avatars/",
    "BTTCO/Portfolios/": "portfolios/",
    "BTTCO/Bookings/": "bookings/",
    "BTTCO/Documents/": "documents/",
    "BTTCO/Scrolls/": "scrolls/",
    "BTTCO/Waivers/": "waivers/",
}


def _remap(key: str) -> str | None:
    for old, new in PREFIX_MAP.items():
        if key.startswith(old):
            return new + key[len(old) :]
    return None


def _iter_bttco_objects(s3, bucket: str) -> Iterable[dict]:
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="BTTCO/"):
        for obj in page.get("Contents", []) or []:
            yield obj


def migrate_r2(dry_run: bool) -> list[tuple[str, str]]:
    account = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ.get("R2_BUCKET_NAME", "bttco-assets")
    endpoint = f"https://{account}.r2.cloudflarestorage.com"

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", region_name="auto"),
    )

    moves: list[tuple[str, str]] = []
    for obj in _iter_bttco_objects(s3, bucket):
        old_key = obj["Key"]
        new_key = _remap(old_key)
        if not new_key:
            print(f"SKIP (unmapped): {old_key}")
            continue

        if dry_run:
            print(f"DRY:  {old_key} -> {new_key}")
            moves.append((old_key, new_key))
            continue

        s3.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": old_key},
            Key=new_key,
            MetadataDirective="COPY",
        )
        s3.delete_object(Bucket=bucket, Key=old_key)
        print(f"MOVED: {old_key} -> {new_key}")
        moves.append((old_key, new_key))

    return moves


def rewrite_db(moves: list[tuple[str, str]]) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url or not moves:
        print("skipping DB rewrite (no DATABASE_URL or no moves)")
        return

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not installed; skipping DB rewrite")
        return

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # avatars: swap /BTTCO/Avatars/ -> /avatars/
            cur.execute(
                """
                UPDATE bttco_users
                SET avatar_url = REPLACE(avatar_url, '/BTTCO/Avatars/', '/avatars/')
                WHERE avatar_url LIKE '%/BTTCO/Avatars/%'
                """
            )
            print(f"bttco_users.avatar_url rows updated: {cur.rowcount}")

            # bookings.reference_images: JSONB array of URL strings
            cur.execute(
                """
                UPDATE bookings
                SET reference_images = REPLACE(
                    reference_images::text,
                    '/BTTCO/Bookings/',
                    '/bookings/'
                )::jsonb
                WHERE reference_images::text LIKE '%/BTTCO/Bookings/%'
                """
            )
            print(f"bookings.reference_images rows updated: {cur.rowcount}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"
    print(f"R2 stray prefix migration (dry_run={dry_run})")
    moves = migrate_r2(dry_run=dry_run)
    print(f"\nR2 moves: {len(moves)}")

    if not dry_run:
        rewrite_db(moves)

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
