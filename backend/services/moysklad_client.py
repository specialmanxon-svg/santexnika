import asyncio
import base64
import time
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import settings

logger = structlog.get_logger(__name__)

_CP_CACHE = {"ts": 0.0, "rows": []}
_CP_DETAILS_CACHE = {"ts": 0.0, "map": {}}
_DEMAND_SELLER_CACHE = {"ts": 0.0, "map": {}}

class MoySkladClient:
    """Async client for MoySklad JSON API 1.2 with support for Token and Basic Auth."""

    def __init__(self):
        headers = {
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json"
        }
        
        # Determine authentication method: Token (Bearer) or Login/Password (Basic)
        if settings.moysklad_token and settings.moysklad_token.strip():
            headers["Authorization"] = f"Bearer {settings.moysklad_token.strip()}"
            self.auth_type = "Bearer"
        elif settings.moysklad_login and settings.moysklad_password:
            credentials = f"{settings.moysklad_login.strip()}:{settings.moysklad_password.strip()}"
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {encoded}"
            self.auth_type = "Basic"
        else:
            self.auth_type = "None"
            
        timeout_config = httpx.Timeout(15.0, connect=10.0)
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout_config,
            base_url=settings.moysklad_api_url.rstrip("/")
        )
        self.semaphore = asyncio.Semaphore(5)

    async def close(self):
        """Close underlying httpx client."""
        await self.client.aclose()

    async def is_configured(self) -> bool:
        """Returns True if any valid auth method is configured."""
        return self.auth_type != "None"

    async def test_connection(self) -> dict:
        """Verifies connection with MoySklad and returns status and account info."""
        if not await self.is_configured():
            return {
                "success": False,
                "auth_type": "None",
                "error": "МойСклад логин/пароль ёки токени .env файлига киритилмаган."
            }

        try:
            # Check company settings or organizations endpoint
            org_data = await self._request("GET", "/entity/organization", params={"limit": 1})
            rows = org_data.get("rows", [])
            org_name = rows[0].get("name", "MoySklad Account") if rows else "MoySklad Account"
            return {
                "success": True,
                "auth_type": self.auth_type,
                "organization_name": org_name,
                "api_url": settings.moysklad_api_url
            }
        except httpx.HTTPStatusError as e:
            logger.warning("moysklad_auth_failed", status=e.response.status_code)
            return {
                "success": False,
                "auth_type": self.auth_type,
                "status_code": e.response.status_code,
                "error": f"MoySklad API хатоси ({e.response.status_code}): Аутентификация муваффақиятсиз бўлди. Токен ёки логин/паролни текширинг."
            }
        except Exception as e:
            logger.error("moysklad_connection_error", error=str(e))
            return {
                "success": False,
                "auth_type": self.auth_type,
                "error": f"MoySklad билан боғланишда хатолик: {str(e)}"
            }

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type((httpx.RequestError,))
    )
    async def _request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None) -> dict:
        async with self.semaphore:
            logger.info("moysklad_request", method=method, endpoint=endpoint, params=params)
            for attempt in range(3):
                response = await self.client.request(
                    method=method,
                    url=endpoint,
                    params=params,
                    json=json_data
                )
                if response.status_code == 429 and attempt < 2:
                    logger.warning("moysklad_rate_limited_429", attempt=attempt, wait_sec=1.5)
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    error_body = response.text
                    logger.error(f"MoySklad API Error: {response.status_code} - {error_body}")
                    raise Exception(f"{response.status_code} - {error_body}") from exc

                if response.content:
                    return response.json()
                return {}
            return {}

    async def get(self, endpoint: str, params: dict = None) -> dict:
        """Convenience method for GET requests."""
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return await self._request("GET", endpoint, params=params)
            
    async def _paginate(self, method: str, endpoint: str, params: dict = None) -> list[dict]:
        if params is None:
            params = {}
        params['limit'] = 1000
        params['offset'] = 0
        all_rows = []
        
        while True:
            data = await self._request(method, endpoint, params)
            rows = data.get("rows", [])
            all_rows.extend(rows)
            
            if len(rows) < params['limit']:
                break
            params['offset'] += params['limit']
            
        return all_rows

    async def create_customer_order(self, order_data: dict) -> dict:
        return await self._request("POST", "/entity/customerorder", json_data=order_data)

    async def get_customer_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/entity/customerorder/{order_id}")

    async def get_products(self, limit: int = 1000, offset: int = 0, search: str = None) -> dict:
        params = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return await self._request("GET", "/entity/product", params=params)

    async def get_assortment(self, limit: int = 1000, offset: int = 0) -> dict:
        """Returns assortment (products, variants) with prices and stock."""
        params = {"limit": limit, "offset": offset}
        return await self._request("GET", "/entity/assortment", params=params)

    async def get_product_by_article(self, article: str) -> dict | None:
        params = {"filter": f"article={article}"}
        data = await self._request("GET", "/entity/product", params=params)
        rows = data.get("rows", [])
        return rows[0] if rows else None

    async def get_stock_all(self) -> list[dict]:
        """Returns stock report from MoySklad."""
        return await self._paginate("GET", "/report/stock/all")

    async def get_stock_by_store(self) -> list[dict]:
        """Returns stock report grouped by warehouse/store."""
        return await self._paginate("GET", "/report/stock/bystore")

    async def get_counterparty_report(self) -> list[dict]:
        return await self._paginate("GET", "/report/counterparty")

    async def get_counterparties(self) -> list[dict]:
        return await self._paginate("GET", "/entity/counterparty")

    async def get_product_folders(self) -> dict:
        """Returns product folders organized into root categories and subfolders."""
        raw_folders = await self._paginate("GET", "/entity/productfolder")
        
        folder_dict = {}
        for f in raw_folders:
            fid = f.get("id")
            name = f.get("name", "")
            path = f.get("pathName", "")
            parent_id = f.get("productFolder", {}).get("meta", {}).get("href", "").split("/")[-1] or None
            folder_dict[fid] = {
                "id": fid,
                "name": name,
                "path_name": path,
                "parent_id": parent_id,
                "href": f.get("meta", {}).get("href"),
                "subfolders": []
            }
            
        root_folders = []
        for fid, f in folder_dict.items():
            pid = f["parent_id"]
            if pid and pid in folder_dict:
                folder_dict[pid]["subfolders"].append(f)
            else:
                root_folders.append(f)
                
        root_folders.sort(key=lambda x: x["name"])
        for r in root_folders:
            r["subfolders"].sort(key=lambda x: x["name"])
            
        return {
            "root_folders": root_folders,
            "all_folders": list(folder_dict.values())
        }

    async def get_currency_rates(self) -> dict:
        """Returns map of currency id and href to exchange rate against base currency."""
        rates = {}
        try:
            curr_resp = await self._request("GET", "/entity/currency")
            for r in curr_resp.get("rows", []):
                rate = float(r.get("rate", 1.0))
                cid = r.get("id")
                chref = r.get("meta", {}).get("href")
                if cid:
                    rates[cid] = rate
                if chref:
                    rates[chref] = rate
                iso = r.get("isoCode")
                if iso:
                    rates[iso] = rate
        except Exception as e:
            logger.warning("failed_to_fetch_currency_rates", error=str(e))
        return rates

    async def get_live_stock_by_folder(self, folder_id: str = None, limit: int = 50, offset: int = 0) -> list[dict]:
        """Returns live assortment with stock, reserve, images, and prices filtered by folder."""
        params = {"limit": limit, "offset": offset, "expand": "images"}
        if folder_id:
            folder_url = f"{settings.moysklad_api_url.rstrip('/')}/entity/productfolder/{folder_id}"
            params["filter"] = f"productFolder={folder_url}"
            
        res = await self._request("GET", "/entity/assortment", params=params)
        rows = res.get("rows", [])
        
        output = []
        for p in rows:
            prices = p.get("salePrices", [])
            retail_price = float(prices[0].get("value", 0.0) / 100) if prices else 0.0
            buy_price = float(p.get("buyPrice", {}).get("value", 0.0) / 100)
            stock = float(p.get("stock", 0.0))
            reserve = float(p.get("reserve", 0.0))
            stock_free = max(0, int(stock - reserve))
            path_name = p.get("pathName", "")
            brand = path_name.split("/")[-1] if path_name else ""
            
            # Extract image miniature URL if available
            images_rows = p.get("images", {}).get("rows", [])
            image_url = None
            if images_rows:
                first_img = images_rows[0]
                image_url = (
                    first_img.get("miniature", {}).get("downloadHref")
                    or first_img.get("tiny", {}).get("href")
                    or first_img.get("meta", {}).get("downloadHref")
                )
            
            output.append({
                "id": p.get("id"),
                "moysklad_id": p.get("id"),
                "sku": p.get("article") or p.get("code") or f"MS-{p.get('id', '')[:8]}",
                "name": p.get("name", "Товар без названия"),
                "brand": brand,
                "path_name": path_name,
                "purchase_price": buy_price,
                "retail_price": retail_price,
                "stock_free": stock_free,
                "stock_reserved": int(reserve),
                "stock_total": int(stock),
                "image_url": image_url
            })
        return output

    async def get_all_products_with_stock(self, max_products: int = 6000) -> list[dict]:
        """
        Paginates and fetches up to max_products (e.g. 5000+) products from MoySklad API
        along with real-time stock and prices using asynchronous batch pagination.
        """
        from datetime import datetime
        now = datetime.utcnow()

        # 1. Fetch stock report map in parallel
        stock_map = {}
        try:
            stock_res = await self._paginate("GET", "/report/stock/all")
            for s in stock_res:
                meta_href = s.get("meta", {}).get("href", "")
                prod_id = meta_href.split("/")[-1].split("?")[0]
                stock_map[prod_id] = {
                    "stock": float(s.get("stock", 0.0)),
                    "reserve": float(s.get("reserve", 0.0)),
                    "in_transit": float(s.get("inTransit", 0.0)),
                    "price": float(s.get("price", 0.0)) / 100,
                    "sale_price": float(s.get("salePrice", 0.0)) / 100,
                }
        except Exception as e:
            logger.warning("failed_to_fetch_stock_map_in_audit", error=str(e))

        # 2. Fetch products paginated with attributes (expand=attributes)
        first_resp = await self._request("GET", "/entity/product", params={"limit": 1000, "offset": 0, "expand": "attributes"})
        total_size = first_resp.get("meta", {}).get("size", 1000)
        all_rows = list(first_resp.get("rows", []))

        target_count = min(total_size, max_products)
        offsets = [off for off in range(1000, target_count, 1000)]

        if offsets:
            async def fetch_page(off):
                try:
                    data = await self._request("GET", "/entity/product", params={"limit": 1000, "offset": off, "expand": "attributes"})
                    return data.get("rows", [])
                except Exception as ex:
                    logger.warning("failed_to_fetch_product_page", offset=off, error=str(ex))
                    return []

            tasks = [fetch_page(off) for off in offsets]
            pages = await asyncio.gather(*tasks)
            for page in pages:
                all_rows.extend(page)

        # 3. Process products and attach stock, attributes and movement dates
        processed = []
        for p in all_rows:
            p_id = p.get("id")
            name = p.get("name", "Товар без названия")
            raw_sku = p.get("article") or p.get("code") or f"MS-{p_id[:8]}"
            sku = str(raw_sku).strip()
            path_name = p.get("pathName", "")

            # Extract 'Бренд товара' from customentity attributes
            brand = ""
            for attr in p.get("attributes", []):
                attr_name = attr.get("name", "")
                if "бренд" in attr_name.lower():
                    val = attr.get("value")
                    if isinstance(val, dict):
                        brand = val.get("name", "")
                    elif isinstance(val, str):
                        brand = val
                    if brand:
                        break

            # Stock info
            st = stock_map.get(p_id, {"stock": 0.0, "reserve": 0.0})
            stock_qty = float(st.get("stock", 0.0))
            stock_reserve = float(st.get("reserve", 0.0))
            stock_free = max(0.0, stock_qty - stock_reserve)

            # Prices
            buy_price_obj = p.get("buyPrice", {})
            buy_price = float(buy_price_obj.get("value", 0.0)) / 100 if buy_price_obj else 0.0
            
            sale_prices = p.get("salePrices", [])
            retail_price = float(sale_prices[0].get("value", 0.0)) / 100 if sale_prices else 0.0
            if buy_price == 0.0 and retail_price > 0.0:
                buy_price = retail_price * 0.7
            if retail_price == 0.0 and buy_price > 0.0:
                retail_price = buy_price * 1.35

            # Calculate days since last movement / update
            updated_str = p.get("updated", "")
            days_in_stock = 45
            if updated_str:
                try:
                    dt = datetime.strptime(updated_str[:19], "%Y-%m-%d %H:%M:%S")
                    days_in_stock = max(0, (now - dt).days)
                except Exception:
                    pass

            processed.append({
                "id": p_id,
                "sku": sku,
                "name": name,
                "brand": brand.strip(),
                "path_name": path_name,
                "stock_qty": stock_qty,
                "stock_free": stock_free,
                "buy_price": buy_price,
                "retail_price": retail_price,
                "days_in_stock": days_in_stock,
                "updated_at": updated_str
            })

        return processed

    async def get_custom_entity_brands(self) -> list:
        """Fetch elements of 'Бренды товара' customentity (056f61f9-de5c-11ef-0a80-06ae0020cdd6)."""
        custom_entity_id = "056f61f9-de5c-11ef-0a80-06ae0020cdd6"
        res = await self._paginate("GET", f"/entity/customentity/{custom_entity_id}")
        brands = []
        for r in res:
            name = r.get("name")
            if name:
                brands.append({
                    "id": r.get("id"),
                    "name": name.strip()
                })
        brands.sort(key=lambda x: x["name"].upper())
        return brands

    async def get_or_create_counterparty(self, client_name: str, phone: str = None) -> dict:
        """Смарт-контрагент дедупликацияси ва бойитиш алгоритми (4 босқич):
        
        1-босқич: Телефон бўйича текшириш (охирги 9 та рақами ёки тўлиқ рақам).
        2-босқич: Исм бўйича текшириш (агар телефон бўйича топилмаса).
        3-босқич: Мавжуд бўлса — боғлаш ва профилни телефон билан бойитиш (PUT /entity/counterparty/{id}).
        4-босқич: Топилмаса — янги контрагент яратиш (POST /entity/counterparty).
        """
        import re
        name = (client_name or "Клиент").strip()
        raw_phone = (phone or "").strip()
        
        digits = re.sub(r"\D", "", raw_phone)
        phone_digits = digits[-9:] if len(digits) >= 9 else digits
        full_phone = None
        if digits:
            if raw_phone.startswith("+"):
                full_phone = raw_phone
            elif len(phone_digits) == 9:
                full_phone = f"+998{phone_digits}"
            else:
                full_phone = f"+{digits}"

        matched_cp = None

        # 1-босқич: Телефон бўйича МойСклад API'дан излаш
        if phone_digits:
            try:
                res = await self._request("GET", "/entity/counterparty", params={"filter": f"phone~={phone_digits}", "limit": 5})
                rows = res.get("rows", [])
                if not rows and full_phone and full_phone != phone_digits:
                    res = await self._request("GET", "/entity/counterparty", params={"filter": f"phone~={full_phone}", "limit": 5})
                    rows = res.get("rows", [])
                if rows:
                    matched_cp = rows[0]
                    logger.info("moysklad_counterparty_found_by_phone", phone=phone_digits, cp_name=matched_cp.get("name"), cp_id=matched_cp.get("id"))
            except Exception as e:
                logger.warning("moysklad_phone_search_failed", phone=phone_digits, error=str(e))

        # 2-босқич: Исм бўйича текшириш (агар телефон бўйича топилмаса)
        if not matched_cp and name:
            try:
                res = await self._request("GET", "/entity/counterparty", params={"filter": f"name~={name}", "limit": 5})
                rows = res.get("rows", [])
                if rows:
                    for row in rows:
                        if row.get("name", "").strip().lower() == name.lower():
                            matched_cp = row
                            break
                    if not matched_cp:
                        matched_cp = rows[0]
                    logger.info("moysklad_counterparty_found_by_name", name=name, cp_id=matched_cp.get("id"))
            except Exception as e:
                logger.warning("moysklad_name_search_failed", name=name, error=str(e))

        # 3-босқич: Мавжуд бўлса — боғлаш ва маълумотини бойитиш (телефон бўлмаса қўшиш)
        if matched_cp:
            cp_phone = (matched_cp.get("phone") or "").strip()
            if not cp_phone and full_phone:
                cp_id = matched_cp.get("id") or matched_cp.get("meta", {}).get("href", "").split("/")[-1].split("?")[0]
                try:
                    logger.info("enriching_counterparty_phone", cp_id=cp_id, phone=full_phone)
                    updated_cp = await self._request("PUT", f"/entity/counterparty/{cp_id}", json_data={"phone": full_phone})
                    if updated_cp.get("meta"):
                        return updated_cp["meta"]
                except Exception as e:
                    logger.warning("failed_to_enrich_counterparty_phone", cp_id=cp_id, error=str(e))
            return matched_cp["meta"]

        # 4-босқич: Топилмаса — янги яратиш
        payload = {
            "name": name,
            "companyType": "individual"
        }
        if full_phone:
            payload["phone"] = full_phone

        logger.info("creating_new_moysklad_counterparty", name=name, phone=full_phone)
        new_cp = await self._request("POST", "/entity/counterparty", json_data=payload)
        return new_cp["meta"]

    async def get_demands(self, counterparty_href: str = None) -> list[dict]:
        params = {}
        if counterparty_href:
            params["filter"] = f"agent={counterparty_href}"
        return await self._paginate("GET", "/entity/demand", params=params)

    async def get_demand_payments(self, demand_id: str) -> list[dict]:
        return await self._request("GET", f"/entity/demand/{demand_id}/payments")

    async def update_counterparty_status(self, counterparty_id: str, status: str) -> bool:
        """Updates counterparty tag/status in MoySklad safely preserving other tags."""
        try:
            curr = await self._request("GET", f"/entity/counterparty/{counterparty_id}")
            current_tags = list(curr.get("tags", []))
            st_upper = (status or "").upper().strip()

            if st_upper in ["BLOCKED", "HARD-LOCK", "LOCK"]:
                if "BLOCKED" not in current_tags:
                    current_tags.append("BLOCKED")
            elif st_upper in ["ACTIVE", "UNBLOCK", "UNLOCK"]:
                current_tags = [t for t in current_tags if t not in ["BLOCKED", "Hard-Lock", "LOCK"]]
            else:
                if status and status not in current_tags:
                    current_tags.append(status)

            await self._request("PUT", f"/entity/counterparty/{counterparty_id}", json_data={
                "tags": current_tags
            })
            _CP_DETAILS_CACHE["ts"] = 0.0
            return True
        except Exception as e:
            logger.warning("moysklad_counterparty_update_failed", error=str(e), counterparty_id=counterparty_id)
            return False

    async def get_counterparty_details_map(self) -> dict[str, dict]:
        """Fetches and caches basic details (phone, inn, tags, owner/seller) for all counterparties."""
        now = time.time()
        if now - _CP_DETAILS_CACHE["ts"] < 300.0 and _CP_DETAILS_CACHE["map"]:
            return _CP_DETAILS_CACHE["map"]

        details_map = {}
        try:
            # Pre-fetch employees to map owner_id to name (MoySklad does not expand nested fields when limit > 100)
            emp_map = {}
            emp_phone_map = {}
            try:
                emp_resp = await self._request("GET", "/entity/employee", params={"limit": 100})
                for er in emp_resp.get("rows", []):
                    eid = er.get("id") or (er.get("meta", {}).get("href", "").split("/")[-1] if "meta" in er else "")
                    ename = er.get("name") or er.get("fullName") or er.get("shortFio") or ""
                    if eid:
                        emp_map[eid] = ename
                        emp_phone_map[eid] = er.get("phone") or ""
            except Exception as ee:
                logger.warning("failed_to_fetch_employee_map", error=str(ee))

            rows = []
            offset = 0
            while True:
                res = await self._request("GET", "/entity/counterparty", params={"limit": 1000, "offset": offset})
                page_rows = res.get("rows", [])
                rows.extend(page_rows)
                total_size = res.get("meta", {}).get("size", len(rows))
                if len(rows) >= total_size or not page_rows:
                    break
                offset += len(page_rows)

            for cp in rows:
                cid = cp.get("id")
                if cid:
                    owner = cp.get("owner") or {}
                    owner_id = owner.get("id") or (owner.get("meta", {}).get("href", "").split("/")[-1].split("?")[0] if "meta" in owner else "")
                    owner_name = owner.get("name") or owner.get("fullName") or emp_map.get(owner_id) or ""
                    owner_phone = owner.get("phone") or emp_phone_map.get(owner_id) or ""
                    details_map[cid] = {
                        "phone": cp.get("phone") or "",
                        "inn": cp.get("inn") or "",
                        "tags": cp.get("tags") or [],
                        "description": cp.get("description") or "",
                        "company_type": cp.get("companyType") or "legal",
                        "seller_name": owner_name,
                        "seller_id": owner_id,
                        "seller_phone": owner_phone
                    }
            _CP_DETAILS_CACHE["ts"] = now
            _CP_DETAILS_CACHE["map"] = details_map
        except Exception as e:
            logger.warning("failed_to_fetch_counterparty_details_map", error=str(e))
        return details_map

    async def get_latest_demand_sellers(self) -> dict[str, dict]:
        """
        Fetches latest demands with expand=owner,agent to determine the salesperson
        who made the most recent sale/shipment to each counterparty.
        Returns: {agent_id: {"seller_name": ..., "seller_id": ..., "demand_name": ...}}
        """
        now = time.time()
        if now - _DEMAND_SELLER_CACHE["ts"] < 120.0 and _DEMAND_SELLER_CACHE["map"]:
            return _DEMAND_SELLER_CACHE["map"]

        seller_map = {}
        try:
            res = await self._request("GET", "/entity/demand", params={"limit": 100, "order": "moment,desc", "expand": "owner,agent"})
            for d in res.get("rows", []):
                agent = d.get("agent") or {}
                agent_id = agent.get("id") or (agent.get("meta", {}).get("href", "").split("/")[-1].split("?")[0] if "meta" in agent else "")
                if agent_id and agent_id not in seller_map:
                    owner = d.get("owner") or {}
                    owner_name = owner.get("name") or owner.get("fullName") or ""
                    owner_id = owner.get("id") or (owner.get("meta", {}).get("href", "").split("/")[-1].split("?")[0] if "meta" in owner else "")
                    if owner_name:
                        seller_map[agent_id] = {
                            "seller_name": owner_name,
                            "seller_id": owner_id,
                            "demand_name": d.get("name", ""),
                            "moment": d.get("moment", "")
                        }
            _DEMAND_SELLER_CACHE["ts"] = now
            _DEMAND_SELLER_CACHE["map"] = seller_map
        except Exception as e:
            logger.warning("failed_to_fetch_latest_demand_sellers", error=str(e))
        return seller_map

    async def get_all_counterparty_reports(self) -> list[dict]:
        """Fetches and caches all rows from /report/counterparty."""
        now = time.time()
        if now - _CP_CACHE["ts"] < 60.0 and _CP_CACHE["rows"]:
            return _CP_CACHE["rows"]

        all_rows = []
        try:
            rep1 = await self._request("GET", "/report/counterparty", params={"limit": 1000, "offset": 0})
            rows1 = rep1.get("rows", [])
            all_rows.extend(rows1)
            total_size = rep1.get("meta", {}).get("size", len(rows1))
            if total_size > 1000:
                rep2 = await self._request("GET", "/report/counterparty", params={"limit": 1000, "offset": 1000})
                all_rows.extend(rep2.get("rows", []))
            _CP_CACHE["ts"] = now
            _CP_CACHE["rows"] = all_rows
        except Exception as e:
            logger.warning("failed_to_fetch_all_counterparty_reports", error=str(e))
            if _CP_CACHE["rows"]:
                return _CP_CACHE["rows"]
        return all_rows

    async def build_meta_ref(self, entity_type: str, entity_id: str) -> dict:
        return {
            "meta": {
                "href": f"{settings.moysklad_api_url.rstrip('/')}/entity/{entity_type}/{entity_id}",
                "metadataHref": f"{settings.moysklad_api_url.rstrip('/')}/entity/{entity_type}/metadata",
                "type": entity_type,
                "mediaType": "application/json"
            }
        }

    async def get_counterparties_by_segment(self, segment: str = "debtors", limit: int = 200) -> list[dict]:
        """Returns counterparties from MoySklad filtered by segment with aging and contact details:
        - debtors: balance < 0 (sorted by abs(balance) descending)
        - creditors: balance > 0 (sorted by balance descending)
        - leads / all: all counterparties
        """
        seg = (segment or "debtors").lower().strip()
        rows = await self.get_all_counterparty_reports()
        details_map = await self.get_counterparty_details_map()
        
        from datetime import datetime
        now_dt = datetime.utcnow()

        results = []
        for r in rows:
            bal = float(r.get("balance", 0.0))
            cp = r.get("counterparty", {})
            cp_id = cp.get("id") or (cp.get("meta", {}).get("href", "").split("/")[-1].split("?")[0])
            name = cp.get("name", "Номсиз")
            
            cp_det = details_map.get(cp_id, {})
            phone = cp_det.get("phone") or cp.get("phone") or ""
            tags = cp_det.get("tags") or []
            is_tag_blocked = any(t.upper() in ["BLOCKED", "HARD-LOCK", "LOCK"] for t in tags)

            last_demand_str = r.get("lastDemandDate")
            days_overdue = 0
            last_demand_display = "—"
            if last_demand_str:
                try:
                    clean_str = last_demand_str.split(".")[0].replace("Z", "")
                    if "T" in clean_str:
                        dt = datetime.fromisoformat(clean_str)
                    else:
                        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                    days_overdue = max(0, (now_dt - dt).days)
                    last_demand_display = dt.strftime("%d.%m.%Y")
                except Exception:
                    pass
            elif r.get("updated"):
                try:
                    clean_str = r.get("updated")[:19]
                    dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                    days_overdue = max(0, (now_dt - dt).days)
                    last_demand_display = dt.strftime("%d.%m.%Y")
                except Exception:
                    pass

            if is_tag_blocked:
                status = "Блокланган"
            elif days_overdue > 60:
                status = "Хавфли"
            elif days_overdue > 30:
                status = "Диққат"
            else:
                status = "Норма"

            if seg in ["debtors", "дебиторы"]:
                if bal < 0:
                    debt_val = abs(bal) / 100.0
                    results.append({
                        "id": cp_id,
                        "moysklad_id": cp_id,
                        "name": name,
                        "phone": phone,
                        "telegram": phone,
                        "balance": bal / 100.0,
                        "debt_sum": debt_val,
                        "formatted_debt": f"{debt_val:,.0f} сўм".replace(",", " "),
                        "company_type": cp.get("companyType") or cp_det.get("company_type", "legal"),
                        "segment": "debtors",
                        "days": days_overdue,
                        "last_demand_date": last_demand_display,
                        "status": status,
                        "is_blocked": is_tag_blocked,
                        "aging": {
                            "0_30_days": debt_val if days_overdue <= 30 else 0.0,
                            "31_60_days": debt_val if 30 < days_overdue <= 60 else 0.0,
                            "over_60_days": debt_val if days_overdue > 60 else 0.0
                        }
                    })
            elif seg in ["creditors", "кредиторы"]:
                if bal > 0:
                    debt_val = bal / 100.0
                    results.append({
                        "id": cp_id,
                        "moysklad_id": cp_id,
                        "name": name,
                        "phone": phone,
                        "telegram": phone,
                        "balance": bal / 100.0,
                        "debt_sum": debt_val,
                        "formatted_debt": f"{debt_val:,.0f} сўм".replace(",", " "),
                        "company_type": cp.get("companyType") or cp_det.get("company_type", "legal"),
                        "segment": "creditors",
                        "days": days_overdue,
                        "last_demand_date": last_demand_display,
                        "status": "Тўланиши керак"
                    })
            else:
                debt_val = abs(bal) / 100.0
                results.append({
                    "id": cp_id,
                    "moysklad_id": cp_id,
                    "name": name,
                    "phone": phone,
                    "balance": bal / 100.0,
                    "debt_sum": debt_val,
                    "formatted_debt": f"{debt_val:,.0f} сўм".replace(",", " ") if debt_val > 0 else "0 сўм",
                    "company_type": cp.get("companyType") or cp_det.get("company_type", "legal"),
                    "segment": "leads",
                    "days": days_overdue,
                    "last_demand_date": last_demand_display,
                    "status": status
                })

        results.sort(key=lambda x: x["debt_sum"], reverse=True)
        return results[:limit]

    async def get_counterparty_reconciliation(self, counterparty_id: str) -> dict:
        """Fetches full reconciliation history for counterparty including primary documents:
        - demands (отгрузки)
        - paymentins (входящие платежи)
        - salesreturns (возвраты покупателей)
        - supplies (приемки)
        - paymentouts (исходящие платежи)
        - purchasereturns (возвраты поставщикам)
        """
        cp = {}
        try:
            cp = await self._request("GET", f"/entity/counterparty/{counterparty_id}")
        except Exception:
            pass

        rep = {}
        try:
            rep = await self._request("GET", f"/report/counterparty/{counterparty_id}")
        except Exception:
            pass

        balance = float(rep.get("balance", 0.0)) / 100
        agent_url = f"{settings.moysklad_api_url.rstrip('/')}/entity/counterparty/{counterparty_id}"
        
        # Currency map from MoySklad
        curr_map = {}
        try:
            c_resp = await self._request("GET", "/entity/currency")
            for c in c_resp.get("rows", []):
                cid = c.get("id")
                iso = (c.get("isoCode") or "").upper()
                name = (c.get("name") or "").lower()
                if not iso:
                    if "доллар" in name or "usd" in name:
                        iso = "USD"
                    elif "евро" in name or "eur" in name:
                        iso = "EUR"
                    else:
                        iso = "UZS"
                symbol = "$" if iso == "USD" else ("€" if iso == "EUR" else "сум")
                curr_map[cid] = {
                    "id": cid,
                    "name": c.get("name"),
                    "iso": iso,
                    "symbol": symbol
                }
        except Exception as e:
            logger.warning("failed_to_fetch_currencies", error=str(e))

        def parse_doc_currency(doc: dict) -> dict:
            rate = doc.get("rate") or {}
            curr_meta = rate.get("currency", {}).get("meta", {})
            href = curr_meta.get("href", "")
            cid = href.rstrip("/").split("/")[-1] if href else ""
            if cid in curr_map:
                return curr_map[cid]
            # Check rate exchange value (e.g. rate > 5000 means USD to UZS rate)
            rate_val = rate.get("value")
            if rate_val and float(rate_val) > 5000:
                return {"iso": "USD", "symbol": "$", "name": "доллар"}
            return {"iso": "UZS", "symbol": "сум", "name": "сум"}

        operations = []

        # 1. Demands (Отгрузки) -> Debit for buyer
        try:
            d_resp = await self._request("GET", f"/entity/demand?filter=agent={agent_url}")
            for d in d_resp.get("rows", []):
                val = float(d.get("sum", 0.0)) / 100
                curr = parse_doc_currency(d)
                is_usd = (curr["iso"] == "USD")
                operations.append({
                    "date": d.get("moment", "")[:10],
                    "moment": d.get("moment", ""),
                    "doc_type": "Отгрузка",
                    "doc_number": d.get("name", ""),
                    "description": f"Отгрузка товаров №{d.get('name', '')}",
                    "currency": curr["iso"],
                    "currency_symbol": curr["symbol"],
                    "amount": val,
                    "debit_usd": val if is_usd else 0.0,
                    "credit_usd": 0.0,
                    "debit_uzs": val if not is_usd else 0.0,
                    "credit_uzs": 0.0,
                    "debit": val,
                    "credit": 0.0
                })
        except Exception as e:
            logger.warning("failed_to_fetch_demands", error=str(e))

        # 2. PaymentIn (Входящие платежи) -> Credit for buyer
        try:
            p_resp = await self._request("GET", f"/entity/paymentin?filter=agent={agent_url}")
            for p in p_resp.get("rows", []):
                val = float(p.get("sum", 0.0)) / 100
                curr = parse_doc_currency(p)
                is_usd = (curr["iso"] == "USD")
                operations.append({
                    "date": p.get("moment", "")[:10],
                    "moment": p.get("moment", ""),
                    "doc_type": "Входящий платёж",
                    "doc_number": p.get("name", ""),
                    "description": f"Оплата от покупателя №{p.get('name', '')}",
                    "currency": curr["iso"],
                    "currency_symbol": curr["symbol"],
                    "amount": val,
                    "debit_usd": 0.0,
                    "credit_usd": val if is_usd else 0.0,
                    "debit_uzs": 0.0,
                    "credit_uzs": val if not is_usd else 0.0,
                    "debit": 0.0,
                    "credit": val
                })
        except Exception as e:
            logger.warning("failed_to_fetch_payments_in", error=str(e))

        # 3. SalesReturn (Возвраты покупателей) -> Credit for buyer
        try:
            r_resp = await self._request("GET", f"/entity/salesreturn?filter=agent={agent_url}")
            for r in r_resp.get("rows", []):
                val = float(r.get("sum", 0.0)) / 100
                curr = parse_doc_currency(r)
                is_usd = (curr["iso"] == "USD")
                operations.append({
                    "date": r.get("moment", "")[:10],
                    "moment": r.get("moment", ""),
                    "doc_type": "Возврат покупателя",
                    "doc_number": r.get("name", ""),
                    "description": f"Возврат товара №{r.get('name', '')}",
                    "currency": curr["iso"],
                    "currency_symbol": curr["symbol"],
                    "amount": val,
                    "debit_usd": 0.0,
                    "credit_usd": val if is_usd else 0.0,
                    "debit_uzs": 0.0,
                    "credit_uzs": val if not is_usd else 0.0,
                    "debit": 0.0,
                    "credit": val
                })
        except Exception as e:
            logger.warning("failed_to_fetch_salesreturns", error=str(e))

        # 4. Supply (Приемки от поставщика) -> Credit for supplier
        try:
            s_resp = await self._request("GET", f"/entity/supply?filter=agent={agent_url}")
            for s in s_resp.get("rows", []):
                val = float(s.get("sum", 0.0)) / 100
                curr = parse_doc_currency(s)
                is_usd = (curr["iso"] == "USD")
                operations.append({
                    "date": s.get("moment", "")[:10],
                    "moment": s.get("moment", ""),
                    "doc_type": "Приемка",
                    "doc_number": s.get("name", ""),
                    "description": f"Поставка товаров от поставщика №{s.get('name', '')}",
                    "currency": curr["iso"],
                    "currency_symbol": curr["symbol"],
                    "amount": val,
                    "debit_usd": 0.0,
                    "credit_usd": val if is_usd else 0.0,
                    "debit_uzs": 0.0,
                    "credit_uzs": val if not is_usd else 0.0,
                    "debit": 0.0,
                    "credit": val
                })
        except Exception as e:
            logger.warning("failed_to_fetch_supplies", error=str(e))

        # 5. PaymentOut (Исходящие платежи поставщику) -> Debit for supplier
        try:
            po_resp = await self._request("GET", f"/entity/paymentout?filter=agent={agent_url}")
            for po in po_resp.get("rows", []):
                val = float(po.get("sum", 0.0)) / 100
                curr = parse_doc_currency(po)
                is_usd = (curr["iso"] == "USD")
                operations.append({
                    "date": po.get("moment", "")[:10],
                    "moment": po.get("moment", ""),
                    "doc_type": "Исходящий платёж",
                    "doc_number": po.get("name", ""),
                    "description": f"Оплата поставщику №{po.get('name', '')}",
                    "currency": curr["iso"],
                    "currency_symbol": curr["symbol"],
                    "amount": val,
                    "debit_usd": val if is_usd else 0.0,
                    "credit_usd": 0.0,
                    "debit_uzs": val if not is_usd else 0.0,
                    "credit_uzs": 0.0,
                    "debit": val,
                    "credit": 0.0
                })
        except Exception as e:
            logger.warning("failed_to_fetch_payments_out", error=str(e))

        # Sort chronologically
        operations.sort(key=lambda x: x["moment"])

        # Calculate separate USD and UZS totals and saldos
        total_debit_usd = sum(op["debit_usd"] for op in operations)
        total_credit_usd = sum(op["credit_usd"] for op in operations)
        saldo_usd = total_debit_usd - total_credit_usd

        total_debit_uzs = sum(op["debit_uzs"] for op in operations)
        total_credit_uzs = sum(op["credit_uzs"] for op in operations)
        saldo_uzs = total_debit_uzs - total_credit_uzs

        # Assign indices
        for idx, op in enumerate(operations, 1):
            op["index"] = idx

        return {
            "counterparty_id": counterparty_id,
            "name": cp.get("name", rep.get("counterparty", {}).get("name", "Контрагент")),
            "phone": cp.get("phone"),
            "inn": cp.get("inn"),
            "company_type": cp.get("companyType", "legal"),
            "balance": balance,
            "debt_sum": abs(balance),
            "operations": operations,
            "total_debit_usd": total_debit_usd,
            "total_credit_usd": total_credit_usd,
            "saldo_usd": saldo_usd,
            "total_debit_uzs": total_debit_uzs,
            "total_credit_uzs": total_credit_uzs,
            "saldo_uzs": saldo_uzs,
            "total_debit": sum(op["debit"] for op in operations),
            "total_credit": sum(op["credit"] for op in operations)
        }

    async def update_counterparty_status(self, counterparty_id: str, status: str) -> dict:
        """
        Updates counterparty tags and description in MoySklad when Hard-Lock status changes.
        status: 'BLOCKED' or 'ACTIVE'
        """
        try:
            cp = await self._request("GET", f"/entity/counterparty/{counterparty_id}")
            current_tags = list(cp.get("tags") or [])
            current_desc = cp.get("description") or ""

            block_tags = ["БЛОК", "BLOCKED", "HARD-LOCK", "ҲУЖЖАТ ЧИҚАРИШ ТАҚИҚЛАНГАН"]
            block_warning = "🛑 ДИҚҚАТ: Ушбу контрагент қарздорлик сабабли блокланган (HARD-LOCK)! Сотув ҳужжатлари чиқариш тақиқланади."

            if status == "BLOCKED":
                for tag in block_tags:
                    if tag not in current_tags:
                        current_tags.append(tag)
                if block_warning not in current_desc:
                    new_desc = f"{block_warning}\n{current_desc}".strip()
                else:
                    new_desc = current_desc
            else:
                current_tags = [t for t in current_tags if t not in block_tags and t != "ТАҚИҚЛАНГАН"]
                new_desc = current_desc.replace(block_warning, "").strip()

            payload = {
                "tags": current_tags,
                "description": new_desc
            }
            res = await self._request("PUT", f"/entity/counterparty/{counterparty_id}", json_data=payload)
            logger.info("moysklad_counterparty_status_updated", counterparty_id=counterparty_id, status=status)
            return {"success": True, "data": res}
        except Exception as e:
            logger.warning("moysklad_counterparty_status_update_failed", counterparty_id=counterparty_id, status=status, error=str(e))
            return {"success": False, "error": str(e)}

    async def get_counterparty_real_debt(self, counterparty_id: str) -> float:
        """
        Fetches live real balance directly from MoySklad API /report/counterparty/{counterparty_id}.
        Returns absolute debt amount in UZS (sum / 100).
        """
        try:
            rep = await self._request("GET", f"/report/counterparty/{counterparty_id}")
            bal = float(rep.get("balance", 0.0) or 0.0)
            return abs(bal) / 100.0
        except Exception as e:
            logger.warning("failed_to_fetch_counterparty_real_debt", counterparty_id=counterparty_id, error=str(e))
            return 0.0

    async def get_demand(self, demand_id: str) -> dict:
        """Fetches single demand document with expanded agent and owner relations."""
        return await self._request("GET", f"/entity/demand/{demand_id}", params={"expand": "agent,owner"})

    async def unconduct_demand(self, demand_id: str, reason: str = "") -> dict:
        """
        Unconducts (applicable=False) a demand document in MoySklad
        and updates description with Hard-Lock warning.
        """
        exact_desc = "🛑 ДИҚҚАТ: Ушбу мижозга Hard-Lock тақиқи қўйилган! Ҳужжат дастур томонидан автоматик бекор қилинди."
        if reason:
            exact_desc = f"{exact_desc} ({reason})"
        payload = {
            "applicable": False,
            "description": exact_desc
        }
        try:
            res = await self._request("PUT", f"/entity/demand/{demand_id}", json_data=payload)
            logger.info("demand_unconducted_successfully", demand_id=demand_id)
            return {"success": True, "data": res}
        except Exception as e:
            logger.error("demand_unconduct_failed", demand_id=demand_id, error=str(e))
            return {"success": False, "error": str(e)}

    async def get_webhooks(self) -> list:
        """Fetch list of active webhooks registered in MoySklad."""
        try:
            res = await self._request("GET", "/entity/webhook")
            return res.get("rows", [])
        except Exception as e:
            logger.warning("moysklad_get_webhooks_failed", error=str(e))
            return []

    async def create_webhook(self, url: str, action: str = "CREATE", entity_type: str = "demand") -> dict:
        """Register a webhook in MoySklad."""
        payload = {
            "url": url,
            "action": action,
            "entityType": entity_type,
            "enabled": True
        }
        return await self._request("POST", "/entity/webhook", json_data=payload)

    async def ensure_demand_webhooks(self, webhook_url: str = "https://diyorgroup.uz/api/v1/webhook/moysklad-demand") -> dict:
        """
        Ensures both CREATE and UPDATE webhooks for 'demand' are registered in MoySklad.
        If permission is restricted (code 30004), returns instructions.
        """
        results = {"success": True, "registered": [], "existing": [], "errors": []}
        try:
            existing = await self.get_webhooks()
            existing_actions = set()
            for wh in existing:
                if wh.get("url") == webhook_url and wh.get("entityType") == "demand":
                    existing_actions.add(wh.get("action"))
                    results["existing"].append(wh)

            for action in ["CREATE", "UPDATE"]:
                if action not in existing_actions:
                    try:
                        created = await self.create_webhook(url=webhook_url, action=action, entity_type="demand")
                        results["registered"].append(created)
                    except Exception as ex:
                        results["success"] = False
                        results["errors"].append({"action": action, "error": str(ex)})
        except Exception as e:
            results["success"] = False
            results["errors"].append({"general": str(e)})

        return results

    async def close(self):
        await self.client.aclose()
