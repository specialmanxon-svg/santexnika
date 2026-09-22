"""Cash Flow service for MoySklad financial analytics and Diyor Group Dashboard."""
import time
import asyncio
import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime

from services.moysklad_client import MoySkladClient
from core.database import async_session_factory
from models.product import MdmProduct
from sqlalchemy import select

logger = structlog.get_logger(__name__)


class CashFlowService:
    def __init__(self):
        self.ms_client = MoySkladClient()
        self._cache = {
            "ts": 0.0,
            "key": "",
            "data": None
        }
        self._category_cache = {
            "ts": 0.0,
            "map": {}
        }

    async def _get_category_map(self) -> Dict[str, str]:
        """Load product moysklad_id -> category (folder/brand) mapping from DB and MoySklad."""
        now = time.time()
        if self._category_cache["map"] and (now - self._category_cache["ts"] < 300):
            return self._category_cache["map"]

        cat_map = {}
        try:
            async with async_session_factory() as session:
                res = await session.execute(select(MdmProduct.moysklad_id, MdmProduct.brand))
                for row in res.fetchall():
                    if row[0] and row[1]:
                        cat_map[row[0]] = row[1]
        except Exception as e:
            logger.warning("failed_to_load_category_map_from_db", error=str(e))

        self._category_cache["ts"] = now
        self._category_cache["map"] = cat_map
        return cat_map

    async def _fetch_payments_entity(self, entity: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> List[dict]:
        """Fetch payment or cash entity with date filtering and pagination."""
        params = {"limit": 1000}
        filters = []
        if date_from:
            filters.append(f"moment>={date_from} 00:00:00")
        if date_to:
            filters.append(f"moment<={date_to} 23:59:59")
        if filters:
            params["filter"] = ";".join(filters)

        try:
            data = await self.ms_client.get(f"/entity/{entity}", params=params)
            return data.get("rows", [])
        except Exception as e:
            logger.warning(f"failed_to_fetch_{entity}", error=str(e))
            return []

    async def get_cashflow_summary(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Comprehensive Cash Flow summary: today's in/out, net profit, margin %, daily flow, and category margins."""
        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")

        if not date_from:
            date_from = f"{now_dt.year}-{now_dt.month:02d}-01"
        if not date_to:
            date_to = today_str

        cache_key = f"{date_from}_{date_to}"
        now_ts = time.time()
        if not force_refresh and self._cache["data"] and self._cache["key"] == cache_key and (now_ts - self._cache["ts"] < 60):
            logger.info("returning_cached_cashflow_summary", key=cache_key)
            return self._cache["data"]

        logger.info("fetching_cashflow_summary", date_from=date_from, date_to=date_to, force_refresh=force_refresh)

        try:
            # 1. Fetch categories map
            cat_map = await self._get_category_map()

            # 2. Fetch payments (bank + cash desk) with gentle delays to prevent 429
            payment_in = await self._fetch_payments_entity("paymentin", date_from, date_to)
            await asyncio.sleep(0.08)
            cash_in = await self._fetch_payments_entity("cashin", date_from, date_to)
            await asyncio.sleep(0.08)
            payment_out = await self._fetch_payments_entity("paymentout", date_from, date_to)
            await asyncio.sleep(0.08)
            cash_out = await self._fetch_payments_entity("cashout", date_from, date_to)
            await asyncio.sleep(0.08)

            # 3. Fetch sales profitability report from MoySklad
            profit_params = {
                "momentFrom": f"{date_from} 00:00:00",
                "limit": 1000
            }
            if date_to:
                profit_params["momentTo"] = f"{date_to} 23:59:59"

            profit_rows = []
            try:
                profit_data = await self.ms_client.get("/report/profit/byproduct", params=profit_params)
                profit_rows = profit_data.get("rows", [])
            except Exception as e:
                logger.warning("failed_to_fetch_profit_byproduct", error=str(e))

            # 4. Aggregate daily flows
            daily_totals: Dict[str, Dict[str, float]] = {}

            all_incoming = payment_in + cash_in
            for p in all_incoming:
                dt_str = p.get("moment", "")[:10]
                if not dt_str:
                    continue
                amt = float(p.get("sum", 0.0)) / 100.0
                if dt_str not in daily_totals:
                    daily_totals[dt_str] = {"income": 0.0, "expense": 0.0}
                daily_totals[dt_str]["income"] += amt

            all_outgoing = payment_out + cash_out
            for p in all_outgoing:
                dt_str = p.get("moment", "")[:10]
                if not dt_str:
                    continue
                amt = float(p.get("sum", 0.0)) / 100.0
                if dt_str not in daily_totals:
                    daily_totals[dt_str] = {"income": 0.0, "expense": 0.0}
                daily_totals[dt_str]["expense"] += amt

            # Build daily_flow list sorted descending (newest first)
            daily_flow = []
            for dt_k, v in daily_totals.items():
                inc = round(v["income"], 2)
                exp = round(v["expense"], 2)
                daily_flow.append({
                    "date": dt_k,
                    "income": inc,
                    "expense": exp,
                    "balance": round(inc - exp, 2)
                })
            daily_flow.sort(key=lambda x: x["date"], reverse=True)

            # Today stats
            income_today = daily_totals.get(today_str, {}).get("income", 0.0)
            expense_today = daily_totals.get(today_str, {}).get("expense", 0.0)
            balance_today = income_today - expense_today

            # Period totals
            total_income_period = sum(d["income"] for d in daily_flow)
            total_expense_period = sum(d["expense"] for d in daily_flow)

            # 5. Aggregate category margins
            category_totals: Dict[str, Dict[str, float]] = {}
            total_revenue = 0.0
            total_cogs = 0.0
            total_profit = 0.0

            for r in profit_rows:
                assort = r.get("assortment", {})
                m_meta = assort.get("meta", {})
                m_type = m_meta.get("type", "product")
                pid = m_meta.get("href", "").split("/")[-1]

                if m_type == "service":
                    cat_name = "Хизматлар (Доставка ва монтаж)"
                else:
                    cat_name = cat_map.get(pid) or assort.get("pathName", "").split("/")[-1] or "Бошқа сантехника"

                sell = float(r.get("sellSum", 0.0) - r.get("returnSum", 0.0)) / 100.0
                cost = float(r.get("sellCostSum", 0.0) - r.get("returnCostSum", 0.0)) / 100.0
                pr = float(r.get("profit", 0.0)) / 100.0

                total_revenue += sell
                total_cogs += cost
                total_profit += pr

                if cat_name not in category_totals:
                    category_totals[cat_name] = {"sales": 0.0, "cost": 0.0, "margin": 0.0}
                category_totals[cat_name]["sales"] += sell
                category_totals[cat_name]["cost"] += cost
                category_totals[cat_name]["margin"] += pr

            # Format category_margin list
            category_margin = []
            for cname, cdata in category_totals.items():
                s = round(cdata["sales"], 2)
                c = round(cdata["cost"], 2)
                m = round(cdata["margin"], 2)
                pct = round((m / s * 100.0), 1) if s > 0 else 0.0
                category_margin.append({
                    "category": cname,
                    "sales": s,
                    "cost": c,
                    "margin": m,
                    "margin_percent": pct
                })
            category_margin.sort(key=lambda x: x["sales"], reverse=True)

            # Calculate Net Profit & Margin %
            net_profit = round(total_profit, 2)
            margin_percent = round((net_profit / total_revenue * 100.0), 1) if total_revenue > 0 else 0.0

            result = {
                "status": "success",
                "period": {
                    "date_from": date_from,
                    "date_to": date_to,
                    "today": today_str
                },
                "income_today": round(income_today, 2),
                "expense_today": round(expense_today, 2),
                "net_profit": net_profit,
                "margin_percent": margin_percent,
                "summary": {
                    "income_today": round(income_today, 2),
                    "expense_today": round(expense_today, 2),
                    "balance_today": round(balance_today, 2),
                    "total_income": round(total_income_period, 2),
                    "total_expense": round(total_expense_period, 2),
                    "revenue": round(total_revenue, 2),
                    "cost": round(total_cogs, 2),
                    "net_profit": net_profit,
                    "margin_percent": margin_percent
                },
                "daily_flow": daily_flow,
                "category_margin": category_margin
            }

            self._cache["ts"] = now_ts
            self._cache["key"] = cache_key
            self._cache["data"] = result
            return result

        except Exception as e:
            logger.error("failed_to_get_cashflow_summary", error=str(e))
            # Return last valid cache if exists
            if self._cache["data"]:
                logger.info("returning_stale_cache_on_error")
                return self._cache["data"]

            # Fallback structure
            return {
                "status": "partial_error",
                "error": str(e),
                "income_today": 0.0,
                "expense_today": 0.0,
                "net_profit": 0.0,
                "margin_percent": 0.0,
                "summary": {
                    "income_today": 0.0,
                    "expense_today": 0.0,
                    "balance_today": 0.0,
                    "total_income": 0.0,
                    "total_expense": 0.0,
                    "revenue": 0.0,
                    "cost": 0.0,
                    "net_profit": 0.0,
                    "margin_percent": 0.0
                },
                "daily_flow": [],
                "category_margin": []
            }

    async def get_daily_cashflow(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        """Backward-compatible endpoint: delegates to get_cashflow_summary."""
        summary = await self.get_cashflow_summary(date_from=date_from, date_to=date_to)
        return {
            "total_income": summary.get("summary", {}).get("total_income", 0.0),
            "total_expense": summary.get("summary", {}).get("total_expense", 0.0),
            "net_cashflow": summary.get("summary", {}).get("total_income", 0.0) - summary.get("summary", {}).get("total_expense", 0.0),
            "daily_breakdown": summary.get("daily_flow", []),
            "days": summary.get("daily_flow", [])
        }

    async def get_monthly_cashflow(self, year: int, month: int) -> Dict[str, Any]:
        """Monthly aggregated cash flow."""
        start_date = f"{year}-{month:02d}-01"
        if month == 12:
            end_date = f"{year}-12-31"
        else:
            end_date = f"{year}-{month+1:02d}-01"

        summary = await self.get_cashflow_summary(date_from=start_date, date_to=end_date)
        return {
            "year": year,
            "month": month,
            "total_income": summary.get("summary", {}).get("total_income", 0.0),
            "total_expense": summary.get("summary", {}).get("total_expense", 0.0),
            "net_cashflow": summary.get("summary", {}).get("total_income", 0.0) - summary.get("summary", {}).get("total_expense", 0.0)
        }

    async def get_margin_per_category(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
        """Backward-compatible endpoint for category margins."""
        summary = await self.get_cashflow_summary(date_from=date_from, date_to=date_to)
        s = summary.get("summary", {})
        return {
            "total_revenue": s.get("revenue", 0.0),
            "total_cost": s.get("cost", 0.0),
            "total_profit": s.get("net_profit", 0.0),
            "total_margin_percent": s.get("margin_percent", 0.0),
            "margin_percent": s.get("margin_percent", 0.0),
            "categories": summary.get("category_margin", [])
        }


cashflow_service = CashFlowService()
