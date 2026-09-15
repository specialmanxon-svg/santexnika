import asyncio
import base64
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import settings

logger = structlog.get_logger(__name__)

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
            
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            base_url=settings.moysklad_api_url.rstrip("/")
        )
        self.semaphore = asyncio.Semaphore(40)

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
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError))
    )
    async def _request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None) -> dict:
        async with self.semaphore:
            logger.info("moysklad_request", method=method, endpoint=endpoint, params=params)
            response = await self.client.request(
                method=method,
                url=endpoint,
                params=params,
                json=json_data
            )
            response.raise_for_status()
            if response.content:
                return response.json()
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

    async def get_demands(self, counterparty_href: str = None) -> list[dict]:
        params = {}
        if counterparty_href:
            params["filter"] = f"agent={counterparty_href}"
        return await self._paginate("GET", "/entity/demand", params=params)

    async def get_demand_payments(self, demand_id: str) -> list[dict]:
        return await self._request("GET", f"/entity/demand/{demand_id}/payments")

    async def update_counterparty_status(self, counterparty_id: str, status: str) -> bool:
        """Updates counterparty tag/status in MoySklad."""
        try:
            await self._request("PUT", f"/entity/counterparty/{counterparty_id}", json_data={
                "tags": [status]
            })
            return True
        except Exception as e:
            logger.warning("moysklad_counterparty_update_failed", error=str(e), counterparty_id=counterparty_id)
            return False

    async def build_meta_ref(self, entity_type: str, entity_id: str) -> dict:
        return {
            "meta": {
                "href": f"{settings.moysklad_api_url.rstrip('/')}/entity/{entity_type}/{entity_id}",
                "metadataHref": f"{settings.moysklad_api_url.rstrip('/')}/entity/{entity_type}/metadata",
                "type": entity_type,
                "mediaType": "application/json"
            }
        }

    async def close(self):
        await self.client.aclose()
