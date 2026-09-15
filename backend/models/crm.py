"""SQLAlchemy models for Diyorgroup Native CRM (diyorgroup.uz/crm)."""
from datetime import datetime
from uuid import UUID, uuid4
from enum import Enum
from sqlalchemy import String, Text, Boolean, Integer, Float, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base


class CrmPipelineType(str, Enum):
    B2C_SHOWROOM = "B2C_SHOWROOM"          # Шоурум сантехники (дизайнеры 7%)
    B2B_OBJECT = "B2B_OBJECT"              # Объектное снабжение (жесткий контроль долгов)


class CrmStageType(str, Enum):
    NEW = "NEW"                            # Новый лид
    QUALIFIED = "QUALIFIED"                # Квалифицирован AI Sales
    ESTIMATE_CALCULATED = "ESTIMATE_CALCULATED" # Смета проверена на совместимость
    INVOICE_ISSUED = "INVOICE_ISSUED"      # Счет выставлен (Триггер резерва в МойСклад)
    PAID = "PAID"                          # Оплачен (Триггер комиссии 7% дизайнеру)
    SHIPPED = "SHIPPED"                    # Отгружен
    CLOSED_WON = "CLOSED_WON"              # Успешно завершен
    CLOSED_LOST = "CLOSED_LOST"            # Провален


class CrmLead(Base):
    """Лид в воронке Diyorgroup CRM."""
    __tablename__ = "crm_leads"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contact_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(64))  # Meta Ads, Instagram, Telegram, Website
    intent_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_type: Mapped[CrmPipelineType] = mapped_column(SQLEnum(CrmPipelineType), default=CrmPipelineType.B2C_SHOWROOM)
    stage: Mapped[CrmStageType] = mapped_column(SQLEnum(CrmStageType), default=CrmStageType.NEW)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CrmDeal(Base):
    """Сделка в Diyorgroup CRM."""
    __tablename__ = "crm_deals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255))
    pipeline_type: Mapped[CrmPipelineType] = mapped_column(SQLEnum(CrmPipelineType), default=CrmPipelineType.B2C_SHOWROOM)
    stage: Mapped[CrmStageType] = mapped_column(SQLEnum(CrmStageType), default=CrmStageType.NEW)
    counterparty_name: Mapped[str] = mapped_column(String(255))
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    moysklad_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    designer_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    designer_commission: Mapped[float] = mapped_column(Float, default=0.0)
    compat_status: Mapped[str] = mapped_column(String(64), default="CHECK_PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CrmTask(Base):
    """Задача сотрудника в Diyorgroup CRM."""
    __tablename__ = "crm_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(String(64), default="LEAD_WARMUP")  # LEAD_WARMUP, FIELD_VISIT, INSPECTION
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("crm_deals.id", ondelete="SET NULL"), nullable=True)
    assigned_to: Mapped[str] = mapped_column(String(128), default="manager")
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
