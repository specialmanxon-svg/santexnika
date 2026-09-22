"""Cash Flow API router."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from services.cashflow_service import cashflow_service

router = APIRouter(prefix="/cashflow", tags=["Пул Айланмаси (Cash Flow)"])

@router.get("/daily")
async def get_daily_cashflow(
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD")
):
    """Daily income/expenses from CashFlowService."""
    try:
        return await cashflow_service.get_daily_cashflow(date_from, date_to)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/monthly")
async def get_monthly_cashflow(
    year: int = Query(2026, description="Year"),
    month: int = Query(9, description="Month (1-12)")
):
    """Monthly summary."""
    try:
        return await cashflow_service.get_monthly_cashflow(year, month)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/summary")
@router.get("/cashflow-summary")
async def get_cashflow_summary(
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    force_refresh: bool = Query(False, description="Force fresh data from MoySklad")
):
    """Full cashflow summary matching GET /api/v1/finance/cashflow-summary."""
    try:
        return await cashflow_service.get_cashflow_summary(date_from=date_from, date_to=date_to, force_refresh=force_refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/margin")
async def get_margin(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None)
):
    """Margin and profit per product category."""
    try:
        return await cashflow_service.get_margin_per_category(date_from, date_to)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


