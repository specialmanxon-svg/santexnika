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

__all__ = [
    "Base",
    "MdmProduct",
    "TechnicalEmbedding",
    "CompatibilityRule",
    "DebtRegistry",
    "AuditEvent",
    "PartnerProfile",
    "SocialStoryLead",
    "PlumberProfile",
    "InventoryItem",
    "WorkTimesheet",
    "TelegramCampaign",
]
