"""MoySklad integration endpoints (Webhooks, Live Sync, Stock Balances)."""
from fastapi import APIRouter, Request, HTTPException, Header, status
import structlog
from sqlalchemy import select

from core.security import verify_moysklad_webhook
from core.database import async_session_factory
from models.product import MdmProduct
from models.inventory import InventoryItem
from services.moysklad_client import MoySkladClient
from services.moysklad_sync import sync_moysklad_stock_and_products
from workers.order_tasks import process_order_paid

logger = structlog.get_logger()
router = APIRouter(prefix="/moysklad", tags=["MoySklad (Складской учет и MDM)"])


@router.get("/status", summary="Проверка подключения к МойСклад")
async def get_moysklad_status():
    """
    Проверяет статус авторизации и доступность API МойСклад.
    """
    client = MoySkladClient()
    try:
        conn = await client.test_connection()
        return conn
    finally:
        await client.close()


@router.post("/sync", summary="Синхронизация товаров и остатков")
async def trigger_moysklad_sync(dry_run: bool = False):
    """
    Запускает синхронизацию каталога товаров и реальных складских остатков.
    """
    logger.info("trigger_moysklad_sync_endpoint", dry_run=dry_run)
    result = await sync_moysklad_stock_and_products(dry_run=dry_run)
    return result


@router.get("/folders", summary="Дерево групп товаров (Product Folders) из МойСклад")
async def get_moysklad_folders():
    """
    Возвращает иерархию папок и категорий товаров из МойСклад API.
    """
    client = MoySkladClient()
    try:
        if await client.is_configured():
            return await client.get_product_folders()
        return {"root_folders": [], "all_folders": []}
    except Exception as e:
        logger.warning("moysklad_folders_fetch_failed", error=str(e))
        return {"root_folders": [], "all_folders": [], "error": str(e)}
    finally:
        await client.close()


@router.get("/stock", summary="Остатки товаров в базе и Live МойСклад")
async def get_stock(
    folder_id: str | None = None,
    limit: int = 50,
    offset: int = 0
):
    """
    Возвращает актуальные товары и остатки, при передаче folder_id фильтрует по выбранной категории.
    """
    client = MoySkladClient()
    try:
        if await client.is_configured():
            items = await client.get_live_stock_by_folder(folder_id=folder_id, limit=limit, offset=offset)
            if items:
                return items
    except Exception as e:
        logger.warning("live_stock_fetch_failed_falling_back_to_db", error=str(e))
    finally:
        await client.close()

    # Fallback to local DB if MoySklad live request failed or offline
    async with async_session_factory() as session:
        stmt = select(MdmProduct).offset(offset).limit(limit)
        res = await session.execute(stmt)
        products = res.scalars().all()
        
        return [
            {
                "id": str(p.id),
                "moysklad_id": p.moysklad_id,
                "sku": p.sku,
                "name": p.name,
                "brand": p.brand,
                "purchase_price": float(p.purchase_price),
                "retail_price": float(p.retail_price),
                "stock_free": p.stock_free,
                "stock_reserved": p.stock_reserved,
                "image_url": None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None
            }
            for p in products
        ]


@router.post("/order-paid", summary="Вебхук оплаты заказа МойСклад")
async def handle_order_paid(
    request: Request,
    x_ms_signature: str | None = Header(None, alias="X-Lognex-WebHook-Signature")
):
    """Handle customerorder.updated webhook from MoySklad."""
    body_bytes = await request.body()
    
    if x_ms_signature and not verify_moysklad_webhook(x_ms_signature, body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")
        
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
    events = payload.get("events", [])
    if not events:
        return {"status": "ignored", "reason": "no events"}
        
    logger.info("ms_webhook_received", event_count=len(events))
    
    for event in events:
        order_meta = event.get("meta", {})
        order_id = order_meta.get("href", "").split("/")[-1]
        if order_id:
            process_order_paid.delay(order_id)
            
    return {"status": "accepted", "processed_events": len(events)}
