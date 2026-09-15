import asyncio
import json
import structlog
from datetime import datetime, timezone
from workers.celery_app import celery_app
from core.redis_client import RedisClient
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger(__name__)

def push_to_dlq(task_name: str, args: tuple, kwargs: dict, exc_info: str):
    """
    Pushes failed task to Dead Letter Queue (Redis).
    """
    try:
        redis_client = RedisClient()
        payload = {
            'task': task_name,
            'args': args,
            'kwargs': kwargs,
            'error': exc_info,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # We need an event loop for redis if it is async, assuming sync wrapper or we run via asyncio
        async def _push():
            client = await redis_client.get_client()
            await client.lpush('dlq:failed_events', json.dumps(payload))
            
            notifier = TelegramNotifier()
            await notifier.notify_dlq_failure(task_name, exc_info)

        asyncio.run(_push())
        logger.info("pushed_to_dlq", task_name=task_name)
    except Exception as e:
        logger.error("failed_to_push_dlq", error=str(e), task_name=task_name)

@celery_app.task(bind=True)
def retry_from_dlq(self):
    """
    Pop items from DLQ and requeue them.
    """
    logger.info("retry_from_dlq_started")
    async def _retry():
        redis_client = RedisClient()
        client = await redis_client.get_client()
        count = 0
        while True:
            item = await client.rpop('dlq:failed_events')
            if not item:
                break
            
            data = json.loads(item)
            task_name = data['task']
            args = data.get('args', [])
            kwargs = data.get('kwargs', {})
            
            celery_app.send_task(task_name, args=args, kwargs=kwargs)
            count += 1
            
        return count

    requeued = asyncio.run(_retry())
    logger.info("retry_from_dlq_completed", requeued_count=requeued)
    return requeued
