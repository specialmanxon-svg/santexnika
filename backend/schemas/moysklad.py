from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from uuid import UUID

class MetaRef(BaseModel):
    """MoySklad meta reference object."""
    href: str
    type: str
    media_type: str = Field(default="application/json", alias="mediaType")
    model_config = ConfigDict(populate_by_name=True)

class EntityMeta(BaseModel):
    meta: MetaRef

class OrderPosition(BaseModel):
    """Position in a customer order."""
    quantity: int
    price: int  # In kopeks (smallest unit)
    reserve: int
    assortment: EntityMeta

class CustomerOrderCreate(BaseModel):
    """Request body for creating a customer order in MoySklad."""
    name: Optional[str] = None
    description: Optional[str] = None
    organization: EntityMeta
    agent: EntityMeta  # Counterparty
    state: Optional[EntityMeta] = None
    positions: List[OrderPosition]

class CustomerOrderResponse(BaseModel):
    """Response from MoySklad after creating/getting a customer order."""
    id: str
    name: str
    sum: Optional[int] = None  # Total in kopeks
    positions: Optional[dict] = None
    state: Optional[EntityMeta] = None
    model_config = ConfigDict(extra="allow")

class CounterpartyReport(BaseModel):
    """Row from counterparty report."""
    counterparty: dict
    demands_count: int = Field(default=0, alias="demandsCount")
    demands_sum: float = Field(default=0, alias="demandsSum")
    returns_count: int = Field(default=0, alias="returnsCount")
    returns_sum: float = Field(default=0, alias="returnsSum")
    balance: float = 0
    profit: float = 0
    model_config = ConfigDict(populate_by_name=True)

class StockReport(BaseModel):
    """Stock data from MoySklad."""
    assortment_id: str
    name: str
    article: Optional[str] = None
    stock: float = 0
    reserve: float = 0
    in_transit: float = Field(default=0, alias="inTransit")
    quantity: float = 0  # available = stock - reserve
    model_config = ConfigDict(populate_by_name=True)
