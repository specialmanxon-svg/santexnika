"""MoySklad webhook endpoints."""
from fastapi import APIRouter, Request, HTTPException, Header
import structlog

from core.security import verify_moysklad_webhook
from workers.order_tasks import process_order_paid

logger = structlog.get_logger()
router = APIRouter(prefix="/moysklad", tags=["MoySklad Webhooks"])

@router.post("/order-paid")
async def handle_order_paid(
    request: Request,
    x_ms_signature: str | None = Header(None, alias="X-Lognex-WebHook-Signature")
):
    """Handle customerorder.updated webhook from MoySklad."""
    body_bytes = await request.body()
    
    if x_ms_signature and not verify_moysklad_webhook(x_ms_signature, body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")
        
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    events = payload.get("events", [])
    if not events:
        return {"status": "ignored", "reason": "no events"}
        
    logger.info("ms_webhook_received", event_count=len(events))
    
    for event in events:
        order_meta = event.get("meta", {})
        order_id = order_meta.get("href", "").split("/")[-1]
        if order_id:
            process_order_paid.delay(order_id)
            
    return {"status": "accepted", "processed_events": len(events)}
