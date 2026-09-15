"""API endpoints for Telegram Broadcast Campaigns with Double Check."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.telegram_campaign import TelegramCampaignService

router = APIRouter(prefix="/campaigns", tags=["Telegram Campaigns"])
service = TelegramCampaignService()


class CreateCampaignRequest(BaseModel):
    name: str
    segment_type: str  # LEADS_WARMUP, DEBTORS, CREDITORS
    message_text: str
    attach_reconciliation_act: bool = False


class DoubleCheckRequest(BaseModel):
    confirmed_by_user: str


@router.post("/create")
async def create_campaign(data: CreateCampaignRequest):
    """Создание черновика сегментированной рассылки."""
    return await service.create_campaign(
        name=data.name,
        segment=data.segment_type,
        text=data.message_text,
        attach_act=data.attach_reconciliation_act
    )


@router.get("/{campaign_id}/preview")
async def preview_campaign(campaign_id: str):
    """Предпросмотр контента и списка получателей перед отправкой."""
    return await service.preview_campaign(campaign_id)


@router.post("/{campaign_id}/double-check")
async def confirm_double_check(campaign_id: str, data: DoubleCheckRequest):
    """Обязательное двойное подтверждение (Double Check)."""
    return await service.confirm_double_check(campaign_id, data.confirmed_by_user)


@router.post("/{campaign_id}/execute")
async def execute_campaign(campaign_id: str):
    """Запуск массовой отправки рассылки."""
    return await service.execute_campaign(campaign_id)
