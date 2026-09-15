from pydantic import BaseModel, Field
from typing import Optional

class OverrideLockRequest(BaseModel):
    company_moysklad_id: str = Field(..., description="MoySklad counterparty UUID")
    otp_code: str = Field(..., min_length=6, max_length=6, description="One-time password")
    reason: str = Field(..., min_length=10, description="Reason for override")

class OverrideLockResponse(BaseModel):
    success: bool
    message: str
    audit_event_id: Optional[str] = None
