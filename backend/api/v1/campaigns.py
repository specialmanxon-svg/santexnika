"""API endpoints for Telegram Broadcast Campaigns with Double Check."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from services.telegram_campaign import TelegramCampaignService

router = APIRouter(prefix="/campaigns", tags=["Telegram Campaigns"])
service = TelegramCampaignService()


class CreateCampaignRequest(BaseModel):
    name: Optional[str] = "Рассылка"
    segment_type: Optional[str] = None
    segment: Optional[str] = None
    message_text: Optional[str] = None
    text: Optional[str] = None
    tone: Optional[int] = 1
    action: Optional[str] = None
    attach_reconciliation_act: bool = True
    counterparty_id: Optional[str] = None
    counterparty_name: Optional[str] = None
    phone: Optional[str] = None
    debt_formatted: Optional[str] = None


class DoubleCheckRequest(BaseModel):
    confirmed_by_user: str


@router.post("/create")
async def create_campaign(data: CreateCampaignRequest):
    """Создание и отправка персонализированной рассылки контрагенту."""
    seg = data.segment_type or data.segment or "DEBTORS"
    txt = data.message_text or data.text or "Уважаемый партнер, направляем акт сверки."
    name = data.name or f"Рассылка {seg}"
    
    res = await service.create_campaign(
        name=name,
        segment=seg,
        text=txt,
        attach_act=data.attach_reconciliation_act,
        counterparty_id=data.counterparty_id,
        counterparty_name=data.counterparty_name,
        phone=data.phone,
        debt_formatted=data.debt_formatted,
        tone=data.tone
    )
    
    if data.action == "execute":
        # Direct execution from UI
        camp_id = res["campaign_id"]
        await service.confirm_double_check(camp_id, "admin_ui")
        return await service.execute_campaign(camp_id)
        
    return res


@router.get("/{campaign_id}/preview")
async def preview_campaign(campaign_id: str):
    """Предпросмотр контента и списка получателей перед отправкой."""
    return await service.preview_campaign(campaign_id)


@router.post("/{campaign_id}/double-check")
async def confirm_double_check(campaign_id: str, data: DoubleCheckRequest):
    """Обязательное двойное подтверждение (Double Check)."""
    return await service.confirm_double_check(campaign_id, data.confirmed_by_user)


@router.get("/act-preview")
async def get_act_preview(company_id: str = "default", session: AsyncSession = Depends(get_db)):
    """Предпросмотр Акта сверки в виде официального HTML документа."""
    from services.act_generator import generate_act_html
    html_content = await generate_act_html(company_id, session)
    return Response(content=html_content, media_type="text/html")


@router.get("/act-download")
async def download_act_pdf(company_id: str = "default", session: AsyncSession = Depends(get_db)):
    """Скачивание официального Акта сверки в формате PDF."""
    from services.act_generator import generate_act_pdf
    pdf_stream = await generate_act_pdf(company_id, session)
    return StreamingResponse(
        pdf_stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="Akt_Sverki_MoySklad.pdf"',
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )


@router.post("/{campaign_id}/execute")
async def execute_campaign(campaign_id: str):
    """Запуск массовой отправки рассылки."""
    return await service.execute_campaign(campaign_id)
