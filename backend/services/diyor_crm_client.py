"""Service client for connecting to Diyorgroup Native CRM."""
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)

class DiyorCrmClient:
    """Async client connecting to https://diyorgroup.uz/crm"""
    
    def __init__(self, base_url: str = "https://diyorgroup.uz/crm/api/v1", api_key: str = ""):
        self.base_url = base_url
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def get_deal(self, deal_id: str) -> Dict[str, Any]:
        """Возвращает сделку и связанные товарные позиции."""
        url = f"{self.base_url}/deals/{deal_id}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Error fetching deal {deal_id}: {e}")
                raise

    async def update_deal(
        self, 
        deal_id: str, 
        stage: Optional[str] = None, 
        moysklad_order_id: Optional[str] = None, 
        designer_commission: Optional[float] = None, 
        compat_status: Optional[str] = None
    ) -> bool:
        """Обновление полей сделки."""
        url = f"{self.base_url}/deals/{deal_id}"
        payload = {}
        if stage is not None:
            payload["stage"] = stage
        if moysklad_order_id is not None:
            payload["moysklad_order_id"] = moysklad_order_id
        if designer_commission is not None:
            payload["designer_commission"] = designer_commission
        if compat_status is not None:
            payload["compat_status"] = compat_status

        if not payload:
            return True

        async with httpx.AsyncClient() as client:
            try:
                response = await client.patch(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return True
            except httpx.HTTPError as e:
                logger.error(f"Error updating deal {deal_id}: {e}")
                return False

    async def block_company(self, company_id: str, is_blocked: bool, debt_60: float) -> bool:
        """Блокировка или разблокировка компании (B2B)."""
        url = f"{self.base_url}/companies/{company_id}/block"
        payload = {
            "is_blocked": is_blocked,
            "debt_60": debt_60
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return True
            except httpx.HTTPError as e:
                logger.error(f"Error blocking company {company_id}: {e}")
                return False

    async def create_task(self, title: str, description: str, task_type: str, deal_id: Optional[str] = None) -> Dict[str, Any]:
        """Создание новой задачи для сотрудника."""
        url = f"{self.base_url}/tasks"
        payload = {
            "title": title,
            "description": description,
            "task_type": task_type,
            "deal_id": deal_id
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Error creating task '{title}': {e}")
                raise

    async def complete_field_task(self, task_id: str, lat: float, lon: float, accuracy: float, comment: str) -> bool:
        """Завершение выездной задачи с GPS координатами."""
        url = f"{self.base_url}/tasks/{task_id}/complete"
        payload = {
            "lat": lat,
            "lon": lon,
            "accuracy": accuracy,
            "comment": comment
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return True
            except httpx.HTTPError as e:
                logger.error(f"Error completing field task {task_id}: {e}")
                return False

    async def update_company_status(self, company_id: str, status: str) -> bool:
        """Обновление статуса компании (например, BLOCKED)."""
        is_blocked = (str(status).upper() == "BLOCKED")
        return await self.block_company(company_id, is_blocked=is_blocked, debt_60=0.0)

    async def get_deal_products(self, deal_id: str) -> list:
        """Возвращает товарные позиции сделки."""
        try:
            deal = await self.get_deal(deal_id)
            return deal.get("positions", [])
        except Exception:
            return []

    async def call_api(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Эмулятор универсальных CRM вызовов для обратной совместимости."""
        params = params or {}
        logger.info(f"DiyorCrmClient.call_api called with method={method}")
        if "deal.list" in method:
            return []
        elif "deal.update" in method:
            deal_id = str(params.get("id", ""))
            fields = params.get("fields", {})
            return await self.update_deal(
                deal_id=deal_id,
                stage=fields.get("STAGE_ID"),
                moysklad_order_id=fields.get("UF_MS_ORDER_ID"),
                designer_commission=fields.get("UF_DESIGNER_COMMISSION")
            )
        elif "company.list" in method:
            return []
        elif "company.update" in method:
            company_id = str(params.get("id", ""))
            fields = params.get("fields", {})
            return await self.block_company(
                company_id=company_id,
                is_blocked=fields.get("UF_SHIPMENT_BLOCKED", False),
                debt_60=float(fields.get("UF_DEBT_OVERDUE_60", 0.0))
            )
        return {}

    async def close(self):
        """Закрытие сессии (совместимость)."""
        pass
