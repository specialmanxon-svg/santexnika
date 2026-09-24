"""Finance and Cash Flow API router for Diyor Group Dashboard."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from services.cashflow_service import cashflow_service

router = APIRouter(prefix="/finance", tags=["Молия ва Пул айланмаси (Finance & Cash Flow)"])


@router.get("/cashflow-summary", summary="Пул айланмаси ва маржиналлик ҳисоботи")
@router.get("/cash-flow", summary="Пул айланмаси ва маржиналлик ҳисоботи (Алиас)")
@router.get("/cashflow", summary="Пул айланмаси ва маржиналлик ҳисоботи (Алиас)")
@router.get("/transactions", summary="Пул айланмаси ва транзакциялар (Алиас)")
async def get_cashflow_summary(
    date_from: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    from_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    force_refresh: bool = Query(False, description="МойСкладдан янги маълумотларни олиш")
):
    """
    МойСклад маълумотлари асосида пул айланмаси ва маржа бўйича тўлиқ маълумот:
    - Кирим (бугун)
    - Чиқиш (бугун)
    - Соф фойда
    - Маржа %
    - daily_flow: кунлик кирим/чиқимлар
    - category_margin: товар тоифалари бўйича маржа ва рентабеллик
    """
    df = from_date or date_from
    dt = to_date or date_to
    try:
        return await cashflow_service.get_cashflow_summary(
            date_from=df,
            date_to=dt,
            force_refresh=force_refresh
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/expenses-by-category", summary="Харажатлар таркиби (Статья расходов)")
@router.get("/expense-items-summary", summary="Харажат моддалари таҳлили (МойСклад Статьи расходов)")
async def get_expenses_by_category(
    start_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    from_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    force_refresh: bool = Query(False, description="МойСкладдан янги маълумотларни олиш")
):
    """
    МойСклад чиқим ҳужжатлари (paymentout ва cashout) асосида харажатлар моддалари (expenseItem):
    [
        { "category": "Ижара (Аренда)", "expense_item": "Ижара (Аренда)", "amount": 15000000, "percentage": 30.0 }, ...
    ]
    """
    df = start_date or from_date or date_from
    dt = end_date or to_date or date_to
    try:
        return await cashflow_service.get_expenses_by_category(
            date_from=df,
            date_to=dt,
            force_refresh=force_refresh
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brand-margin", summary="Брендлар бўйича маржа ва фойда таҳлили")
async def get_brand_margin(
    start_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    from_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    force_refresh: bool = Query(False, description="МойСкладдан янги маълумотларни олиш")
):
    """
    МойСклад фойда ҳисоботи асосида брендлар бўйича сотув, таннарх ва маржа:
    [
        { "brand": "GROHE", "sales": 183362166.0, "cost": 122383984.0, "margin": 60978182.0, "margin_percent": 33.26 },
        { "brand": "VALTEC", "sales": 80000000.0, "cost": 65000000.0, "margin": 15000000.0, "margin_percent": 18.75 }
    ]
    """
    df = start_date or from_date or date_from
    dt = end_date or to_date or date_to
    try:
        return await cashflow_service.get_brand_margin(
            date_from=df,
            date_to=dt,
            force_refresh=force_refresh
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

