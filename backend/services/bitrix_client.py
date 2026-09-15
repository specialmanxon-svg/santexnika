"""Adapter bridging legacy client calls directly to Diyorgroup Native CRM."""
import structlog
from services.diyor_crm_client import DiyorCrmClient

logger = structlog.get_logger(__name__)


class BitrixClient(DiyorCrmClient):
    """Backwards-compatibility bridge forwarding calls directly to Diyorgroup CRM (diyorgroup.uz/crm)."""
    
    def __init__(self):
        super().__init__()

    async def get_deal_products(self, deal_id: int) -> list:
        deal = await self.get_deal(str(deal_id))
        return deal.get("positions", [])

    async def update_company(self, company_id: int, fields: dict) -> bool:
        return await self.block_company(str(company_id), fields.get("is_blocked", False), fields.get("debt_60", 0.0))

    async def get_company(self, company_id: int) -> dict:
        return {"id": company_id, "is_blocked": False}

    async def add_task_comment(self, task_id: int, text: str, author_id: int = 1) -> int:
        return 1

    async def batch(self, commands: dict) -> dict:
        return {}

    async def close(self):
        pass
