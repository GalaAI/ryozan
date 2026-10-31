"""Artist portfolio thumbnails.

Portfolio uploads are stored once, as the normalized original (up to 4096px on the long
edge, a JPEG or PNG of several hundred KB to a few MB). Every gallery surface, the public
portfolio page, the legacy booking wizard and the @bttco/booking-flow work wall, was
loading that original into a tile a few hundred pixels wide. This module gives each
original one small WebP companion:

    portfolios/<slug>/<file>.jpg                 the original (unchanged)
    portfolios/<slug>/thumbs/<file>.webp         640px long edge, quality 82 (tiles)
    portfolios/<slug>/focus/<file>.webp          1400px long edge, quality 82 (enlarged view)

The mappings original_url -> derivative_url live on BTTCOUser.portfolio_thumbs and
BTTCOUser.portfolio_focus (JSONB). Dicts rather than parallel lists, so removing or
reordering an original never desyncs them. Consumers read the thumb for grid tiles and the
focus derivative when a piece is enlarged; the original is never decoded in a browser. A
missing derivative falls back to the original, so a failed one never breaks a gallery.

Generation is best effort on upload: a failure logs and the original still lands.
`backfill_missing_portfolio_derivatives` fills gaps for images uploaded before a
derivative existed (or whose generation failed); it is idempotent and additive, runs from
`scripts/backfill_portfolio_thumbs.py` and, when PORTFOLIO_DERIVATIVES_ON_STARTUP is on,
in the background at API startup so every deployment heals its own gaps.
"""

from __future__ import annotations

import io
import logging
import posixpath
from dataclasses import dataclass
from urllib.parse import urlsplit

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

from app.services.storage import upload_file

logger = logging.getLogger("bttco.portfolio_thumbs")

# Long-edge size for gallery tiles. The booking wall renders tiles at ~17vw (about 330px
# on a 1920 display, 2x on retina), the portfolio page grid at roughly the same. 640 covers
# both at 2x without paying for more.
PORTFOLIO_THUMB_EDGE_PX = 640
# Long-edge size for the enlarged (focus) view. The booking wall's focus card is at most
# 56vw and 587px tall; the portfolio page lightbox is viewport-capped. 1400 covers a 2x
# 700px card, which is the largest any surface draws, without shipping the 4096 original.
PORTFOLIO_FOCUS_EDGE_PX = 1400
PORTFOLIO_THUMB_QUALITY = 82
THUMB_FOLDER = "thumbs"
FOCUS_FOLDER = "focus"
EMPTY_EDGE_MAX_CHANNEL = 12
EMPTY_EDGE_ACTIVE_FRACTION = 0.02
MIN_RETAINED_EDGE_FRACTION = 0.5


@dataclass(frozen=True)
class PortfolioThumb:
    data: bytes
    width: int
    height: int
    mime_type: str = "image/webp"
    extension: str = "webp"


class PortfolioThumbError(Exception):
    """The original could not be decoded into a thumbnail."""


def _trim_empty_edge_bands(image: Image.Image) -> Image.Image:
    """Remove edge-connected transparent or near-black padding without cropping photo content.

    Some legacy portfolio PNGs place a portrait photograph in the middle of a much taller
    black canvas. The canvas is valid image data, so CSS cover cannot remove it. A row or
    column is considered content when more than two percent of its pixels are opaque and
    brighter than the near-black threshold. The half-image retention guard prevents a
    sparse or intentionally dark photograph from being reduced to a tiny accidental bbox.
    """
    width, height = image.size
    if width < 2 or height < 2:
        return image

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        active = alpha.point(lambda value: 255 if value > 8 else 0)
    else:
        red, green, blue = rgba.convert("RGB").split()
        brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        active = brightest.point(lambda value: 255 if value > EMPTY_EDGE_MAX_CHANNEL else 0)

    occupancy_threshold = round(255 * EMPTY_EDGE_ACTIVE_FRACTION)
    row_occupancy = list(active.resize((1, height), Image.Resampling.BOX).getdata())
    column_occupancy = list(active.resize((width, 1), Image.Resampling.BOX).getdata())
    active_rows = [index for index, value in enumerate(row_occupancy) if value > occupancy_threshold]
    active_columns = [index for index, value in enumerate(column_occupancy) if value > occupancy_threshold]
    if not active_rows or not active_columns:
        return image

    left, right = active_columns[0], active_columns[-1] + 1
    top, bottom = active_rows[0], active_rows[-1] + 1
    if (right - left) < width * MIN_RETAINED_EDGE_FRACTION or (bottom - top) < height * MIN_RETAINED_EDGE_FRACTION:
        return image
    if (left, top, right, bottom) == (0, 0, width, height):
        return image
    return image.crop((left, top, right, bottom))


def build_portfolio_thumb(image_bytes: bytes, *, max_edge_px: int = PORTFOLIO_THUMB_EDGE_PX) -> PortfolioThumb:
    """Decode an original and return a metadata-free WebP no larger than max_edge_px."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        if (image.format or "").upper() == "MPO":
            image.seek(0)
        image = ImageOps.exif_transpose(image)
        # WebP keeps alpha, so a transparent PNG stays transparent; everything else is RGB.
        image = image.convert("RGBA") if image.mode in ("RGBA", "LA", "P") else image.convert("RGB")
        image = _trim_empty_edge_bands(image)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise PortfolioThumbError("The portfolio image could not be decoded for a thumbnail.") from exc

    if max(image.size) > max_edge_px:
        image.thumbnail((max_edge_px, max_edge_px), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=PORTFOLIO_THUMB_QUALITY, method=4)
    return PortfolioThumb(data=buffer.getvalue(), width=image.width, height=image.height)


def thumb_filename_for(original_url_or_filename: str) -> str:
    """`.../portfolios/<artist-slug>/IMG_0212.JPG` -> `IMG_0212.webp` (the thumb keeps the stem)."""
    path = urlsplit(original_url_or_filename).path or original_url_or_filename
    stem = posixpath.splitext(posixpath.basename(path))[0] or "image"
    return f"{stem}.webp"


async def upload_portfolio_derivative(
    image_bytes: bytes,
    *,
    slug: str,
    original_name: str,
    max_edge_px: int,
    folder: str,
) -> str | None:
    """Build and store one derivative for one original. Returns the public URL, or None.

    Never raises: the original upload must succeed even when a derivative does not.
    """
    try:
        thumb = build_portfolio_thumb(image_bytes, max_edge_px=max_edge_px)
    except PortfolioThumbError:
        logger.warning("Portfolio %s skipped for %s/%s: undecodable original", folder, slug, original_name)
        return None
    url = await upload_file(
        file_bytes=thumb.data,
        filename=thumb_filename_for(original_name),
        mime_type=thumb.mime_type,
        folder_path=f"portfolios/{slug}/{folder}",
    )
    if not url:
        logger.warning("Portfolio %s upload failed for %s/%s", folder, slug, original_name)
        return None
    logger.info("Portfolio %s stored: %s (%dx%d, %d bytes)", folder, url, thumb.width, thumb.height, len(thumb.data))
    return url


async def upload_portfolio_thumb(image_bytes: bytes, *, slug: str, original_name: str) -> str | None:
    """The 640px tile derivative (see upload_portfolio_derivative)."""
    return await upload_portfolio_derivative(
        image_bytes, slug=slug, original_name=original_name, max_edge_px=PORTFOLIO_THUMB_EDGE_PX, folder=THUMB_FOLDER
    )


async def upload_portfolio_focus(image_bytes: bytes, *, slug: str, original_name: str) -> str | None:
    """The 1400px enlarged-view derivative (see upload_portfolio_derivative)."""
    return await upload_portfolio_derivative(
        image_bytes, slug=slug, original_name=original_name, max_edge_px=PORTFOLIO_FOCUS_EDGE_PX, folder=FOCUS_FOLDER
    )


@dataclass
class BackfillReport:
    made_thumbs: int = 0
    made_focus: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def made(self) -> int:
        return self.made_thumbs + self.made_focus


async def backfill_missing_portfolio_derivatives(
    db,
    *,
    only_slug: str | None = None,
    apply: bool = True,
    rebuild: bool = False,
    fetch_original=None,
    log=print,
) -> BackfillReport:
    """Give every portfolio original its thumb and focus derivative, recording the mappings.

    Idempotent and additive by default: an original that already has both is skipped; a
    partial failure leaves the rest recorded, and the next run only does what is still
    missing. ``rebuild`` overwrites each derivative at its existing deterministic object
    key while leaving originals untouched.
    `db` is an AsyncSession; `fetch_original(url) -> bytes | None` defaults to an httpx GET.
    Originals are never deleted or overwritten. Default mode only adds missing objects and
    mappings; rebuild mode replaces derivative objects at their existing keys and refreshes
    the two mappings.
    """
    import httpx
    from sqlalchemy import select

    from app.models.user import BTTCOUser

    report = BackfillReport()
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:

        async def default_fetch(url: str) -> bytes | None:
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                log(f"    download failed: {exc}")
                return None
            if response.status_code != 200:
                log(f"    download failed: HTTP {response.status_code}")
                return None
            return response.content

        fetch = fetch_original or default_fetch
        rows = (
            (
                await db.execute(
                    select(BTTCOUser)
                    .where(BTTCOUser.role.in_(("artist", "founder")), BTTCOUser.slug.isnot(None))
                    .order_by(BTTCOUser.name)
                )
            )
            .scalars()
            .all()
        )
        for artist in rows:
            slug = artist.slug or str(artist.id)
            if only_slug and slug != only_slug:
                continue
            images = [u for u in (artist.portfolio_images or []) if isinstance(u, str) and u.startswith("http")]
            thumbs = dict(artist.portfolio_thumbs or {})
            focus = dict(artist.portfolio_focus or {})
            pending = list(images) if rebuild else [u for u in images if u not in thumbs or u not in focus]
            complete = 0 if rebuild else len(images) - len(pending)
            action = "to rebuild" if rebuild else "to make"
            log(f"{slug}: {len(images)} images, {complete} complete, {len(pending)} {action}")
            report.skipped += complete
            for url in pending:
                name = url.rsplit("/", 1)[-1]
                if not apply:
                    make_thumb = rebuild or url not in thumbs
                    make_focus = rebuild or url not in focus
                    log(f"    would make {'thumb ' if make_thumb else ''}{'focus' if make_focus else ''} for {name}")
                    continue
                data = await fetch(url)
                if data is None:
                    report.failed += 1
                    continue
                if rebuild or url not in thumbs:
                    thumb_url = await upload_portfolio_thumb(data, slug=slug, original_name=name)
                    if thumb_url:
                        thumbs[url] = thumb_url
                        report.made_thumbs += 1
                    else:
                        report.failed += 1
                if rebuild or url not in focus:
                    focus_url = await upload_portfolio_focus(data, slug=slug, original_name=name)
                    if focus_url:
                        focus[url] = focus_url
                        report.made_focus += 1
                    else:
                        report.failed += 1
                artist.portfolio_thumbs = dict(thumbs) or None
                artist.portfolio_focus = dict(focus) or None
                await db.commit()
                log(
                    f"    {name}: thumb {'ok' if url in thumbs else 'missing'}, focus {'ok' if url in focus else 'missing'}"
                )
    log(
        f"done: {report.made_thumbs} thumbs, {report.made_focus} focus made, {report.skipped} complete, "
        f"{report.failed} failed{'' if apply else ' (dry run)'}"
    )
    return report


def thumbs_for(images: list[str] | None, thumbs: dict | None) -> list[str | None]:
    """Parallel list of thumb URLs for a portfolio_images list (None where no thumb exists)."""
    if not images:
        return []
    lookup = thumbs if isinstance(thumbs, dict) else {}
    return [lookup.get(url) if isinstance(url, str) else None for url in images]
