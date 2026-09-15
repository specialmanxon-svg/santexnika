from sqlalchemy import String, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin

class CompatibilityRule(Base, UUIDMixin):
    __tablename__ = "compatibility_rules"

    base_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    compatible_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    component_type: Mapped[str] = mapped_column(String(64), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint('base_sku', 'compatible_sku', name='unique_compat_pair'),
    )
