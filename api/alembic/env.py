"""
Alembic migration environment.

Imports all models so autogenerate can detect schema changes.
Uses async engine from our database module.
"""

from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection

from alembic import context

# Import Base and all models so autogenerate sees them
from app.core.config import get_settings
from app.core.database import Base
from app.models.ai_memory import AIMemory  # noqa: F401
from app.models.api_usage import APIUsage  # noqa: F401
from app.models.booking import Booking  # noqa: F401
from app.models.booking_draft import BookingDraft  # noqa: F401
from app.models.booking_lifecycle_event import BookingLifecycleEvent  # noqa: F401
from app.models.booking_slot_hold import BookingSlotHold  # noqa: F401
from app.models.broadcast import Broadcast, GalaFeedback  # noqa: F401
from app.models.club import ClubPost, ClubPostLike  # noqa: F401
from app.models.crm_pipeline_task import CrmPipelineTask  # noqa: F401
from app.models.document import DocumentInstance, DocumentTemplate  # noqa: F401
from app.models.econ import EconConnection, EconLedgerEntry, EconSyncRun  # noqa: F401
from app.models.email_log import EmailLog  # noqa: F401
from app.models.email_route import EmailRoute  # noqa: F401
from app.models.email_template import EmailTemplate  # noqa: F401
from app.models.flash import FlashDesign, FlashSheet, ModelFeedbackSample  # noqa: F401
from app.models.gallery_deletion_outbox import GalleryDeletionOutbox  # noqa: F401
from app.models.gift_card import GiftCard, GiftCardRedemption  # noqa: F401
from app.models.guild import (  # noqa: F401
    ClassEnrollment,
    GauntletAttempt,
    GauntletChallenge,
    GuildClass,
    Inspiration,
    LeaderboardEntry,
)
from app.models.mcp_oauth import (  # noqa: F401
    McpOAuthAuthorizationCode,
    McpOAuthClient,
    McpOAuthRefreshToken,
)
from app.models.message import Conversation, ConversationParticipant, Message  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.notification_preferences import NotificationPreferences  # noqa: F401
from app.models.pack_bridge_outbox import PackBridgeOutbox  # noqa: F401
from app.models.pos_transaction import POSTransaction  # noqa: F401
from app.models.push_subscription import PushSubscription  # noqa: F401
from app.models.quest import Quest, UserQuest  # noqa: F401
from app.models.quote import Quote  # noqa: F401
from app.models.reference import Reference, ReferenceView  # noqa: F401
from app.models.response_template import ResponseTemplate  # noqa: F401
from app.models.scroll_category import ScrollCategory  # noqa: F401
from app.models.scroll_owner_email_outbox import ScrollOwnerEmailOutbox  # noqa: F401
from app.models.session_type import ArtistSessionType  # noqa: F401
from app.models.sms_message import SmsMessage  # noqa: F401
from app.models.sms_template import SmsTemplate  # noqa: F401
from app.models.stash import StashCanvasSession, StashItem  # noqa: F401
from app.models.time_block import TimeBlock  # noqa: F401
from app.models.user import BTTCOUser  # noqa: F401
from app.models.viber import (  # noqa: F401
    ViberAccount,
    ViberAIProvider,
    ViberConnection,
    ViberProposal,
)
from app.models.workspace import (  # noqa: F401
    Workspace,
    WorkspaceChannel,
    WorkspaceFeatureStatus,
    WorkspaceLog,
    WorkspaceNote,
    WorkspaceTodo,
)

settings = get_settings()
config = context.config

# Override sqlalchemy.url — ensure sync (psycopg2) driver for migrations
_sync_url = settings.DATABASE_SYNC_URL
if "+asyncpg" in _sync_url:
    _sync_url = _sync_url.replace("+asyncpg", "")
config.set_main_option("sqlalchemy.url", _sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

BTTCO_OWNED_TABLE_PREFIXES = ("bttco_", "viber_")
BTTCO_OWNED_TABLES = set(target_metadata.tables)


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Keep autogenerate scoped to BTTCO-owned tables in the shared Nexus database."""
    if type_ == "table":
        if name is None:
            return True
        return name in BTTCO_OWNED_TABLES or name.startswith(BTTCO_OWNED_TABLE_PREFIXES)

    table_name = parent_names.get("table_name")
    if table_name:
        return table_name in BTTCO_OWNED_TABLES or table_name.startswith(BTTCO_OWNED_TABLE_PREFIXES)

    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database using sync engine."""
    from sqlalchemy import create_engine

    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
