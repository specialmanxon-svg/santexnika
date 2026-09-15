"""API endpoints for Growth Director forecasts and Inventory Liquidity."""
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db
from agents.growth_agent import GrowthDirectorAgent
from agents.inventory_agent import InventoryAgent
from core.rbac import RBACService

router = APIRouter(prefix="/analytics", tags=["Analytics & Agents"])
growth_agent = GrowthDirectorAgent()
inventory_agent = InventoryAgent()
rbac_service = RBACService()


@router.get("/growth-forecast")
async def get_growth_forecast(city: str = Query("Бухара", enum=["Бухара", "Ташкент", "Алматы", "Астана"])):
    """Прогнозирование регионального спроса ИИ-Директором по развитию."""
    return growth_agent.forecast_regional_demand(city)


@router.get("/inventory-liquidity")
async def get_inventory_liquidity(db: AsyncSession = Depends(get_db)):
    """Аудит неликвидов (>90 дней) и расчет +5% менеджерского бонуса."""
    return await inventory_agent.audit_inventory_liquidity(db)


@router.get("/export")
async def export_data(
    format_type: str = Query("json", enum=["json", "csv", "excel"]),
    dataset: str = Query("financial_summary", enum=["financial_summary", "debt_registry", "inventory_liquidity"]),
    role: str = Query("owner", description="Роль пользователя в RBAC")
):
    """Выгрузка данных по запросу владельца (Data Portability & Backup)."""
    if not rbac_service.can_export_data(role):
        raise HTTPException(status_code=403, detail="Экспорт разрешен только владельцу (Owner)")
    
    file_bytes, media_type, filename = rbac_service.export_dataset(dataset, format_type)
    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
