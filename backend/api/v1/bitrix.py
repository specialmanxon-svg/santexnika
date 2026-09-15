from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from core.database import get_db
from core.security import verify_bitrix_webhook
from schemas.bitrix import BitrixWebhookPayload
from workers.deal_tasks import process_deal_reservation

logger = structlog.get_logger()
router = APIRouter(prefix="/bitrix", tags=["Bitrix24 Webhooks"])


@router.post("/deal-stage-change")
async def handle_deal_stage_change(request: Request):
    """Handle ONCRMDEALUPDATE webhook from Bitrix24.
    
    When a deal moves to INVOICE_ISSUED stage, triggers
    product reservation in MoySklad.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    try:
        payload = BitrixWebhookPayload(**body)
    except Exception as e:
        logger.warning("invalid_payload", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid payload schema")
    
    # Verify webhook authenticity
    if not verify_bitrix_webhook(payload.auth):
        raise HTTPException(status_code=403, detail="Invalid webhook token")
    
    deal_id = payload.entity_id
    if not deal_id:
        raise HTTPException(status_code=400, detail="Missing deal ID")
    
    logger.info("deal_webhook_received", deal_id=deal_id, event=payload.event)
    
    # Dispatch to Celery worker asynchronously
    process_deal_reservation.delay(deal_id)
    
    return {"status": "accepted", "deal_id": deal_id}
