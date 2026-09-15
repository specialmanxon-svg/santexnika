"""SQLAlchemy model for Field HR Timesheets with GPS and EXIF verification."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, Float, Boolean, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class WorkTimesheet(Base):
    """Табель учета рабочего времени полевых инженеров."""
    __tablename__ = "work_timesheets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    employee_id: Mapped[int] = mapped_column(Integer, index=True)
    employee_name: Mapped[str] = mapped_column(String(255))
    object_name: Mapped[str] = mapped_column(String(255))
    checkin_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    checkout_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    gps_accuracy: Mapped[float] = mapped_column(Float)
    is_spoof_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    exif_time_delta_sec: Mapped[int] = mapped_column(Integer, default=0)
    total_hours: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED")
