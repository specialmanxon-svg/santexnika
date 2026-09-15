"""API endpoints for Social Intelligence, Stories Scanner, and Plumber Radar."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from services.social_intelligence import SocialIntelligenceService

router = APIRouter(prefix="/social", tags=["Social Intelligence"])
service = SocialIntelligenceService()


class DetectPartnerRequest(BaseModel):
    instagram_username: str
    bio_text: str
    source_account: str = "@feruz_latipov"


class ScanStoryRequest(BaseModel):
    username: str
    story_text: str
    story_url: str


@router.post("/detect-partner")
async def detect_partner(data: DetectPartnerRequest, db: AsyncSession = Depends(get_db)):
    """Мониторинг новых подписчиков и выявление дизайнеров/архитекторов."""
    return await service.detect_partner_lead(
        instagram_username=data.instagram_username,
        bio_text=data.bio_text,
        source_account=data.source_account,
        session=db
    )


@router.post("/scan-story")
async def scan_story(data: ScanStoryRequest, db: AsyncSession = Depends(get_db)):
    """Семантическое сканирование Stories по ключевым словам сантехники."""
    return await service.scan_story_intent(
        username=data.username,
        story_text=data.story_text,
        story_url=data.story_url,
        session=db
    )


@router.post("/plumbers/{plumber_id}/kp")
async def generate_plumber_kp(plumber_id: str, db: AsyncSession = Depends(get_db)):
    """Генерация персонализированного КП сантехнику по брендовой компетенции."""
    return await service.generate_plumber_kp(plumber_id=plumber_id, session=db)
