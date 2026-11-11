"""Startup never waits on storage (R2).

Railway fails a deploy whose health check does not answer within its window. A boot step that awaits
an upload, a move or a delete on R2 ties startup to how fast storage answers, so every storage pass
runs in a background task started after the DB-only boot steps. Here every storage call hangs forever:
the lifespan must still reach its yield, the storage passes must be running in the background, and
shutdown must cancel them cleanly.

No database and no network: the DB-only boot steps run against fakes. Synthetic values only.
"""

import asyncio
import inspect
import os
import unittest
from contextlib import ExitStack, redirect_stdout, suppress
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

import main  # noqa: E402
from app.services import scroll_archive, storage  # noqa: E402

OUTBOX_WORKERS = (
    "retry_pending_scroll_pdfs_once",
    "process_owner_email_outbox_once",
    "retry_public_scroll_pdf_deletions_once",
    "process_booking_email_outbox_once",
    "process_byof_deposit_reviews_once",
    "process_booky_courier_outbox_once",
    "process_courier_notification_outbox_once",
    "process_gallery_deletion_outbox_once",
    "queue_recent_learning_captures_once",
    "process_pack_bridge_outbox_once",
    "process_apex_training_manifest_once",
)


class _HangingStorage:
    """Every storage call blocks until cancelled, recording that it started and that it was cancelled."""

    def __init__(self):
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.any_started = asyncio.Event()

    def call(self, name):
        async def _hang(*_args, **_kwargs):
            self.started.append(name)
            self.any_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(name)
                raise

        return _hang


class _FakeConn:
    """Stands in for both the SQLAlchemy and asyncpg connections the DB-only seeds use."""

    async def run_sync(self, _fn):
        return 0

    async def execute(self, *_args, **_kwargs):
        return MagicMock(rowcount=0)

    async def fetchval(self, *_args, **_kwargs):
        return 7

    async def close(self):
        return None


class _FakeEngine:
    def begin(self):
        conn = _FakeConn()

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()


class StartupNeverAwaitsStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_reaches_yield_while_storage_hangs_and_shutdown_cancels_cleanly(self):
        hanging = _HangingStorage()

        async def _unstored_pass(limit=20):
            # The real worker body would upload; here the upload hangs.
            await storage.upload_file(file_bytes=b"%PDF synthetic", filename="x.pdf", mime_type="application/pdf")
            return 0

        with ExitStack() as stack:
            enter = stack.enter_context
            # DB-only boot steps run against fakes.
            enter(patch.object(main, "engine", _FakeEngine()))
            enter(patch.object(main.asyncpg, "connect", AsyncMock(return_value=_FakeConn())))
            enter(patch.object(main, "_check_schema_revision", AsyncMock()))
            enter(patch.object(main, "_load_saved_studio_settings", AsyncMock()))
            enter(patch.object(main, "_ensure_demo_account", AsyncMock()))
            enter(patch.object(main, "_ensure_demo_shop", AsyncMock(return_value=False)))
            enter(patch.object(main, "_announce_first_run_setup", AsyncMock()))
            enter(patch.object(main.settings, "PORTFOLIO_DERIVATIVES_ON_STARTUP", False))
            enter(patch.object(scroll_archive, "clear_sent_owner_email_attachments", AsyncMock(return_value=(0, 0, 0))))
            enter(patch.object(scroll_archive, "count_legacy_scroll_references", AsyncMock(return_value=0)))
            # Every storage path hangs.
            enter(patch.object(storage, "upload_file", hanging.call("upload")))
            enter(patch.object(scroll_archive, "retry_unstored_scroll_pdfs", hanging.call("retry at boot")))
            enter(patch.object(scroll_archive, "move_public_scroll_pdfs_once", hanging.call("boot move")))
            enter(patch.object(main, "process_unstored_scroll_pdfs_once", _unstored_pass))
            for worker in OUTBOX_WORKERS:
                enter(patch.object(main, worker, AsyncMock(return_value=0)))
            output = StringIO()
            enter(redirect_stdout(output))

            context = main.lifespan(main.app)
            # Startup must finish promptly even though storage never answers.
            await asyncio.wait_for(context.__aenter__(), timeout=5)
            self.assertIn("Startup seeds: done", output.getvalue())
            self.assertNotIn("Startup seed FAILED", output.getvalue())
            self.assertNotIn("retry at boot", hanging.started)

            # The storage passes run in the background once startup is done.
            await asyncio.wait_for(hanging.any_started.wait(), timeout=5)
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertIn("boot move", hanging.started)
            self.assertIn("upload", hanging.started)

            # Shutdown cancels them cleanly: no hang, no leaked exception.
            await asyncio.wait_for(context.__aexit__(None, None, None), timeout=5)
            self.assertEqual(sorted(hanging.cancelled), sorted(hanging.started))
            self.assertIn("Shutting down", output.getvalue())

    async def test_hanging_storage_recovery_never_delays_outbound_email(self):
        """A storage outage stalls only the storage recovery loop; every email worker still runs."""
        hanging = _HangingStorage()
        email_ran = asyncio.Event()

        async def _booking_email():
            email_ran.set()
            return 0

        storage_workers = (
            "retry_pending_scroll_pdfs_once",
            "process_unstored_scroll_pdfs_once",
            "retry_public_scroll_pdf_deletions_once",
        )
        with ExitStack() as stack:
            enter = stack.enter_context
            for worker in OUTBOX_WORKERS:
                enter(patch.object(main, worker, AsyncMock(return_value=0)))
            for worker in storage_workers:
                enter(patch.object(main, worker, hanging.call(worker)))
            enter(patch.object(main, "process_booking_email_outbox_once", _booking_email))
            recovery = asyncio.create_task(main._run_scroll_pdf_storage_recovery())
            outbox = asyncio.create_task(main._run_owner_email_outbox())
            try:
                await asyncio.wait_for(hanging.any_started.wait(), timeout=5)
                await asyncio.wait_for(email_ran.wait(), timeout=5)
                self.assertEqual(hanging.started, ["retry_pending_scroll_pdfs_once"])
            finally:
                for task in (outbox, recovery):
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    def test_email_loop_names_no_storage_pass(self):
        source = inspect.getsource(main._run_owner_email_outbox)
        for name in (
            "retry_pending_scroll_pdfs_once",
            "process_unstored_scroll_pdfs_once",
            "retry_public_scroll_pdf_deletions_once",
        ):
            self.assertNotIn(name, source)

    def test_no_startup_seed_step_names_a_storage_pass(self):
        source = inspect.getsource(main._run_startup_seeds)
        for name in (
            "retry_unstored_scroll_pdfs",
            "process_unstored_scroll_pdfs_once",
            "move_public_scroll_pdfs",
            "retry_public_scroll_pdf_deletions",
            "retry_pending_scroll_pdfs",
            "upload_file",
            "delete_file",
            "storage.",
        ):
            self.assertNotIn(name, source)


if __name__ == "__main__":
    unittest.main()
