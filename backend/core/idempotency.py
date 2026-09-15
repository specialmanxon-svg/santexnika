"""Idempotency layer using Redis for webhook deduplication."""
import hashlib
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()

IDEMPOTENCY_TTL = 86400  # 24 hours


def generate_idempotency_key(event_type: str, entity_id: str, timestamp: str) -> str:
    """Generate SHA-256 idempotency key from event parameters."""
    raw = f"{event_type}:{entity_id}:{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that checks idempotency keys for webhook endpoints."""
    
    WEBHOOK_PATHS = ["/api/v1/bitrix/", "/api/v1/moysklad/"]
    
    async def dispatch(self, request: Request, call_next):
        # Only check idempotency for webhook endpoints
        path = request.url.path
        if not any(path.startswith(p) for p in self.WEBHOOK_PATHS):
            return await call_next(request)
        
        # Try to extract idempotency key from request
        # For webhooks, we generate the key from the body
        body = await request.body()
        
        # Store body for downstream handlers
        request.state._body = body
        
        # Generate key from request content
        key = hashlib.sha256(body).hexdigest()
        
        try:
            from core.redis_client import check_idempotency_key, set_idempotency_key
            is_duplicate = await check_idempotency_key(key)
            if is_duplicate:
                logger.info("idempotent_request_skipped", path=path, key=key[:16])
                return Response(
                    content='{"status": "already_processed"}',
                    status_code=200,
                    media_type="application/json"
                )
            
            response = await call_next(request)
            
            if response.status_code < 400:
                await set_idempotency_key(key, "1", IDEMPOTENCY_TTL)
            
            return response
        except Exception as e:
            logger.warning("idempotency_check_bypassed", error=str(e))
            return await call_next(request)
