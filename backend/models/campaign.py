"""SQLAlchemy model for Telegram Segmented Broadcast Campaigns with Double Check."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, Text, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from models.base import Base


class TelegramCampaign(Base):
    """Сегментированные рассылки в Telegram (/send_message)."""
    __tablename__ = "telegram_campaigns"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    segment_type: Mapped[str] = mapped_column(String(64))  # LEADS_WARMUP, DEBTORS, CREDITORS
    message_text: Mapped[str] = mapped_column(Text)
    attach_reconciliation_act: Mapped[bool] = mapped_column(Boolean, default=False)
    target_count: Mapped[int] = mapped_column(Integer, default=0)
    is_previewed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_double_check_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")  # DRAFT, PREVIEW, CONFIRMED, COMPLETED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
