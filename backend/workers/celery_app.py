import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('CELERY_CONFIG_MODULE', 'config')

celery_app = Celery('diyorgroup')

# Configure from environment
celery_app.conf.update(
    broker_url=os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/1'),
    result_backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/2'),
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Tashkent',  # Uzbekistan timezone
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue='default',
    task_queues={
        'default': {'exchange': 'default', 'routing_key': 'default'},
        'high_priority': {'exchange': 'high_priority', 'routing_key': 'high_priority'},
        'dlq': {'exchange': 'dlq', 'routing_key': 'dlq'},
    },
    broker_transport_options={
        'visibility_timeout': 3600,  # 1 hour
    },
    beat_schedule={
        'cfo-daily-debt-check': {
            'task': 'workers.cfo_tasks.run_daily_debt_check',
            'schedule': crontab(hour=6, minute=0),  # 06:00 daily
            'options': {'queue': 'high_priority'},
        },
        'sync-products-hourly': {
            'task': 'workers.sync_tasks.sync_moysklad_products',
            'schedule': crontab(minute=0),  # Every hour
            'options': {'queue': 'default'},
        },
    },
)

celery_app.autodiscover_tasks(['workers'])
