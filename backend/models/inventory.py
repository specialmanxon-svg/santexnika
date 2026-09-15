"""SQLAlchemy model for ABC/XYZ inventory analysis and non-liquid incentives."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, Integer, Float, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class InventoryItem(Base):
    """Товарная позиция с ABC/XYZ классификацией и триггером бонуса менеджера."""
    __tablename__ = "inventory_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str] = mapped_column(String(64))
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    days_in_stock: Mapped[int] = mapped_column(Integer, default=0)  # Дни без движения
    abc_category: Mapped[str] = mapped_column(String(8), default="A")  # A, B, C
    xyz_category: Mapped[str] = mapped_column(String(8), default="X")  # X, Y, Z
    is_non_liquid: Mapped[bool] = mapped_column(Boolean, default=False)  # > 90 дней
    manager_bonus_percent: Mapped[float] = mapped_column(Float, default=0.0)  # +5% при неликвиде
    last_sale_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
