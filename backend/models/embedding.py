import uuid
from datetime import datetime
from sqlalchemy import String, Text, ForeignKey, JSON, Uuid, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin

class TechnicalEmbedding(Base, UUIDMixin):
    __tablename__ = "technical_embeddings"

    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, 
        ForeignKey("mdm_products.id", ondelete="CASCADE"), 
        nullable=False
    )
    doc_chunk: Mapped[str] = mapped_column(Text, nullable=False)
    source_document: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=datetime.utcnow
    )
