"""SQLAlchemy model for Employee Timesheets — Ходимлар давомади."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, Float, Integer, BigInteger, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base



class WorkTimesheet(Base):
    """Ишчиларнинг ишга келиш ва кетиш давомади."""
    __tablename__ = "work_timesheets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    employee_id: Mapped[int] = mapped_column(Integer, index=True)
    employee_name: Mapped[str] = mapped_column(String(255))
    object_name: Mapped[str] = mapped_column(String(255), default="Марказий дўкон (Бухоро)")
    checkin_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    checkout_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_hours: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="CHECKED_IN")  # CHECKED_IN, CHECKED_OUT
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_accuracy: Mapped[float] = mapped_column(Float, default=10.0)
    is_spoof_verified: Mapped[bool] = mapped_column(Integer, default=1)
    exif_time_delta_sec: Mapped[int] = mapped_column(Integer, default=0)
    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    attendance_status: Mapped[str | None] = mapped_column(String(128), nullable=True)  # Ўз вақтида (GPS тасдиқланди), Кечикди, etc.
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Workplace(Base):
    """Иш жойлари ва Объектлар (Geofences) — Дўконлар, омборлар ва монтаж объектлари."""
    __tablename__ = "workplaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_meters: Mapped[float] = mapped_column(Float, default=100.0)
    is_active: Mapped[int] = mapped_column(Integer, default=1)  # 1: актив, 0: нофаол/тугатилган
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuthorizedEmployee(Base):
    """Telegram ботга телефон орқали боғланган тасдиқланган ходимлар."""
    __tablename__ = "authorized_employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True, unique=True, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    moysklad_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(100), default="Ходим")
    is_active: Mapped[int] = mapped_column(Integer, default=1)  # 1: актив, 0: блокланган
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


