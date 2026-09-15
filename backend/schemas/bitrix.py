from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from enum import Enum

class DealType(str, Enum):
    B2C_SHOWROOM = "B2C_SHOWROOM"
    B2B_OBJECT = "B2B_OBJECT"

class CompatStatus(str, Enum):
    CHECK_PENDING = "CHECK_PENDING"
    COMPATIBLE = "COMPATIBLE"
    HUMAN_REVIEW_REQ = "HUMAN_REVIEW_REQ"

class BitrixWebhookPayload(BaseModel):
    """Payload received from Bitrix24 outbound webhook."""
    event: str
    data: dict
    ts: str
    auth: dict
    
    @property
    def entity_id(self) -> int:
        return int(self.data.get("FIELDS", {}).get("ID", 0))
    
    @property
    def application_token(self) -> str:
        return self.auth.get("application_token", "")

class DealProductRow(BaseModel):
    id: int = Field(alias="ID")
    product_id: int = Field(alias="PRODUCT_ID")
    product_name: str = Field(alias="PRODUCT_NAME")
    price: float = Field(alias="PRICE")
    quantity: float = Field(alias="QUANTITY")
    discount_rate: float = Field(default=0, alias="DISCOUNT_RATE")
    discount_sum: float = Field(default=0, alias="DISCOUNT_SUM")
    tax_rate: Optional[float] = Field(default=None, alias="TAX_RATE")
    model_config = ConfigDict(populate_by_name=True)

class DealUpdate(BaseModel):
    """Fields to update on a Bitrix24 deal."""
    uf_ms_order_id: Optional[str] = None
    uf_designer_commission: Optional[float] = None
    uf_compat_status: Optional[CompatStatus] = None
    stage_id: Optional[str] = None

class CompanyUpdate(BaseModel):
    """Fields to update on a Bitrix24 company."""
    uf_debt_total: Optional[float] = None
    uf_debt_overdue_60: Optional[float] = None
    uf_shipment_blocked: Optional[bool] = None
    uf_override_active: Optional[bool] = None
