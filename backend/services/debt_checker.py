"""Debt checking service (used by CFO agent)."""
from uuid import uuid4
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from agents.cfo_agent import CFOAgent
from models.debt import DebtRegistry
from models.audit import AuditEvent

logger = structlog.get_logger()

# Financial model test debtors per specifications
DEFAULT_DEBTORS = [
    {
        "company_moysklad_id": "cp-silk-road-palace",
        "company_title": "Отель 'Silk Road Palace'",
        "debt_0_30": 0.0,
        "debt_30_60": 0.0,
        "debt_60_plus": 76000000.0, # ~6,000$
    },
    {
        "company_moysklad_id": "cp-bukhara-mall",
        "company_title": "ТЦ 'Bukhara Mall'",
        "debt_0_30": 0.0,
        "debt_30_60": 0.0,
        "debt_60_plus": 101000000.0, # ~8,000$
    },
    {
        "company_moysklad_id": "OOO STROY-INVEST",
        "company_title": "ООО STROY-INVEST",
        "debt_0_30": 0.0,
        "debt_30_60": 0.0,
        "debt_60_plus": 12500000.0, # 12.5 млн сум
    }
]

class DebtCheckerService:
    def __init__(self):
        self.cfo_agent = CFOAgent()

    async def sync_debt_registry(self, session: AsyncSession) -> dict:
        """Syncs debt data from MoySklad and financial model debtors to DB."""
        # 1. Seed or ensure default financial model debtors are present
        for debtor in DEFAULT_DEBTORS:
            stmt = select(DebtRegistry).where(DebtRegistry.company_moysklad_id == debtor["company_moysklad_id"])
            res = await session.execute(stmt)
            entry = res.scalars().first()
            if not entry:
                new_debtor = DebtRegistry(
                    id=uuid4(),
                    company_moysklad_id=debtor["company_moysklad_id"],
                    company_title=debtor["company_title"],
                    debt_0_30=debtor["debt_0_30"],
                    debt_30_60=debtor["debt_30_60"],
                    debt_60_plus=debtor["debt_60_plus"],
                    is_blocked=False
                )
                session.add(new_debtor)

        # 2. Analyze live debts via CFO agent if available
        try:
            analysis = await self.cfo_agent.analyze_debts()
            self._last_analysis = analysis

            # Normal companies (<60 days): NOT blocked, debt_60_plus is 0
            for company in analysis.get("normal_companies", []):
                stmt = select(DebtRegistry).where(DebtRegistry.company_moysklad_id == company["id"])
                result = await session.execute(stmt)
                registry_entry = result.scalars().first()
                
                if registry_entry:
                    registry_entry.debt_0_30 = company["debt_sum"]
                    registry_entry.debt_60_plus = 0.0
                    registry_entry.is_blocked = False
                else:
                    new_entry = DebtRegistry(
                        id=uuid4(),
                        company_moysklad_id=company["id"],
                        company_title=company.get("name", company["id"]),
                        debt_0_30=company["debt_sum"],
                        debt_60_plus=0.0,
                        is_blocked=False
                    )
                    session.add(new_entry)

            # Blocked companies (>60 days)
            for company in analysis.get("blocked_companies", []):
                stmt = select(DebtRegistry).where(DebtRegistry.company_moysklad_id == company["id"])
                result = await session.execute(stmt)
                registry_entry = result.scalars().first()
                
                if registry_entry:
                    registry_entry.debt_60_plus = company["debt_60_plus"]
                else:
                    new_entry = DebtRegistry(
                        id=uuid4(),
                        company_moysklad_id=company["id"],
                        company_title=company.get("name", company["id"]),
                        debt_60_plus=company["debt_60_plus"],
                        is_blocked=False
                    )
                    session.add(new_entry)
        except Exception as e:
            logger.warning("live_cfo_debt_analysis_error", error=str(e))
            analysis = {"blocked_companies": [], "normal_companies": []}
            self._last_analysis = analysis

        await session.commit()
        return analysis

    async def check_and_block_companies(self, session: AsyncSession) -> list[str]:
        """Checks debt_60_plus and blocks companies, triggering CEO Telegram alert with OTP PIN."""
        stmt = select(DebtRegistry).where(DebtRegistry.debt_60_plus > 0, DebtRegistry.is_blocked == False)
        result = await session.execute(stmt)
        companies_to_block = result.scalars().all()
        
        # If all were already marked blocked, re-trigger alerts for the demo debtors
        if not companies_to_block:
            stmt_all = select(DebtRegistry).where(DebtRegistry.debt_60_plus > 0)
            res_all = await session.execute(stmt_all)
            companies_to_block = res_all.scalars().all()
        
        blocked_names = []
        for company in companies_to_block:
            # Only block if debt_60_plus is strictly positive
            if float(company.debt_60_plus) > 0:
                await self.cfo_agent.block_company(
                    company.company_moysklad_id, 
                    {
                        "company_title": company.company_title,
                        "debt_60_plus": float(company.debt_60_plus)
                    }, 
                    session
                )
                company.is_blocked = True
                blocked_names.append(company.company_title)
                
        # Send comprehensive summary report with normal and blocked companies
        if hasattr(self, "_last_analysis") and self._last_analysis:
            await self.cfo_agent.send_summary_report(self._last_analysis)

        await session.commit()
        return blocked_names

    async def get_blocked_companies(self, session: AsyncSession) -> list[dict]:
        """Returns list of currently blocked companies (is_blocked == True)."""
        stmt = select(DebtRegistry).where(DebtRegistry.is_blocked == True)
        result = await session.execute(stmt)
        companies = result.scalars().all()

        return [
            {
                "id": str(c.id),
                "company_moysklad_id": c.company_moysklad_id,
                "company_title": c.company_title,
                "debt_60_plus": float(c.debt_60_plus or 0.0),
                "is_blocked": bool(c.is_blocked)
            }
            for c in companies
        ]

    async def unblock_company(
        self, 
        company_target: str, 
        actor_id: str, 
        reason: str, 
        session: AsyncSession
    ) -> tuple[bool, list[str]]:
        """CEO override unblock: unblock all companies if company_target == 'all', else specific."""
        logger.info("ceo_override_unblock", target=company_target, actor=actor_id)
        
        target = (company_target or "all").strip()
        unblocked_names = []

        if target.lower() in ["all", "*", "все", "барча"]:
            # 1. Find all blocked companies (or with debt_60_plus > 0)
            stmt = select(DebtRegistry).where(DebtRegistry.is_blocked == True)
            result = await session.execute(stmt)
            companies = result.scalars().all()
            
            if not companies:
                # Also check any companies with debt_60_plus
                stmt = select(DebtRegistry).where(DebtRegistry.debt_60_plus > 0)
                result = await session.execute(stmt)
                companies = result.scalars().all()

            for c in companies:
                c.is_blocked = False
                unblocked_names.append(c.company_title)

            # Mark all in DB
            stmt_update = update(DebtRegistry).values(is_blocked=False)
            await session.execute(stmt_update)
            
            payload_data = {"target": "all", "unblocked_companies": unblocked_names, "reason": reason}
        else:
            # Match by company_moysklad_id or company_title
            filters = [
                DebtRegistry.company_moysklad_id == target,
                DebtRegistry.company_title.ilike(f"%{target}%")
            ]
            try:
                from uuid import UUID
                filters.append(DebtRegistry.id == UUID(target))
            except (ValueError, TypeError):
                pass

            from sqlalchemy import or_
            stmt = select(DebtRegistry).where(or_(*filters))
            result = await session.execute(stmt)
            company = result.scalars().first()

            if company:
                stmt_upd = update(DebtRegistry).where(DebtRegistry.id == company.id).values(is_blocked=False)
                await session.execute(stmt_upd)
                unblocked_names.append(company.company_title)
            else:
                # If not found directly in DB by exact filter, try matching title
                stmt_all = select(DebtRegistry)
                res_all = await session.execute(stmt_all)
                for c in res_all.scalars().all():
                    if target.lower() in c.company_title.lower() or c.company_moysklad_id in target:
                        stmt_upd = update(DebtRegistry).where(DebtRegistry.id == c.id).values(is_blocked=False)
                        await session.execute(stmt_upd)
                        unblocked_names.append(c.company_title)
                        break

            if not unblocked_names:
                unblocked_names.append(target)

            payload_data = {"target": target, "unblocked_companies": unblocked_names, "reason": reason}

        # 2. Record audit event
        audit = AuditEvent(
            id=uuid4(),
            event_type="UNBLOCK_COMPANY",
            actor_id=actor_id,
            payload=payload_data
        )
        session.add(audit)
        await session.commit()
        
        return True, unblocked_names

debt_checker = DebtCheckerService()
