import redis.asyncio as redis
from typing import Optional
from config import settings

redis_client = redis.from_url(settings.redis_url, decode_responses=True)

async def set_idempotency_key(key: str, value: str = "1", expire: int = 3600) -> bool:
    """
    Sets a key in Redis if it does not exist.
    Returns True if the key was set (meaning the operation can proceed),
    False if the key already exists (meaning the operation should be skipped).
    """
    result = await redis_client.set(key, value, ex=expire, nx=True)
    return bool(result)

async def check_idempotency_key(key: str) -> bool:
    """
    Checks if an idempotency key exists.
    """
    return bool(await redis_client.exists(key))

async def push_to_dlq(queue_name: str, payload: str) -> None:
    """
    Pushes a failed payload to a Dead Letter Queue in Redis.
    """
    await redis_client.lpush(f"dlq:{queue_name}", payload)
