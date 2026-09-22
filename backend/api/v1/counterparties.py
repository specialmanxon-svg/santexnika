"""API endpoints for Counterparties and Reconciliation reports from MoySklad."""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from services.moysklad_client import MoySkladClient

router = APIRouter(prefix="/counterparties", tags=["Counterparties"])

@router.get("/by-segment", summary="Контрагенты по сегменту (Дебиторы, Кредиторы, Лиды)")
async def get_counterparties_by_segment(
    segment: str = Query("debtors", description="Сегмент: debtors, creditors, leads"),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Возвращает список контрагентов из МойСклад по выбранному сегменту с суммами задолженности.
    - debtors: контрагенты с balance < 0 (дебиторская задолженность перед нами)
    - creditors: контрагенты с balance > 0 (наша кредиторская задолженность перед поставщиками)
    - leads / all: все контрагенты
    """
    ms_client = MoySkladClient()
    try:
        if not await ms_client.is_configured():
            raise HTTPException(status_code=503, detail="MoySklad is not configured")
        
        counterparties = await ms_client.get_counterparties_by_segment(segment=segment, limit=limit)
        return counterparties
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch counterparties: {str(e)}")
    finally:
        await ms_client.close()


@router.get("/{counterparty_id}/reconciliation", summary="Детальная история операций для Акта сверки")
async def get_counterparty_reconciliation(counterparty_id: str):
    """
    Возвращает детальную историю операций (отгрузки, входящие платежи, возвраты, приемки)
    для конкретного контрагента с расчетом сальдо.
    """
    ms_client = MoySkladClient()
    try:
        if not await ms_client.is_configured():
            raise HTTPException(status_code=503, detail="MoySklad is not configured")
        
        reconciliation_data = await ms_client.get_counterparty_reconciliation(counterparty_id)
        return reconciliation_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch reconciliation: {str(e)}")
    finally:
        await ms_client.close()
