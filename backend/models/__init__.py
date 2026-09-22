"""Diyor Group — Маълумотлар моделлари."""
from .base import Base
from .product import MdmProduct
from .debt import DebtRegistry
from .audit import AuditEvent
from .social import PartnerProfile
from .inventory import InventoryItem
from .hr import WorkTimesheet, Workplace, AuthorizedEmployee
from .campaign import TelegramCampaign
from .crm import CrmLead, CrmDeal, CrmTask, CrmPipelineType, CrmStageType
from .task import Task

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
    "AuthorizedEmployee",
    "TelegramCampaign",
    "CrmLead",
    "CrmDeal",
    "CrmTask",
    "CrmPipelineType",
    "CrmStageType",
    "Task",
]
