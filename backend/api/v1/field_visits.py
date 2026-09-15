"""Field visit endpoints."""
import json
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from core.database import get_db
from schemas.field_visit import FieldVisitCheckIn, FieldVisitResponse, GeoLocation
from services.visit_validator import visit_validator

logger = structlog.get_logger()
router = APIRouter(prefix="/field-visits", tags=["Field Visits"])

@router.post("/checkin", response_model=FieldVisitResponse)
async def checkin_visit(
    data: str = Form(...),
    photo: UploadFile = File(...),
    session: AsyncSession = Depends(get_db)
):
    """Field visit check-in with GPS and photo validation."""
    try:
        checkin_data = json.loads(data)
        checkin = FieldVisitCheckIn(**checkin_data)
    except Exception as e:
        logger.error("invalid_checkin_data", error=str(e))
        raise HTTPException(status_code=400, detail="Invalid JSON data in form field")
        
    logger.info("processing_checkin", task_id=checkin.task_id)
    
    result = await visit_validator.process_checkin(
        checkin=checkin,
        photo_file=photo,
        session=session
    )
    
    return result
