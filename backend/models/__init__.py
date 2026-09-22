"""Diyor Group — Маълумотлар моделлари."""
from .base import Base
from .product import MdmProduct
from .debt import DebtRegistry
from .audit import AuditEvent
from .social import PartnerProfile
from .inventory import InventoryItem
from .hr import WorkTimesheet, Workplace
from .campaign import TelegramCampaign
from .crm import CrmLead, CrmDeal, CrmTask, CrmPipelineType, CrmStageType

# Aliases for backwards compatibility
ProductMDM = MdmProduct
AuditLog = AuditEvent

__all__ = [
    "Base",
    "MdmProduct",
    "ProductMDM",
    "DebtRegistry",
    "AuditEvent",
    "AuditLog",
    "PartnerProfile",
    "InventoryItem",
    "WorkTimesheet",
    "Workplace",
    "TelegramCampaign",
    "CrmLead",
    "CrmDeal",
    "CrmTask",
    "CrmPipelineType",
    "CrmStageType",
]
