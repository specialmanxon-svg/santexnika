"""Топшириқлар ва Назорат — Task ORM модели."""
from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Task(Base):
    """Раҳбар → Ходим топшириқлари."""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ижрочи ходим
    assigned_to: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    assigned_name: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Кузатувчи / Назоратчи (ихтиёрий)
    observer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observer_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Муддат
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Статус: new, in_progress, completed, expired
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)

    # Ходим жавоби / ҳисоботи
    employee_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Овозли хабар файли йўли
    voice_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Эслатма юборилганми
    reminder_sent: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
