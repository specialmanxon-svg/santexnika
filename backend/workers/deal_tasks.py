import asyncio
import structlog
from workers.celery_app import celery_app
from workers.dlq_handler import push_to_dlq
from services.reservation import reserve_deal_products
from core.exceptions import IntegrationError, ValidationError

logger = structlog.get_logger(__name__)

def backoff_schedule(retries):
    delays = [10, 30, 90, 300, 900]
    if retries < len(delays):
        return delays[retries]
    return 900

@celery_app.task(bind=True, max_retries=5, acks_late=True)
def process_deal_reservation(self, deal_id: int):
    """
    Process deal reservation from Bitrix24 to MoySklad.
    """
    logger.info("process_deal_reservation_started", deal_id=deal_id, attempt=self.request.retries + 1)
    try:
        order_id = asyncio.run(reserve_deal_products(deal_id))
        logger.info("process_deal_reservation_success", deal_id=deal_id, order_id=order_id)
        return order_id
    except Exception as exc:
        logger.error("process_deal_reservation_failed", deal_id=deal_id, error=str(exc))
        retries = self.request.retries
        if retries < self.max_retries:
            countdown = backoff_schedule(retries)
            logger.info("process_deal_reservation_retrying", deal_id=deal_id, retry_in=countdown)
            raise self.retry(exc=exc, countdown=countdown)
        else:
            logger.error("process_deal_reservation_max_retries_exceeded", deal_id=deal_id)
            push_to_dlq('workers.deal_tasks.process_deal_reservation', args=(deal_id,), kwargs={}, exc_info=str(exc))
            raise
