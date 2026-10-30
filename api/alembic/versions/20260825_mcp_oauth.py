"""add durable MCP OAuth clients, codes, and refresh tokens

Net-new justification: the existing BTTCO JWT tables authenticate site users,
but remote MCP OAuth also requires registered clients, single-use PKCE codes,
and revocable refresh tokens. The migration mirrors the ORM tables that
production creates through the existing additive startup path.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260825_mcp_oauth"
down_revision: str | Sequence[str] | None = "20260824_sheet_crop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bttco_mcp_oauth_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", sa.String(160), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column("redirect_uris", postgresql.JSONB(), nullable=False),
        sa.Column("grant_types", postgresql.JSONB(), nullable=False),
        sa.Column("response_types", postgresql.JSONB(), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_bttco_mcp_oauth_clients_client_id",
        "bttco_mcp_oauth_clients",
        ["client_id"],
        unique=True,
    )
    op.create_table(
        "bttco_mcp_oauth_authorization_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(160), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bttco_mcp_oauth_authorization_codes_client_id", "bttco_mcp_oauth_authorization_codes", ["client_id"])
    op.create_index("ix_bttco_mcp_oauth_authorization_codes_user_id", "bttco_mcp_oauth_authorization_codes", ["user_id"])
    op.create_index("ix_bttco_mcp_oauth_authorization_codes_expires_at", "bttco_mcp_oauth_authorization_codes", ["expires_at"])
    op.create_table(
        "bttco_mcp_oauth_refresh_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(160), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bttco_mcp_oauth_refresh_tokens_client_id", "bttco_mcp_oauth_refresh_tokens", ["client_id"])
    op.create_index("ix_bttco_mcp_oauth_refresh_tokens_user_id", "bttco_mcp_oauth_refresh_tokens", ["user_id"])
    op.create_index("ix_bttco_mcp_oauth_refresh_tokens_expires_at", "bttco_mcp_oauth_refresh_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("bttco_mcp_oauth_refresh_tokens")
    op.drop_table("bttco_mcp_oauth_authorization_codes")
    op.drop_table("bttco_mcp_oauth_clients")
