"""AI CFO Agent for debt analysis."""
import structlog
from datetime import datetime, timezone
import pyotp
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from services.moysklad_client import MoySkladClient
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger()

class CFOAgent:
    """Agent that analyzes debts and blocks companies based on aging rules."""
    
    def __init__(self):
        self.moysklad = MoySkladClient()
        self.telegram = TelegramNotifier()
        
    async def analyze_debts(self) -> dict:
        """Fetch counterparty balances, calculate aging from last demand date, and divide tiyins by 100."""
        logger.info("starting_debt_analysis")
        
        results = {
            "total_debt": 0.0,
            "blocked_companies": [],
            "normal_companies": [],
            "stats": {}
        }
        
        now = datetime.utcnow()
        
        try:
            # Fetch counterparty reports with balances and dates from MoySklad
            report_data = await self.moysklad._request("GET", "/report/counterparty", params={"limit": 100})
            rows = report_data.get("rows", [])
            
            for r in rows:
                cp = r.get("counterparty", {})
                cp_id = cp.get("id")
                cp_name = cp.get("name", "Контрагент")
                raw_balance = float(r.get("balance", 0.0))
                
                # In MoySklad, negative balance means customer owes us money (accounts receivable)
                if raw_balance < 0:
                    balance_sum = round(abs(raw_balance) / 100.0, 2)
                    results["total_debt"] += balance_sum
                    
                    # Determine aging based on lastDemandDate (not counterparty creation date)
                    last_demand_str = r.get("lastDemandDate")
                    days_overdue = 0
                    last_demand_display = "Н/Д"
                    
                    if last_demand_str:
                        try:
                            clean_str = last_demand_str.split(".")[0].replace("Z", "")
                            if "T" in clean_str:
                                last_demand_dt = datetime.fromisoformat(clean_str)
                            else:
                                last_demand_dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                            days_overdue = (now - last_demand_dt).days
                            last_demand_display = last_demand_dt.strftime("%d.%m.%Y")
                        except Exception as parse_err:
                            logger.warning("last_demand_date_parse_error", error=str(parse_err), date_str=last_demand_str)
                    
                    company_info = {
                        "id": cp_id,
                        "name": cp_name,
                        "debt_sum": balance_sum,
                        "days": days_overdue,
                        "last_demand_date": last_demand_display
                    }
                    
                    # Rule: Only debts > 60 days trigger Hard-Lock
                    if days_overdue > 60:
                        company_info["debt_60_plus"] = balance_sum
                        results["blocked_companies"].append(company_info)
                    else:
                        results["normal_companies"].append(company_info)
                        
        except Exception as e:
            logger.warning("cfo_counterparty_report_failed", error=str(e))
            
        results["stats"]["total_blocked"] = len(results["blocked_companies"])
        results["stats"]["total_normal"] = len(results["normal_companies"])
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
        
    async def block_company(self, company_moysklad_id: str, debt_data: dict, session: AsyncSession = None) -> bool:
        """Blocks a company in MoySklad and Native CRM, and alerts CEO."""
        logger.info("blocking_company", company_id=company_moysklad_id, debt=debt_data)
        
        company_name = debt_data.get("company_title", company_moysklad_id)
        debt_amount = float(debt_data.get("debt_60_plus", 0.0))
        
        ms_success = True
        if len(company_moysklad_id) == 36:
            ms_success = await self.moysklad.update_counterparty_status(company_moysklad_id, "BLOCKED")
            
        crm_success = await self.crm.block_company(company_moysklad_id, is_blocked=True, debt_60=debt_amount)
        
        # Generate current TOTP PIN for CEO
        from core.security import generate_otp
        ceo_pin = generate_otp()
        
        alert_text = (
            f"🚨 <b>Hard-Lock: Автоматическая блокировка!</b>\n\n"
            f"🏢 Компания: <b>{company_name}</b>\n"
            f"🆔 ID: <code>{company_moysklad_id}</code>\n"
            f"⏳ Просроченный долг (>60 дней): <b>{debt_amount:,.2f} UZS</b>\n\n"
            f"⚠️ Отгрузки и выписка счетов заблокированы.\n"
            f"Для экстренной разблокировки руководителем (CEO Override) используйте одноразовый PIN:\n"
            f"🔑 <b>CEO PIN: <code>{ceo_pin}</code></b> (действует 3-5 мин)\n"
            f"Ввод этого PIN в дашборде снимет Hard-Lock."
        )
        send_res = await self.telegram.send_ceo_message(alert_text)
        logger.info("cfo_telegram_alert_sent", company=company_name, pin=ceo_pin, sent=send_res)
        
        return crm_success
        
    async def send_summary_report(self, analysis: dict) -> bool:
        """Sends comprehensive summary report to CEO Telegram with both normal and blocked debts."""
        from core.security import generate_otp
        ceo_pin = generate_otp()

        blocked = analysis.get("blocked_companies", [])
        normal = analysis.get("normal_companies", [])

        lines = ["📊 <b>AI CFO: Молиявий мониторинг ва қарздорлик таҳлили</b>\n"]

        if normal:
            lines.append("🟢 <b>Нормал муддатдаги қарздорлар (&lt;60 кун):</b>")
            for c in normal:
                lines.append(
                    f"• <b>{c['name']}</b>: {c['debt_sum']:,.2f} сўм\n"
                    f"  ⏳ Муддати: <b>{c['days']} кун</b> (Охирги отгрузка: {c['last_demand_date']})\n"
                    f"  ✅ Ҳолати: <b>Блок қилинмади (Норма)</b>"
                )
            lines.append("")

        if blocked:
            lines.append("🚨 <b>Муддати ўтган қарздорлар (&gt;60 кун, Hard-Lock):</b>")
            for c in blocked:
                debt_val = c.get('debt_60_plus', c.get('debt_sum', 0))
                days_val = c.get('days', '>60')
                lines.append(
                    f"• <b>{c['name']}</b>: {debt_val:,.2f} сўм\n"
                    f"  ⏳ Муддати: <b>{days_val} кун</b> ⛔ <b>Заблокирован</b>"
                )
            lines.append("")
            lines.append(
                f"🔑 <b>CEO Override PIN: <code>{ceo_pin}</code></b> (амал қилиш муддати 30 сек)\n"
                f"<i>Дашбордда киритилса Hard-Lock ечилади.</i>"
            )

        text = "\n".join(lines)
        res = await self.telegram.send_ceo_message(text)
        logger.info("cfo_summary_report_sent", success=res, pin=ceo_pin)
        return res

    async def generate_report(self, analysis: dict) -> str:
        """Generates formatted report for Telegram."""
        blocked_count = len(analysis.get("blocked_companies", []))
        report = f"📊 *Debt Analysis Report*\n\n"
        report += f"Total Blocked Companies: {blocked_count}\n"
        report += "\nDetails:\n"
        
        for comp in analysis.get("blocked_companies", []):
            report += f"- {comp['name']}: >60 days debt: {comp.get('debt_60_plus', 0)} UZS\n"
            
        return report
