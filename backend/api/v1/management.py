"""Management endpoints for admin operations."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from core.database import get_db
from core.security import get_current_superuser, verify_otp
from schemas.management import OverrideLockRequest
from services.debt_checker import debt_checker
from services.telegram_notifier import TelegramNotifier

logger = structlog.get_logger()
router = APIRouter(prefix="/management", tags=["Management"])
telegram_notifier = TelegramNotifier()

@router.post("/override-lock")
async def override_lock(
    request: OverrideLockRequest,
    session: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_current_superuser)
):
    """CEO emergency override to unblock a company."""
    if not verify_otp(request.otp_code):
        logger.warning("override_failed_invalid_otp", company=request.company_moysklad_id)
        raise HTTPException(status_code=401, detail="Invalid OTP code")
        
    logger.info("processing_ceo_override", company=request.company_moysklad_id)
    
    success = await debt_checker.unblock_company(
        company_moysklad_id=request.company_moysklad_id,
        actor_id=admin_id,
        reason=request.reason,
        session=session
    )
    
    if success:
        # Notify via telegram
        await telegram_notifier.send_message(
            f"🔓 *CEO Override Executed*\nCompany ID: {request.company_moysklad_id}\nReason: {request.reason}"
        )
        return {"status": "success", "message": "Company unblocked successfully"}
    
    raise HTTPException(status_code=500, detail="Failed to unblock company")
