"""API endpoints for Inventory Liquidity and Data Export."""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from agents.inventory_agent import InventoryAgent
from services.excel_exporter import generate_excel_report

router = APIRouter(prefix="/analytics", tags=["Аналитика ва Экспорт"])
inventory_agent = InventoryAgent()


@router.get("/inventory-liquidity")
async def get_inventory_liquidity(db: AsyncSession = Depends(get_db)):
    """Аудит неликвидов (>90 дней) и расчет +5% менеджерского бонуса."""
    return await inventory_agent.audit_inventory_liquidity(db)


@router.get("/export")
async def export_data(
    format_type: str = Query("json", enum=["json", "csv", "excel"]),
    dataset: str = Query("financial_summary", enum=["financial_summary", "debt_registry", "inventory_liquidity"]),
    db: AsyncSession = Depends(get_db)
):
    """Маълумотларни экспорт қилиш (Excel, JSON, CSV)."""
    if format_type == "excel":
        excel_stream = await generate_excel_report(db)
        return StreamingResponse(
            excel_stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename=diyorgroup_{dataset}.xlsx",
                "Access-Control-Expose-Headers": "Content-Disposition"
            }
        )

    # For JSON/CSV fallback
    return {"status": "export_requested", "format": format_type, "dataset": dataset}


@router.post("/inventory-audit")
async def run_inventory_audit(db: AsyncSession = Depends(get_db)):
    """Запуск аудита неликвидов с обновлением базы и уведомлением в Telegram."""
    result = await inventory_agent.audit_inventory_liquidity(db, force_refresh=True)
    return result
