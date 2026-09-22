"""SQLAlchemy models for Partner Attribution (5% бонус дастури)."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, Boolean, Integer, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class PartnerProfile(Base):
    """Ҳамкорлар базаси: прораблар, сантехниклар, дизайнерлар."""
    __tablename__ = "partner_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    role_type: Mapped[str] = mapped_column(String(64), default="plumber")  # plumber, designer, foreman
    region: Mapped[str] = mapped_column(String(64), default="Бухара")
    referral_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.05)
    total_deals_count: Mapped[int] = mapped_column(Integer, default=0)
    total_sales_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total_commission_paid: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
