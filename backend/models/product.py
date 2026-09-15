from sqlalchemy import String, Numeric, Integer, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime
from datetime import datetime

from .base import Base, UUIDMixin

class MdmProduct(Base, UUIDMixin):
    __tablename__ = "mdm_products"

    moysklad_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sku: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str] = mapped_column(String(64), nullable=False)
    purchase_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    retail_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    stock_free: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stock_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=datetime.utcnow, 
        server_default=text("NOW()"), 
        onupdate=datetime.utcnow
    )
