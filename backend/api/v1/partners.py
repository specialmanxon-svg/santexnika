"""API Router for Partners & 5% Referral Program with MoySklad and Telegram."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.partner_service import partner_service

router = APIRouter(prefix="/partners", tags=["Partners & 5% Referral Program"])


class CreatePartnerRequest(BaseModel):
    name: str = Field(..., description="Имя / ФИО специалиста (мастер, прораб, дизайнер)")
    role: str = Field("Сантехник", description="Роль: Сантехник / Дизайнер / Прораб / Архитектор")
    phone_or_tg: str = Field(..., description="Телефон (+998...) или Telegram @username")
    region: Optional[str] = Field("Бухоро", description="Регион: Бухоро, Тошкент, Самарқанд, etc.")


class SendOfferRequest(BaseModel):
    partner_id: Optional[str] = Field(None, description="ID партнера если уже зарегистрирован")
    name: Optional[str] = Field(None, description="Имя специалиста")
    role: Optional[str] = Field("Сантехник", description="Роль специалиста")
    phone_or_tg: Optional[str] = Field(None, description="Телефон или Telegram @username")
    region: Optional[str] = Field("Бухоро", description="Регион")


@router.get("/overview", summary="Аналитика и список партнеров (МойСклад + 5% Бонусы)")
async def get_partners_overview(
    refresh: bool = Query(False, description="Принудительное обновление из МойСклад"),
    from_date: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)")
):
    """
    Возвращает аналитические показатели (4 карточки) и список контрагентов-партнеров:
    - summary: { total_sent, active_partners, conversion_rate, opened_rate }
    - partners_list: [ { id, name, role, phone_or_tg, region, status, status_label, referral_code, total_sales, bonus_amount } ]
    """
    try:
        return await partner_service.get_partners_overview(force_refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load partners overview: {str(e)}")


@router.post("/create", summary="Добавление партнера в МойСклад с тегом 'Реферал 5%'")
async def create_partner(request: CreatePartnerRequest):
    """
    Регистрирует нового контрагента в МойСклад с тегом 'Реферал 5%',
    генерирует уникальный реферальный ID (например, REF-BUX-042)
    и формирует персональную ссылку t.me/... для мгновенной отправки менеджером.
    """
    try:
        result = await partner_service.create_partner(
            name=request.name,
            role=request.role,
            phone_or_tg=request.phone_or_tg,
            region=request.region or "Бухоро"
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create partner: {str(e)}")


@router.post("/send-offer", summary="Генерация и отправка персонального оффера с 5% бонусом")
async def send_partner_offer(request: SendOfferRequest):
    """
    Генерирует персональную ссылку для Telegram и отправляет готовый шаблон приглашения
    (5% кэшбэк/бонус, оптовые скидки, прямые поставки со склада).
    """
    try:
        name = request.name or "Ҳамкор"
        phone_or_tg = request.phone_or_tg or ""
        role = request.role or "Сантехник"
        region = request.region or "Бухоро"

        result = await partner_service.send_offer(
            name=name,
            phone_or_tg=phone_or_tg,
            role=role,
            region=region,
            partner_id=request.partner_id
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send offer: {str(e)}")
