"""Compatibility checking service wrapping AI agent."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agents.sales_agent import SalesAgent
from schemas.compatibility import CompatibilityCheckResponse

logger = structlog.get_logger()

class CompatibilityCheckerService:
    def __init__(self):
        self.sales_agent = SalesAgent()
        
    async def check_compatibility(
        self, 
        primary_sku: str, 
        target_sku: str, 
        session: AsyncSession
    ) -> CompatibilityCheckResponse:
        """Wrapper method that delegates to AI Sales Agent."""
        logger.info("service_checking_compatibility", primary=primary_sku, target=target_sku)
        return await self.sales_agent.check_compatibility(primary_sku, target_sku, session)

compatibility_checker = CompatibilityCheckerService()
