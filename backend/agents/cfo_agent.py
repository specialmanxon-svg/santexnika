"""AI CFO Agent for debt analysis."""
import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from services.moysklad_client import MoySkladClient
from services.bitrix_client import BitrixClient
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger()

class CFOAgent:
    """Agent that analyzes debts and blocks companies based on aging rules."""
    
    def __init__(self):
        self.moysklad = MoySkladClient()
        self.bitrix = BitrixClient()
        self.telegram = TelegramNotifier()
        
    async def analyze_debts(self) -> dict:
        """Fetch demands, calculate aging, and block non-paying counterparties."""
        logger.info("starting_debt_analysis")
        
        counterparties = await self.moysklad.get_counterparties()
        demands = await self.moysklad.get_demands()
        
        results = {
            "total_debt": 0.0,
            "blocked_companies": [],
            "stats": {}
        }
        
        for counterparty in counterparties:
            cp_demands = [d for d in demands if d.get("counterparty_id") == counterparty["id"]]
            aging = await self._calculate_aging_buckets(cp_demands)
            
            # Simple rule: if >60 days debt > 0, block
            if aging.get("over_60_days", 0) > 0:
                results["blocked_companies"].append({
                    "id": counterparty["id"],
                    "name": counterparty["name"],
                    "debt_60_plus": aging["over_60_days"]
                })
                
        results["stats"]["total_blocked"] = len(results["blocked_companies"])
        
        logger.info("debt_analysis_completed", stats=results["stats"])
        return results
        
    async def _calculate_aging_buckets(self, demands: list[dict]) -> dict:
        """Group unpaid demands by age."""
        now = datetime.now(timezone.utc)
        buckets = {
            "0_30_days": 0.0,
            "31_60_days": 0.0,
            "over_60_days": 0.0
        }
        for demand in demands:
            if demand.get("is_paid", False):
                continue
            
            created_at_str = demand.get("created_at")
            if not created_at_str:
                continue
                
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                days_old = (now - created_at).days
                
                amount = float(demand.get("amount", 0.0))
                
                if days_old <= 30:
                    buckets["0_30_days"] += amount
                elif days_old <= 60:
                    buckets["31_60_days"] += amount
                else:
                    buckets["over_60_days"] += amount
            except Exception as e:
                logger.error("error_calculating_aging", error=str(e), demand=demand)
                
        return buckets
        
    async def block_company(self, company_moysklad_id: str, debt_data: dict, session: AsyncSession) -> bool:
        """Blocks a company in MoySklad and Bitrix."""
        logger.info("blocking_company", company_id=company_moysklad_id, debt=debt_data)
        
        ms_success = await self.moysklad.update_counterparty_status(company_moysklad_id, "BLOCKED")
        bx_success = await self.bitrix.update_company_status(company_moysklad_id, "BLOCKED")
        
        return ms_success and bx_success
        
    async def generate_report(self, analysis: dict) -> str:
        """Generates formatted report for Telegram."""
        blocked_count = len(analysis.get("blocked_companies", []))
        report = f"💰 *Debt Analysis Report*\n\n"
        report += f"Total Blocked Companies: {blocked_count}\n"
        report += "\nDetails:\n"
        
        for comp in analysis.get("blocked_companies", []):
            report += f"- {comp['name']}: >60 days debt: {comp['debt_60_plus']} UZS\n"
            
        return report
