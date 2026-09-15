"""SQLAlchemy models for Social Intelligence and Partner Attribution."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, Text, Boolean, Integer, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class PartnerProfile(Base):
    """Карточки дизайнеров и архитекторов с Designer Attribution."""
    __tablename__ = "partner_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    instagram_username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    role_type: Mapped[str] = mapped_column(String(64), default="designer")
    source_account: Mapped[str] = mapped_column(String(128))
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bitrix_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.07)
    total_deals_count: Mapped[int] = mapped_column(Integer, default=0)
    total_commission_paid: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SocialStoryLead(Base):
    """Результаты сканера Stories (ремонт, сантехника, смеситель)."""
    __tablename__ = "social_story_leads"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    author_username: Mapped[str] = mapped_column(String(128), index=True)
    story_url: Mapped[str] = mapped_column(String(512))
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    detected_keywords: Mapped[list] = mapped_column(JSON, default=list)
    lead_intent_score: Mapped[float] = mapped_column(Float, default=0.85)
    crm_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="PENDING_WARMUP")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PlumberProfile(Base):
    """Plumber Radar: База сантехников с тегированием квалификации."""
    __tablename__ = "plumber_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    city: Mapped[str] = mapped_column(String(64), default="Бухара")
    brand_specialization: Mapped[str] = mapped_column(String(128))
    tier_level: Mapped[str] = mapped_column(String(32), default="SILVER")
    preferred_categories: Mapped[list] = mapped_column(JSON, default=list)
    kp_template_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bitrix_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
