"""Widen bttco_sms_messages.error_code to 40 characters.

Revision ID: 20260921_sms_error_code
Revises: 20260921_preferred_slots

Sender lane configuration codes such as SENDER_NOT_CONFIGURED are longer than
the original varchar(20). The overflow failed the flush that logged the SMS and
rolled back the request session, so a public booking submit returned 500 after
the booking row had already been prepared.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260921_sms_error_code"
down_revision: str | None = "20260921_preferred_slots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "bttco_sms_messages",
        "error_code",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "bttco_sms_messages",
        "error_code",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
