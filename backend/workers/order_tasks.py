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

