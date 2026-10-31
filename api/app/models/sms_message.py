"""SMS message log — every outbound and inbound message routes through here."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SmsMessage(Base):
    __tablename__ = "ryozan_sms_messages"
    __table_args__ = (
        Index("ix_sms_messages_created_at", "created_at"),
        Index("ix_sms_messages_direction", "direction"),
        Index("ix_sms_messages_status", "status"),
        Index("ix_sms_messages_to_phone", "to_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # outbound | inbound
    to_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    from_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    # 40: lane configuration codes such as SENDER_NOT_CONFIGURED exceed 20 characters,
    # and an overflow here poisons the request session that logged the send.
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    twilio_sid: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    template_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    courier_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ryozan_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sender_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
