"""Cash Flow service for MoySklad financial analytics and Diyor Group Dashboard."""
import time
import asyncio
import re
import urllib.parse
import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime

from services.moysklad_client import MoySkladClient
from core.database import async_session_factory
from models.product import MdmProduct
from sqlalchemy import select

logger = structlog.get_logger(__name__)


EXPENSE_CATEGORY_NAMES = {
    "Аренда": "Ижара (Аренда)",
    "Зарплата": "Иш ҳақи (Зарплата)",
    "Логистика": "Транспорт ва логистика",
    "Закупка товаров": "Товарлар хариди (Закупка)",
    "Налоги и сборы": "Солиқлар ва йиғимлар",
    "Коммунальные": "Коммунал тўловлар",
    "Маркетинг и реклама": "Маркетинг ва реклама",
    "Ремонт": "Таъмирлаш харажатлари (Ремонт)",
    "Продукты питания": "Озиқ-овқат харажатлари",
    "Перемещение": "Пул ўтказмалари (Перемещение)",
    "Выплата тела кредита": "Кредит асосий қарзи тўлови",
    "Вывод прибыли": "Фойда олиш (Дивиденд)",
    "Проценты по кредиту": "Кредит фоизлари тўлови",
    "Возврат": "Қайтарилган маблағлар (Возврат)",
    "Прочие": "Бошқа харажатлар",
    "Списания": "Ҳисобдан чиқариш (Списание)",
    "Покупка основных средств": "Асосий воситалар хариди"
}

COMMON_BRAND_KEYWORDS = [
    ("GROHE", ["GROHE", "BAUEDGE", "BAUCLASSIC", "ESSENCE", "TEMPESTA", "ESSENTIALS", "BAULOOP", "EUROSMART", "RAPID SL", "SOLIDO"]),
    ("VALTEC", ["VALTEC"]),
    ("JAQUAR", ["JAQUAR"]),
    ("ESSCO", ["ESSCO"]),
    ("KALDE", ["KALDE"]),
    ("KNAUF", ["KNAUF", "КНАУФ"]),
    ("BNBM", ["BNBM", "SINOGIPS"]),
    ("VENTUM", ["VENTUM", "ВЕНТУМ"]),
    ("EAST COLOR", ["EAST COLOR"]),
    ("DEMIR", ["DEMIR", "ДЕМИР"]),
    ("VIKO", ["VIKO", "ВИКО"]),
    ("GAPPO & FRAP", ["GAPPO", "FRAP"]),
    ("DCO", ["DCO"]),
    ("DIYOR", ["DIYOR"]),
    ("Artize", ["ARTIZE"])
]

def normalize_brand(raw_name: str) -> str:
    if not raw_name or not str(raw_name).strip():
        return "Бошқа брендлар"
    b = str(raw_name).strip()
    bu = b.upper()
    if "GROHE" in bu:
        return "GROHE"
    if "JAQUAR" in bu:
        return "JAQUAR"
    if "VALTEC" in bu:
        return "VALTEC"
    if "KALDE" in bu:
        return "KALDE"
    if "КНАУФ" in bu or "KNAUF" in bu:
        return "KNAUF"
    if "ESSCO" in bu:
        return "ESSCO"
    if "ARTIZE" in bu:
        return "Artize"
    if "GAPPO" in bu or "FRAP" in bu:
        return "GAPPO & FRAP"
    if "DCO" in bu:
        return "DCO"
    if "DIYOR" in bu:
        return "DIYOR"
    if "RAGLO" in bu or "SPLENKA" in bu:
        return "RAGLO & SPLENKA"
    if "BNBM" in bu:
        return "BNBM"
    if "VENTUM" in bu or "ВЕНТУМ" in bu:
        return "VENTUM"
    if "EAST COLOR" in bu:
        return "EAST COLOR"
    if "DEMIR" in bu or "ДЕМИР" in bu:
        return "DEMIR"
    if "VIKO" in bu or "ВИКО" in bu:
        return "VIKO"
    if "ХОЗ" in bu or "ХОЗЯЙСТВ" in bu:
        return "Хўжалик моллари (Хозтовары)"
    if "ХИЗМАТ" in bu or "ДОСТАВКА" in bu or "УСЛУГ" in bu:
        return "Хизматлар (Доставка ва монтаж)"
    if "ДУСЕЛ" in bu or "DUSEL" in bu:
        return "DUSEL"
    if "DAIKIN" in bu:
        return "DAIKIN"
    if "EGGAR" in bu or "EGGER" in bu:
        return "EGGER"
    if "EMERICH" in bu:
        return "EMERICH"
    if "ТЕКБОНД" in bu or "TEKBOND" in bu:
        return "TEKBOND"
    if "E.C.A" in bu:
        return "E.C.A."
    if "VERO" in bu:
        return "VERO"
    if "MILANO" in bu:
        return "MILANO"
    if "VIEANY" in bu:
        return "VIEANY"
    if "АСПЕКТ" in bu:
        return "АСПЕКТ"
    if "КЕРОМАГРАНИТ" in bu:
        return "КЕРОМАГРАНИТ"
    return b


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
        self._product_brand_cache: Dict[str, str] = {}
        self._expense_items_cache = {
            "ts": 0.0,
            "map": {}
        }
        self._expenses_cache = {
            "ts": 0.0,
            "key": "",
            "data": None
        }
        self._brand_margin_cache = {
            "ts": 0.0,
            "key": "",
            "data": None
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

    async def _get_expense_items_map(self) -> Dict[str, str]:
        """Fetch and cache all expense items from MoySklad API."""
        now = time.time()
        if self._expense_items_cache["map"] and (now - self._expense_items_cache["ts"] < 600):
            return self._expense_items_cache["map"]

        exp_map = {}
        try:
            items_data = await self.ms_client.get("/entity/expenseitem")
            for row in items_data.get("rows", []):
                item_id = row.get("id")
                item_name = row.get("name")
                if item_id and item_name:
                    exp_map[item_id] = item_name
        except Exception as e:
            logger.warning("failed_to_fetch_expense_items_map", error=str(e))

        if exp_map:
            self._expense_items_cache["ts"] = now
            self._expense_items_cache["map"] = exp_map
        return exp_map or self._expense_items_cache.get("map", {})

    async def _resolve_product_brands(self, products_info: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Takes a list of {'id': pid, 'name': pname, 'type': ptype, 'pathName': path}
        Returns mapping pid -> brand name
        """
        result = {}
        missing_pids = []

        for p in products_info:
            pid = p.get("id")
            if not pid:
                continue
            ptype = p.get("type", "product")
            pname = p.get("name", "")

            if ptype == "service" or "хизмат" in pname.lower() or "доставка" in pname.lower() or "монтаж" in pname.lower():
                result[pid] = "Хизматлар (Доставка ва монтаж)"
                continue

            if pid in self._product_brand_cache:
                result[pid] = self._product_brand_cache[pid]
                continue

            # Check product name directly against known brand keywords first (avoids remote HTTP calls)
            name_upper = pname.upper()
            found_brand = None
            for brand_label, keywords in COMMON_BRAND_KEYWORDS:
                for kw in keywords:
                    if re.search(r'\b' + re.escape(kw) + r'\b', name_upper):
                        found_brand = brand_label
                        break
                if found_brand:
                    break

            if found_brand:
                norm = normalize_brand(found_brand)
                self._product_brand_cache[pid] = norm
                result[pid] = norm
                continue

            missing_pids.append(pid)

        # Batch fetch missing product attributes from MoySklad (capped to prevent timeouts)
        if missing_pids:
            chunk_size = 50
            chunks = [missing_pids[i:i + chunk_size] for i in range(0, min(len(missing_pids), 100), chunk_size)]
            tasks = [
                self.ms_client.get("/entity/product", params={"filter": ";".join([f"id={x}" for x in c if x])})
                for c in chunks if c
            ]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in batch_results:
                if not isinstance(res, dict):
                    continue
                for prod in res.get("rows", []):
                    prod_id = prod.get("id")
                    detected = None

                    # 1. Check custom attribute 'Бренд товара' or 'Бренд'
                    for a in prod.get("attributes", []):
                        if "бренд" in a.get("name", "").lower():
                            val = a.get("value")
                            if isinstance(val, dict):
                                detected = val.get("name")
                            elif val:
                                detected = str(val)
                            if detected:
                                break

                    # 2. Check pathName / productFolder
                    if not detected:
                        path = prod.get("pathName", "")
                        if path:
                            parts = [x.strip() for x in path.split("/") if x.strip()]
                            for part in parts:
                                if part.lower() not in ["сантехника", "строй материаль", "остаток товар база"]:
                                    detected = part
                                    break

                    # 3. Check product name against known brand keywords
                    if not detected:
                        name_upper = prod.get("name", "").upper()
                        for brand_label, keywords in COMMON_BRAND_KEYWORDS:
                            for kw in keywords:
                                if re.search(r'\b' + re.escape(kw) + r'\b', name_upper):
                                    detected = brand_label
                                    break
                            if detected:
                                break

                    final_brand = normalize_brand(detected or "Бошқа брендлар")
                    self._product_brand_cache[prod_id] = final_brand
                    result[prod_id] = final_brand

        # Any still unassigned
        for p in products_info:
            pid = p.get("id")
            if not pid:
                continue
            if pid not in result:
                name_upper = p.get("name", "").upper()
                detected = None
                for brand_label, keywords in COMMON_BRAND_KEYWORDS:
                    for kw in keywords:
                        if re.search(r'\b' + re.escape(kw) + r'\b', name_upper):
                            detected = brand_label
                            break
                    if detected:
                        break
                final_brand = normalize_brand(detected or "Бошқа брендлар")
                self._product_brand_cache[pid] = final_brand
                result[pid] = final_brand

        return result

    async def _fetch_profit_report(self, date_from: str, date_to: str) -> List[dict]:
        """Fetch byproduct profit report from MoySklad."""
        profit_params = {
            "momentFrom": f"{date_from} 00:00:00",
            "limit": 1000
        }
        if date_to:
            profit_params["momentTo"] = f"{date_to} 23:59:59"
        try:
            profit_data = await self.ms_client.get("/report/profit/byproduct", params=profit_params)
            return profit_data.get("rows", [])
        except Exception as e:
            logger.warning("failed_to_fetch_profit_byproduct", error=str(e))
            return []

    async def _fetch_payments_entity(self, entity: str, date_from: Optional[str] = None, date_to: Optional[str] = None) -> List[dict]:
        """Fetch payment or cash entity with date filtering and pagination."""
        params = {"limit": 1000}
        filters = []
        if date_from:
            clean_date_from = str(date_from).strip().split(" ")[0].split("T")[0]
            if clean_date_from:
                filters.append(f"moment>={clean_date_from} 00:00:00")
        if date_to:
            clean_date_to = str(date_to).strip().split(" ")[0].split("T")[0]
            if clean_date_to:
                filters.append(f"moment<={clean_date_to} 23:59:59")
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
        """Comprehensive Cash Flow summary: today's in/out, net profit, margin %, daily flow, and brand margins."""
        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")

        if date_from:
            date_from = str(date_from).strip().split(" ")[0].split("T")[0]
        else:
            date_from = f"{now_dt.year}-{now_dt.month:02d}-01"

        if date_to:
            date_to = str(date_to).strip().split(" ")[0].split("T")[0]
        else:
            date_to = today_str

        cache_key = f"{date_from}_{date_to}"
        now_ts = time.time()
        # 5-minute cache (300 seconds)
        if not force_refresh and self._cache["data"] and self._cache["key"] == cache_key and (now_ts - self._cache["ts"] < 300):
            logger.info("returning_cached_cashflow_summary", key=cache_key)
            return self._cache["data"]

        logger.info("fetching_cashflow_summary", date_from=date_from, date_to=date_to, force_refresh=force_refresh)

        try:
            # 1. Fetch categories map, 4 payment entities, AND profit report all concurrently in parallel!
            cat_map_task = self._get_category_map()
            p_in_task = self._fetch_payments_entity("paymentin", date_from, date_to)
            c_in_task = self._fetch_payments_entity("cashin", date_from, date_to)
            p_out_task = self._fetch_payments_entity("paymentout", date_from, date_to)
            c_out_task = self._fetch_payments_entity("cashout", date_from, date_to)
            profit_task = self._fetch_profit_report(date_from, date_to)

            gathered_results = await asyncio.gather(
                cat_map_task,
                p_in_task,
                c_in_task,
                p_out_task,
                c_out_task,
                profit_task,
                return_exceptions=True
            )

            cat_map = gathered_results[0] if isinstance(gathered_results[0], dict) else {}
            payment_in = gathered_results[1] if isinstance(gathered_results[1], list) else []
            cash_in = gathered_results[2] if isinstance(gathered_results[2], list) else []
            payment_out = gathered_results[3] if isinstance(gathered_results[3], list) else []
            cash_out = gathered_results[4] if isinstance(gathered_results[4], list) else []
            profit_rows = gathered_results[5] if isinstance(gathered_results[5], list) else []

            # 3. Aggregate daily flows
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

            # 4. Aggregate brand margins (Брендлар бўйича маржа ва фойда таҳлили)
            products_info = []
            for r in profit_rows:
                assort = r.get("assortment", {})
                m_meta = assort.get("meta", {})
                pid = m_meta.get("href", "").split("/")[-1] if m_meta.get("href") else ""
                products_info.append({
                    "id": pid,
                    "name": assort.get("name", ""),
                    "type": m_meta.get("type", "product"),
                    "pathName": assort.get("pathName", "")
                })

            brand_map = await self._resolve_product_brands(products_info)

            brand_totals: Dict[str, Dict[str, float]] = {}
            total_revenue = 0.0
            total_cogs = 0.0
            total_profit = 0.0

            for r in profit_rows:
                assort = r.get("assortment", {})
                m_meta = assort.get("meta", {})
                pid = m_meta.get("href", "").split("/")[-1] if m_meta.get("href") else ""

                b_name = brand_map.get(pid, "Бошқа брендлар")

                sell = float(r.get("sellSum", 0.0) - r.get("returnSum", 0.0)) / 100.0
                cost = float(r.get("sellCostSum", 0.0) - r.get("returnCostSum", 0.0)) / 100.0
                pr = float(r.get("profit", 0.0)) / 100.0

                total_revenue += sell
                total_cogs += cost
                total_profit += pr

                if b_name not in brand_totals:
                    brand_totals[b_name] = {"sales": 0.0, "cost": 0.0, "margin": 0.0}
                brand_totals[b_name]["sales"] += sell
                brand_totals[b_name]["cost"] += cost
                brand_totals[b_name]["margin"] += pr

            # Format brand_margin list
            brand_margin = []
            for bname, bdata in brand_totals.items():
                s = round(bdata["sales"], 2)
                c = round(bdata["cost"], 2)
                m = round(bdata["margin"], 2)
                pct = round((m / s * 100.0), 2) if s > 0 else 0.0
                encoded_bname = urllib.parse.quote(str(bname))
                is_other = bname in ["Бошқа брендлар", "Boshqa brendlar"]
                moysklad_url = "https://online.moysklad.ru/app/#profit" if is_other else f"https://online.moysklad.ru/app/#good?search={encoded_bname}"
                brand_margin.append({
                    "brand": bname,
                    "category": bname,
                    "sales": s,
                    "cost": c,
                    "margin": m,
                    "margin_percent": pct,
                    "moysklad_url": moysklad_url,
                    "report_url": "https://online.moysklad.ru/app/#profit"
                })
            brand_margin.sort(key=lambda x: x["sales"], reverse=True)
            category_margin = brand_margin

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
                "category_margin": category_margin,
                "brand_margin": brand_margin
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
                "category_margin": [],
                "brand_margin": []
            }

    async def get_expenses_by_category(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Group outgoing payments (paymentout and cashout) by expenseItem (Статья расходов).
        Returns list of categories formatted as:
        [
            { "category": "Ижара (Аренда)", "expense_item": "Ижара (Аренда)", "amount": 15000000.0, "percentage": 30.0 }, ...
        ]
        """
        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")

        if date_from:
            date_from = str(date_from).strip().split(" ")[0].split("T")[0]
        else:
            date_from = f"{now_dt.year}-{now_dt.month:02d}-01"

        if date_to:
            date_to = str(date_to).strip().split(" ")[0].split("T")[0]
        else:
            date_to = today_str

        cache_key = f"{date_from}_{date_to}"
        now_ts = time.time()
        # 5-minute cache (300 seconds)
        if not force_refresh and self._expenses_cache["data"] and self._expenses_cache["key"] == cache_key and (now_ts - self._expenses_cache["ts"] < 300):
            return self._expenses_cache["data"]

        logger.info("fetching_expenses_by_category", date_from=date_from, date_to=date_to, force_refresh=force_refresh)

        try:
            exp_map_task = self._get_expense_items_map()
            p_out_task = self._fetch_payments_entity("paymentout", date_from, date_to)
            c_out_task = self._fetch_payments_entity("cashout", date_from, date_to)

            gathered = await asyncio.gather(exp_map_task, p_out_task, c_out_task, return_exceptions=True)
            exp_map = gathered[0] if isinstance(gathered[0], dict) else {}
            payment_out = gathered[1] if isinstance(gathered[1], list) else []
            cash_out = gathered[2] if isinstance(gathered[2], list) else []

            category_sums: Dict[str, float] = {}
            category_ids: Dict[str, str] = {}
            total_expenses = 0.0

            for p in payment_out + cash_out:
                amt = float(p.get("sum", 0.0)) / 100.0
                if amt <= 0:
                    continue

                exp_obj = p.get("expenseItem")
                name = None
                exp_id = None
                if isinstance(exp_obj, dict):
                    name = exp_obj.get("name")
                    href = exp_obj.get("meta", {}).get("href", "")
                    if href:
                        exp_id = href.split("/")[-1]
                    if not name and exp_id:
                        name = exp_map.get(exp_id)

                if not name or not str(name).strip():
                    name = "Бошқа харажатлар" if exp_obj else "Кўрсатилмаган харажат"

                clean_name = EXPENSE_CATEGORY_NAMES.get(name, name)
                category_sums[clean_name] = category_sums.get(clean_name, 0.0) + amt
                if exp_id and clean_name not in category_ids:
                    category_ids[clean_name] = exp_id
                total_expenses += amt

            name_to_id = {v.lower().strip(): k for k, v in exp_map.items()}
            clean_to_orig = {clean: orig for orig, clean in EXPENSE_CATEGORY_NAMES.items()}

            result = []
            for cat, amt in sorted(category_sums.items(), key=lambda x: x[1], reverse=True):
                pct = round((amt / total_expenses * 100.0), 1) if total_expenses > 0 else 0.0
                orig_name = clean_to_orig.get(cat, cat)
                ms_id = category_ids.get(cat) or name_to_id.get(orig_name.lower().strip()) or name_to_id.get(cat.lower().strip())

                if ms_id:
                    moysklad_url = f"https://online.moysklad.ru/app/#expenseitem/edit?id={ms_id}"
                else:
                    moysklad_url = "https://online.moysklad.ru/app/#expenseitem"

                result.append({
                    "category": cat,
                    "expense_item": cat,
                    "amount": round(amt, 2),
                    "percentage": pct,
                    "moysklad_id": ms_id,
                    "moysklad_name": orig_name,
                    "moysklad_url": moysklad_url,
                    "moysklad_payments_url": "https://online.moysklad.ru/app/#paymentout"
                })

            self._expenses_cache["ts"] = now_ts
            self._expenses_cache["key"] = cache_key
            self._expenses_cache["data"] = result
            return result

        except Exception as e:
            logger.error("failed_to_get_expenses_by_category", error=str(e))
            if self._expenses_cache["data"]:
                return self._expenses_cache["data"]
            return []

    async def get_brand_margin(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Brand margin breakdown from MoySklad profit report:
        [
            { "brand": "GROHE", "sales": 183362166.0, "cost": 122383984.0, "margin": 60978182.0, "margin_percent": 33.26 }, ...
        ]
        """
        summary = await self.get_cashflow_summary(date_from=date_from, date_to=date_to, force_refresh=force_refresh)
        return summary.get("brand_margin", [])

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
