"""Retain confirmed sign-in identities during artist account consolidation.

The existing same-email dedupe cannot represent multiple verified identities.
Extend that owner with one private alias table, not another user account path.
"""

from alembic import op
from app.models.user import UserIdentityAlias

revision = "20261004_identity_aliases"
down_revision = "20261001_account_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    UserIdentityAlias.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    UserIdentityAlias.__table__.drop(op.get_bind(), checkfirst=True)
