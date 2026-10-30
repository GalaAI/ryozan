"""Durable OAuth records for the remote BTTCO MCP authorization server.

Net-new justification: BTTCO already owns user identity and MCP tools, but it
has no durable OAuth client, one-time authorization code, or refresh-token
records. These records extend that authority without creating a second user
system and keep remote client grants revocable and auditable.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class McpOAuthClient(Base):
    __tablename__ = "bttco_mcp_oauth_clients"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    client_id: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    grant_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    response_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(40), nullable=False, default="none")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class McpOAuthAuthorizationCode(Base):
    __tablename__ = "bttco_mcp_oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class McpOAuthRefreshToken(Base):
    __tablename__ = "bttco_mcp_oauth_refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
