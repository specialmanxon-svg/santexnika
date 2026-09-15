from .base import Base
from .product import MdmProduct
from .embedding import TechnicalEmbedding
from .compatibility import CompatibilityRule
from .debt import DebtRegistry
from .audit import AuditEvent
from .social import PartnerProfile, SocialStoryLead, PlumberProfile
from .inventory import InventoryItem
from .hr import WorkTimesheet
from .campaign import TelegramCampaign
from .crm import CrmLead, CrmDeal, CrmTask, CrmPipelineType, CrmStageType

# Aliases for backwards compatibility
ProductMDM = MdmProduct
AuditLog = AuditEvent

__all__ = [
    "Base",
    "MdmProduct",
    "ProductMDM",
    "TechnicalEmbedding",
    "CompatibilityRule",
    "DebtRegistry",
    "AuditEvent",
    "AuditLog",
    "PartnerProfile",
    "SocialStoryLead",
    "PlumberProfile",
    "InventoryItem",
    "WorkTimesheet",
    "TelegramCampaign",
    "CrmLead",
    "CrmDeal",
    "CrmTask",
    "CrmPipelineType",
    "CrmStageType",
]
