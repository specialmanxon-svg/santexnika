"""API Router for automated 5% referral offers dispatching and conversion stats."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.referral_dispatcher import referral_dispatcher

router = APIRouter(prefix="/referrals", tags=["Referral Partner Program"])


class SendReferralOfferRequest(BaseModel):
    specialist_id: Optional[str] = Field(None, description="ID мутахассиса (мастера, прораба, дизайнера)")
    name: str = Field(..., description="Имя специалиста")
    phone_or_tg: str = Field(..., description="Телефон или Telegram @username")
    role: str = Field("Мастер-сантехник", description="Роль/профиль специалиста")
    region: Optional[str] = Field("Ташкент", description="Регион Узбекистана")


@router.post("/send-offer")
async def send_referral_offer(request: SendReferralOfferRequest, db: AsyncSession = Depends(get_db)):
    """
    Отправка персонального предложения 5% бонуса/кэшбэка мастеру/прорабу/дизайнеру.
    Генерирует уникальную реферальную ссылку (t.me/bot?start=ref_...), отправляет в Telegram
    и возвращает ссылку для прямого перехода в Telegram чат.
    """
    try:
        result = await referral_dispatcher.send_offer(
            name=request.name,
            phone_or_tg=request.phone_or_tg,
            role=request.role,
            region=request.region or "Ташкент",
            specialist_id=request.specialist_id,
            session=db
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_referral_stats(
    from_date: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
):
    """
    Получение статистики рассылок реферальных предложений для вкладки 'Рассылки Telegram'.
    (Всего отправлено, Открыто, Вступили в бот, Активные партнёры).
    """
    return await referral_dispatcher.get_stats()


@router.get("/overview")
async def get_referral_overview():
    """Алиас для обратной совместимости с дашбордом."""
    from services.partner_service import partner_service
    return await partner_service.get_partners_overview()


@router.post("/{offer_id}/confirm")
async def confirm_referral_partner(offer_id: str, db: AsyncSession = Depends(get_db)):
    """
    Подтверждение партнёра в реферальной программе (перевод в статус 'В Рефералах 5%').
    """
    return await referral_dispatcher.confirm_offer(offer_id=offer_id, session=db)
