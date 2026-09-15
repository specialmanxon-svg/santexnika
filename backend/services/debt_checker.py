"""Debt checking service (used by CFO agent)."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from agents.cfo_agent import CFOAgent
from models.debt import DebtRegistry
from models.audit import AuditEvent

logger = structlog.get_logger()

class DebtCheckerService:
    def __init__(self):
        self.cfo_agent = CFOAgent()

    async def sync_debt_registry(self, session: AsyncSession) -> dict:
        """Syncs debt data from MoySklad to PostgreSQL."""
        analysis = await self.cfo_agent.analyze_debts()
        
        for company in analysis.get("blocked_companies", []):
            stmt = select(DebtRegistry).where(DebtRegistry.company_id == company["id"])
            result = await session.execute(stmt)
            registry_entry = result.scalars().first()
            
            if registry_entry:
                registry_entry.debt_60_plus = company["debt_60_plus"]
                registry_entry.is_blocked = True
            else:
                new_entry = DebtRegistry(
                    company_id=company["id"],
                    debt_60_plus=company["debt_60_plus"],
                    is_blocked=True
                )
                session.add(new_entry)
                
        await session.commit()
        return analysis

    async def check_and_block_companies(self, session: AsyncSession) -> list[str]:
        """Checks debt_60_plus and blocks companies."""
        stmt = select(DebtRegistry).where(DebtRegistry.debt_60_plus > 0, DebtRegistry.is_blocked == False)
        result = await session.execute(stmt)
        companies_to_block = result.scalars().all()
        
        blocked_ids = []
        for company in companies_to_block:
            success = await self.cfo_agent.block_company(company.company_id, {"debt_60_plus": company.debt_60_plus}, session)
            if success:
                company.is_blocked = True
                blocked_ids.append(company.company_id)
                
        await session.commit()
        return blocked_ids

    async def unblock_company(
        self, 
        company_moysklad_id: str, 
        actor_id: str, 
        reason: str, 
        session: AsyncSession
    ) -> bool:
        """CEO override unblock."""
        logger.info("ceo_override_unblock", company=company_moysklad_id, actor=actor_id)
        
        # 1. Update DB
        stmt = update(DebtRegistry).where(DebtRegistry.company_id == company_moysklad_id).values(is_blocked=False)
        await session.execute(stmt)
        
        # 2. Record audit
        audit = AuditEvent(
            action="UNBLOCK_COMPANY",
            actor_id=actor_id,
            target_id=company_moysklad_id,
            reason=reason
        )
        session.add(audit)
        await session.commit()
        
        # 3. In real app, call Moysklad to remove blocked status
        # ...
        
        return True

debt_checker = DebtCheckerService()
