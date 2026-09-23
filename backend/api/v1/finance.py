"""Finance and Cash Flow API router for Diyor Group Dashboard."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from services.cashflow_service import cashflow_service

router = APIRouter(prefix="/finance", tags=["Молия ва Пул айланмаси (Finance & Cash Flow)"])


@router.get("/cashflow-summary", summary="Пул айланмаси ва маржиналлик ҳисоботи")
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

