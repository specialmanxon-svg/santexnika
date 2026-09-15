"""Compatibility check endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from core.database import get_db
from schemas.compatibility import CompatibilityCheckRequest, CompatibilityCheckResponse
from services.compatibility_checker import compatibility_checker

logger = structlog.get_logger()
router = APIRouter(prefix="/compatibility", tags=["Compatibility Check"])

@router.post("/check", response_model=CompatibilityCheckResponse)
async def check_compatibility(
    request: CompatibilityCheckRequest,
    session: AsyncSession = Depends(get_db)
):
    """Check plumbing compatibility between two SKUs."""
    logger.info("api_compatibility_check", primary=request.primary_sku, target=request.target_sku)
    
    try:
        result = await compatibility_checker.check_compatibility(
            primary_sku=request.primary_sku,
            target_sku=request.target_sku,
            session=session
        )
        return result
    except Exception as e:
        logger.error("api_compatibility_error", error=str(e))
        raise HTTPException(status_code=500, detail="Internal error during compatibility check")
