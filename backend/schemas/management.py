import re
from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Any

class OverrideLockRequest(BaseModel):
    company_id: Optional[str] = Field(default="all", description="MoySklad counterparty ID, title or 'all'")
    company_moysklad_id: Optional[str] = Field(default=None, description="Legacy field for backwards compatibility")
    otp_code: Optional[str] = Field(default=None, description="One-time password")
    pin: Optional[str] = Field(default=None, description="Alias for otp_code")
    reason: str = Field(default="CEO Emergency Override", description="Reason for override")

    @model_validator(mode="before")
    @classmethod
    def unify_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw_pin = data.get("pin") or data.get("otp_code") or ""
            digits = "".join(re.findall(r"\d", str(raw_pin)))
            data["pin"] = digits
            data["otp_code"] = digits
            
            cid = data.get("company_id") or data.get("company_moysklad_id") or "all"
            data["company_id"] = str(cid).strip()
            data["company_moysklad_id"] = str(cid).strip()
        return data

    def get_pin(self) -> str:
        return self.pin or self.otp_code or ""

    def get_target_company(self) -> str:
        return self.company_id or self.company_moysklad_id or "all"

class OverrideLockResponse(BaseModel):
    success: bool
    message: str
    unblocked_companies: List[str] = []
    audit_event_id: Optional[str] = None

class BlockedCompanyItem(BaseModel):
    id: str
    company_moysklad_id: str
    company_title: str
    debt_60_plus: float
    is_blocked: bool
