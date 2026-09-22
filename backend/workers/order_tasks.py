import asyncio
import structlog
from workers.celery_app import celery_app
from workers.dlq_handler import push_to_dlq
from services.commission import calculate_designer_commission
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger(__name__)

def backoff_schedule(retries):
    delays = [10, 30, 90, 300, 900]
    return delays[retries] if retries < len(delays) else 900

@celery_app.task(bind=True, max_retries=5, acks_late=True)
def process_order_commission(self, order_href: str):
    """
    Process MoySklad order for designer commission calculation.
    """
    logger.info("process_order_commission_started", order_href=order_href, attempt=self.request.retries + 1)
    try:
        commission = asyncio.run(calculate_designer_commission(order_href))
        logger.info("process_order_commission_success", order_href=order_href, commission=commission)
        return commission
    except Exception as exc:
        logger.error("process_order_commission_failed", order_href=order_href, error=str(exc))
        retries = self.request.retries
        if retries < self.max_retries:
            countdown = backoff_schedule(retries)
            raise self.retry(exc=exc, countdown=countdown)
        else:
            logger.error("process_order_commission_max_retries", order_href=order_href)
            push_to_dlq('workers.order_tasks.process_order_commission', args=(order_href,), kwargs={}, exc_info=str(exc))
            raise

process_order_paid = process_order_commission


async def create_order_with_smart_counterparty(client_name: str, phone: str = None, items: list = None, description: str = None) -> dict:
    """Creates a MoySklad customer order using 4-step smart counterparty deduplication."""
    from services.moysklad_client import MoySkladClient
    from config import settings
    ms = MoySkladClient()
    try:
        agent_meta = await ms.get_or_create_counterparty(client_name, phone=phone)
        positions = []
        for item in items or []:
            ms_id = item.get("moysklad_id")
            qty = item.get("quantity", 1)
            price = int(item.get("price", 0.0) * 100)
            if ms_id:
                positions.append({
                    "quantity": qty,
                    "price": price,
                    "reserve": qty,
                    "assortment": {
                        "meta": {
                            "href": f"{settings.moysklad_api_url.rstrip('/')}/entity/product/{ms_id}",
                            "type": "product",
                            "mediaType": "application/json"
                        }
                    }
                })
        order_payload = {
            "organization": {
                "meta": {
                    "href": f"{settings.moysklad_api_url.rstrip('/')}/entity/organization/{settings.moysklad_organization_id}",
                    "type": "organization",
                    "mediaType": "application/json"
                }
            },
            "agent": {
                "meta": agent_meta
            }
        }
        if description:
            order_payload["description"] = description
        if positions:
            order_payload["positions"] = positions
        return await ms.create_customer_order(order_payload)
    finally:
        await ms.close()


@celery_app.task(bind=True, max_retries=3, acks_late=True)
def create_customer_order_task(self, client_name: str, phone: str = None, items: list = None, description: str = None):
    """Celery task to create a customer order in MoySklad with smart counterparty resolution."""
    logger.info("create_customer_order_task_started", client_name=client_name, phone=phone)
    try:
        res = asyncio.run(create_order_with_smart_counterparty(client_name, phone, items, description))
        logger.info("create_customer_order_task_success", order_id=res.get("id"), order_name=res.get("name"))
        return res
    except Exception as exc:
        logger.error("create_customer_order_task_failed", error=str(exc))
        retries = self.request.retries
        if retries < self.max_retries:
            raise self.retry(exc=exc, countdown=backoff_schedule(retries))
        else:
            push_to_dlq('workers.order_tasks.create_customer_order_task', args=(client_name, phone, items, description), kwargs={}, exc_info=str(exc))
            raise


