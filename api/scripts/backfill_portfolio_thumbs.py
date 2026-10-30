"""Backfill portfolio derivatives (640px thumb and 1400px focus WebP) for existing images.

Delegates to app.services.portfolio_thumbs.backfill_missing_portfolio_derivatives, the same
pass the API runs in the background at startup. Idempotent and additive: an image that
already has both derivatives is skipped, a partial failure leaves the rest recorded, and
nothing in R2 or the database is deleted or overwritten besides the two mappings.

    python scripts/backfill_portfolio_thumbs.py            # dry run: prints the plan, writes nothing
    python scripts/backfill_portfolio_thumbs.py --apply    # upload derivatives and record them
    python scripts/backfill_portfolio_thumbs.py --apply --slug justin
    python scripts/backfill_portfolio_thumbs.py --apply --rebuild --slug brett

Rebuild mode overwrites only the deterministic thumb/focus objects. Original uploads remain
untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models.booking  # noqa: F401  (mapper must exist for the user relationships)
from app.core.database import async_session
from app.services.portfolio_thumbs import backfill_missing_portfolio_derivatives


async def run(apply: bool, only_slug: str | None, rebuild: bool) -> int:
    async with async_session() as db:
        report = await backfill_missing_portfolio_derivatives(db, only_slug=only_slug, apply=apply, rebuild=rebuild)
    return 1 if report.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="upload derivatives and record them (default: dry run)")
    parser.add_argument("--slug", help="only this artist")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="regenerate existing derivatives at the same object keys (originals remain unchanged)",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.apply, args.slug, args.rebuild))


if __name__ == "__main__":
    raise SystemExit(main())
