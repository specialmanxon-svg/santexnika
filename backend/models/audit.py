from sqlalchemy import String, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from .base import Base, UUIDMixin

class AuditEvent(Base, UUIDMixin):
    __tablename__ = "audit_events"

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=datetime.utcnow
    )
