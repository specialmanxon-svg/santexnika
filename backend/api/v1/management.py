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

@router.get("/blocked-companies")
async def get_blocked_companies(session: AsyncSession = Depends(get_db)):
    """Returns list of currently blocked companies for CEO selection."""
    companies = await debt_checker.get_blocked_companies(session)
    return {"status": "success", "count": len(companies), "companies": companies}

@router.post("/override-lock")
async def override_lock(
    request: OverrideLockRequest,
    session: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_current_superuser)
):
    """CEO emergency override to unblock one or all companies."""
    pin_code = request.get_pin()
    if not verify_otp(pin_code):
        logger.warning("override_failed_invalid_otp", pin=pin_code, target=request.get_target_company())
        raise HTTPException(status_code=401, detail="Неверный или просроченный OTP/PIN код руководителя")
        
    target = request.get_target_company()
    logger.info("processing_ceo_override", target=target)
    
    success, unblocked_names = await debt_checker.unblock_company(
        company_target=target,
        actor_id=admin_id,
        reason=request.reason,
        session=session
    )
    
    if success:
        # Format names for Telegram and response
        if len(unblocked_names) > 1:
            names_text = "\n".join([f"• <b>{name}</b>" for name in unblocked_names])
            title_text = f"Барча <b>{len(unblocked_names)} та компания</b> блокдан ечилди"
        elif len(unblocked_names) == 1:
            names_text = f"• <b>{unblocked_names[0]}</b>"
            title_text = f"<b>{unblocked_names[0]}</b> блокдан ечилди"
        else:
            names_text = f"• <b>{target}</b>"
            title_text = f"<b>{target}</b> блокдан ечилди"

        # Notify via telegram
        telegram_msg = (
            f"🔓 <b>CEO Override Executed</b>\n\n"
            f"Ҳолат: {title_text}\n"
            f"Рўйхат:\n{names_text}\n\n"
            f"Сабаб: <i>{request.reason}</i>\n"
            f"Ижрочи: <code>{admin_id}</code>"
        )
        await telegram_notifier.send_ceo_message(telegram_msg)
        
        return {
            "status": "success",
            "message": f"CEO Override: {title_text}",
            "unblocked_companies": unblocked_names,
            "count": len(unblocked_names)
        }
    
    raise HTTPException(status_code=500, detail="Failed to unblock company")

@router.post("/run-cfo-analysis")
async def run_cfo_analysis(session: AsyncSession = Depends(get_db)):
    """Manually trigger AI CFO Agent debt analysis (Cron replacement for demo)."""
    logger.info("manual_cfo_analysis_triggered")
    try:
        # We call sync_debt_registry which does the analysis and DB syncing
        from services.debt_checker import debt_checker
        analysis = await debt_checker.sync_debt_registry(session)
        # Check and block (this triggers block_company and Telegram)
        blocked_ids = await debt_checker.check_and_block_companies(session)
        
        return {
            "status": "success", 
            "analysis": analysis,
            "newly_blocked_count": len(blocked_ids),
            "blocked_ids": blocked_ids
        }
    except Exception as e:
        logger.error("cfo_analysis_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
