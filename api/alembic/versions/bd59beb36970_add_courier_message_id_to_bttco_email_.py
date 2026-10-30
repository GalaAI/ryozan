"""add courier_message_id to bttco_email_log

Revision ID: bd59beb36970
Revises: 20260627_time_blocks
Create Date: 2026-07-02 10:02:46.992352
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bd59beb36970'
down_revision: Union[str, None] = '20260627_time_blocks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bttco_email_log', sa.Column('courier_message_id', sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column('bttco_email_log', 'courier_message_id')
