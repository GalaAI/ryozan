"""Owner bulk upload of one artist's portfolio through the staff gallery route.

Boundary: this is not sync_public_state.py (production to local, read side) and not
backfill_portfolio_thumbs.py (derivatives for images already in a gallery). It only calls
POST /artists/{slug}/gallery/upload, so every image takes the same validation, storage and
thumbnail path as an upload from the profile editor. It never touches the database or R2 directly.

Source is a folder or a .zip. Prepared sets often hold three files per piece (NAME.jpg, NAME.webp,
NAME-thumb.jpg); the server makes its own derivatives, so one original per piece is sent and
thumbs are skipped.

Environment:
    BTTCO_API_BASE   API root, for example http://localhost:8801/api/v1
    BTTCO_TOKEN      a founder session token (never printed)

Usage:
    python scripts/upload_artist_gallery.py --slug devin --source devin-burnett-portfolio.zip
    python scripts/upload_artist_gallery.py --slug devin --source devin-burnett-portfolio.zip --apply

Without --apply it lists what would be sent and exits.
"""

import argparse
import mimetypes
import os
import sys
import zipfile
from pathlib import Path

import httpx

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
# One original per piece; first match wins.
EXTENSION_PREFERENCE = {".jpg": 0, ".jpeg": 0, ".png": 1, ".webp": 2}
REQUEST_TIMEOUT_SECONDS = 120.0


def _is_thumb(name: str) -> bool:
    stem = Path(name).stem.lower()
    return stem.endswith(("-thumb", "_thumb")) or "/thumbs/" in name.replace("\\", "/").lower()


def select_originals(names: list[str]) -> list[str]:
    """One file per piece, in name order, thumbs and non-images dropped."""
    best: dict[str, str] = {}
    for name in names:
        path = Path(name)
        ext = path.suffix.lower()
        if ext not in IMAGE_EXTENSIONS or _is_thumb(name) or path.name.startswith("."):
            continue
        key = str(path.with_suffix("")).lower()
        current = best.get(key)
        if current is None or EXTENSION_PREFERENCE[ext] < EXTENSION_PREFERENCE[Path(current).suffix.lower()]:
            best[key] = name
    return [best[key] for key in sorted(best)]


def _read_source(source: Path) -> list[tuple[str, bytes]]:
    if source.is_dir():
        names = [str(p.relative_to(source)) for p in sorted(source.rglob("*")) if p.is_file()]
        return [(Path(n).name, (source / n).read_bytes()) for n in select_originals(names)]
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            return [(Path(n).name, archive.read(n)) for n in select_originals(names)]
    raise SystemExit(f"Source is neither a folder nor a zip: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True, help="artist slug, for example devin")
    parser.add_argument("--source", required=True, type=Path, help="folder or .zip of images")
    parser.add_argument("--apply", action="store_true", help="send the uploads (default is a dry run)")
    parser.add_argument("--skip", type=int, default=0, help="skip the first N images (resume after a failure)")
    args = parser.parse_args()

    files = _read_source(args.source)[max(args.skip, 0) :]
    if not files:
        print("No images found in the source.")
        return 1
    print(f"{len(files)} image(s) for /artists/{args.slug}:")
    for name, data in files:
        print(f"  {name}  {len(data):,} bytes")
    if not args.apply:
        print("Dry run. Add --apply to upload.")
        return 0

    api_base = os.environ.get("BTTCO_API_BASE", "").rstrip("/")
    token = os.environ.get("BTTCO_TOKEN", "")
    if not api_base or not token:
        print("Set BTTCO_API_BASE and BTTCO_TOKEN before using --apply.", file=sys.stderr)
        return 2

    url = f"{api_base}/artists/{args.slug}/gallery/upload"
    sent = 0
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, headers={"Authorization": f"Bearer {token}"}) as client:
        for name, data in files:
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            try:
                response = client.post(url, files={"file": (name, data, mime)})
            except httpx.HTTPError as exc:
                print(f"  FAILED {name}: {type(exc).__name__}", file=sys.stderr)
                break
            if response.status_code != 200:
                print(f"  FAILED {name}: HTTP {response.status_code} {response.text[:200]}", file=sys.stderr)
                break
            sent += 1
            print(f"  ok {name}  gallery now {len(response.json().get('portfolio_images') or [])}")
    print(f"Uploaded {sent} of {len(files)}.")
    if sent != len(files):
        # Stops at the first failure. Resume from there so the images that landed are not sent twice.
        print(f"Resume with --skip {args.skip + sent}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
