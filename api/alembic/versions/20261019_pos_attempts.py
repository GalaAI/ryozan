"""Extend the existing POS ledger with versioned, durable collection attempts.

The previous POS pricing migration cannot represent an unsettled attempt or its
account and evidence snapshots. This additive revision keeps those facts in the
existing transaction owner, with all legacy values and booking references intact.
"""

import sqlalchemy as sa

from alembic import op
from app.core.schema_sync import install_pos_attempt_v1_constraints
from app.models.pos_transaction import POS_ATTEMPT_V1_COLUMNS, POSTransaction

revision = "20261019_pos_attempts"
down_revision = "20261004_identity_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    existing = {column["name"] for column in sa.inspect(connection).get_columns("bttco_pos_transactions")}
    # The v1 column/constraint contract is versioned in the canonical model,
    # following this repository's shared model-backed additive migration path.
    for name in POS_ATTEMPT_V1_COLUMNS:
        if name not in existing:
            column = POSTransaction.__table__.c[name]
            op.add_column("bttco_pos_transactions", sa.Column(name, column.type, nullable=True))
    install_pos_attempt_v1_constraints(connection)


def downgrade() -> None:
    # A rollback may remove an unused contract, never discard durable payment
    # attempts. Callers must resolve the owning release before removing data.
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("bttco_pos_transactions")}
    if "attempt_version" not in columns:
        return
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM bttco_pos_transactions WHERE attempt_version IS NOT NULL)")
    ).scalar():
        raise RuntimeError("Cannot remove POS attempt schema while durable attempts exist")
    for index in sa.inspect(connection).get_indexes("bttco_pos_transactions"):
        if index["name"].startswith("ux_pos_attempt_v1_"):
            op.drop_index(index["name"], table_name="bttco_pos_transactions")
    for constraint in sa.inspect(connection).get_check_constraints("bttco_pos_transactions"):
        if constraint["name"].startswith("ck_pos_attempt_v1_"):
            op.drop_constraint(constraint["name"], "bttco_pos_transactions", type_="check")
    for name in reversed(POS_ATTEMPT_V1_COLUMNS):
        if name in columns:
            op.drop_column("bttco_pos_transactions", name)
