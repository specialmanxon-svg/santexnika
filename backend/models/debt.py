from sqlalchemy import String, Numeric, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime
from datetime import datetime

from .base import Base, UUIDMixin

class DebtRegistry(Base, UUIDMixin):
    __tablename__ = "debt_registry"

    company_moysklad_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    company_title: Mapped[str] = mapped_column(String(255), nullable=False)
    debt_0_30: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    debt_30_60: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    debt_60_plus: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=datetime.utcnow
    )
