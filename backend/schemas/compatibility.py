from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class CompatibilityStatus(str, Enum):
    APPROVED = "APPROVED"
    ESCALATED = "ESCALATED"
    REJECTED = "REJECTED"

class CompatibilityCheckRequest(BaseModel):
    primary_sku: str = Field(..., description="Base product SKU")
    target_sku: str = Field(..., description="Target product SKU to check compatibility")

class CompatibilityCheckResponse(BaseModel):
    status: CompatibilityStatus
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    action: Optional[str] = None
    notice: Optional[str] = None
