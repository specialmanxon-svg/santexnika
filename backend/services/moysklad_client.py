import asyncio
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import settings

logger = structlog.get_logger(__name__)

class MoySkladClient:
    def __init__(self):
        headers = {
            "Authorization": f"Bearer {settings.moysklad_token}",
            "Accept-Encoding": "gzip"
        }
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            base_url=settings.moysklad_api_url.rstrip("/")
        )
        self.semaphore = asyncio.Semaphore(40)

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
            # MoySklad might return empty response for DELETE or empty bodies
            if response.content:
                return response.json()
            return {}
            
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

    async def get_product_by_article(self, article: str) -> dict | None:
        params = {"filter": f"article={article}"}
        data = await self._request("GET", "/entity/product", params=params)
        rows = data.get("rows", [])
        return rows[0] if rows else None

    async def get_stock_all(self) -> list[dict]:
        return await self._paginate("GET", "/report/stock/all")

    async def get_counterparty_report(self) -> list[dict]:
        return await self._paginate("GET", "/report/counterparty")

    async def get_demands(self, counterparty_href: str = None) -> list[dict]:
        params = {}
        if counterparty_href:
            params["filter"] = f"agent={counterparty_href}"
        return await self._paginate("GET", "/entity/demand", params=params)

    async def get_demand_payments(self, demand_id: str) -> list[dict]:
        # Get linked payments from demand relations or similar. 
        # Typically demands have linked payments via endpoint
        # For this implementation, returning linked payments via /entity/demand/{id}/payments if it exists or similar
        return await self._request("GET", f"/entity/demand/{demand_id}/payments")

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
