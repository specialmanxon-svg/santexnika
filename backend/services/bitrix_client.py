import asyncio
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import settings

logger = structlog.get_logger(__name__)

class BitrixClient:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.semaphore = asyncio.Semaphore(2)
        self.webhook_url = settings.bitrix24_webhook_url.rstrip("/")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError))
    )
    async def _request(self, method: str, params: dict = None) -> dict:
        url = f"{self.webhook_url}/{method}.json"
        
        async with self.semaphore:
            logger.info("bitrix_request", method=method, params=params)
            response = await self.client.post(url, json=params or {})
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                logger.error("bitrix_api_error", error=data["error"], error_description=data.get("error_description"))
                raise ValueError(f"Bitrix24 API Error: {data.get('error_description', data['error'])}")
            return data.get("result")

    async def get_deal(self, deal_id: int) -> dict:
        return await self._request("crm.deal.get", {"id": deal_id})

    async def update_deal(self, deal_id: int, fields: dict) -> bool:
        result = await self._request("crm.deal.update", {"id": deal_id, "fields": fields})
        return bool(result)

    async def get_deal_products(self, deal_id: int) -> list[dict]:
        return await self._request("crm.deal.productrows.get", {"id": deal_id})

    async def update_company(self, company_id: int, fields: dict) -> bool:
        result = await self._request("crm.company.update", {"id": company_id, "fields": fields})
        return bool(result)

    async def get_company(self, company_id: int) -> dict:
        return await self._request("crm.company.get", {"id": company_id})

    async def add_task_comment(self, task_id: int, text: str, author_id: int = 1) -> int:
        params = {
            "TASKID": task_id,
            "FIELDS": {
                "POST_MESSAGE": text,
                "AUTHOR_ID": author_id
            }
        }
        return await self._request("task.commentitem.add", params)

    async def batch(self, commands: dict) -> dict:
        # Commands must be a dict where key is command name, value is API path like 'crm.deal.get?ID=1'
        return await self._request("batch", {"cmd": commands})

    async def close(self):
        await self.client.aclose()
