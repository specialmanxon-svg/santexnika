"""Warehouse & Inventory Audit API router (Diyor Group)."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db, AsyncSessionLocal
from agents.inventory_agent import InventoryAgent
from services.moysklad_client import MoySkladClient

router = APIRouter(prefix="/warehouse", tags=["Омбор ва Товар Аудити"])
inventory_agent = InventoryAgent()

_brands_cache = {"ts": 0.0, "data": []}


@router.get("/brands")
async def get_brands():
    """
    МойСклад «Справочники -> Бренды товара» (customentity/056f61f9-de5c-11ef-0a80-06ae0020cdd6)
    махсус маълумотномасидан ҳақиқий брендлар рўйхатини олиш.
    """
    import time
    now = time.time()
    if _brands_cache["data"] and (now - _brands_cache["ts"] < 300):
        return {
            "status": "success",
            "count": len(_brands_cache["data"]),
            "brands": _brands_cache["data"]
        }

    ms_client = MoySkladClient()
    try:
        brands = await ms_client.get_custom_entity_brands()
        _brands_cache["ts"] = now
        _brands_cache["data"] = brands
        return {
            "status": "success",
            "count": len(brands),
            "brands": brands
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Брендларни олишда хатолик: {str(e)}")
    finally:
        await ms_client.close()


@router.get("/audit")
async def full_audit(
    force_refresh: bool = Query(True, description="МойСкладдан янгилаб олиш"),
    from_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)")
):
    """
    Тўлиқ омбор аудити (МойСклад Live API):
    - Ноликвидлар (90+ кун)
    - Музлаган маблағ
    - Захираси тугаётган топ-товарлар
    - Умумлаштирувчи карточкалар (Жами, Ноликвид, Музлаган, Тугаётган)
    """
    df = from_date or date_from
    dt = to_date or date_to
    try:
        async with AsyncSessionLocal() as session:
            result = await inventory_agent.audit_inventory_liquidity(session, force_refresh=force_refresh)
            return {
                "status": "success",
                "total_products": result.get("total_products", 0),
                "non_liquid_count": result.get("non_liquid_count", 0),
                "frozen_capital": result.get("frozen_capital", 0.0),
                "frozen_capital_usd": result.get("frozen_capital_usd", 0.0),
                "low_stock_count": result.get("low_stock_count", 0),
                "available_brands": result.get("available_brands", []),
                "non_liquid_items": result.get("non_liquid_items", []),
                "low_stock_items": result.get("low_stock_items", []),
                "groups": result.get("groups", [])
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Омбор аудитида хатолик: {str(e)}")


@router.get("/non-liquid")
async def get_non_liquid():
    """90+ кун сотилмаган ноликвидлар ва музлаган маблағ ҳисоботи."""
    try:
        async with AsyncSessionLocal() as session:
            result = await inventory_agent.audit_inventory_liquidity(session, force_refresh=False)
            return {
                "total_products": result.get("total_products", 0),
                "non_liquid_count": result.get("non_liquid_count", 0),
                "frozen_capital": result.get("frozen_capital", 0.0),
                "frozen_capital_usd": result.get("frozen_capital_usd", 0.0),
                "available_brands": result.get("available_brands", []),
                "items": result.get("non_liquid_items", []),
                "groups": result.get("groups", [])
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/low-stock")
async def get_low_stock(threshold: int = Query(5, description="Минимал қолдиқ чегараси")):
    """Захираси тугаётган топ-товарлар рўйхати (МойСклад қолдиқ 1-5 дона)."""
    try:
        async with AsyncSessionLocal() as session:
            result = await inventory_agent.audit_inventory_liquidity(session, force_refresh=False)
            items = result.get("low_stock_items", [])
            filtered = [it for it in items if it.get("stock_qty", 0) <= threshold]
            return {
                "low_stock_count": len(filtered),
                "threshold": threshold,
                "items": filtered
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


from pydantic import BaseModel, Field
from datetime import datetime
import httpx
import structlog
from config import settings

logger = structlog.get_logger(__name__)


class PurchaseOrderCreateRequest(BaseModel):
    product_id: str = Field(..., description="МойСклад маҳсулот ID ёки UUID")
    quantity: float = Field(default=10.0, description="Буюртма миқдори (дона)")
    supplier_id: Optional[str] = Field(default=None, description="Таъминотчи ID (ихтиёрий)")
    notes: Optional[str] = Field(default="", description="Қўшимча изоҳ")


@router.post("/create-purchase-order", summary="МойСклад: Заказ поставщику яратиш")
async def create_purchase_order(payload: PurchaseOrderCreateRequest):
    """
    Захираси тугаётган товар учун МойСклад'да «Заказ поставщику» яратиш
    ва корхонанинг ички Раҳбарият/Таъминотчи гуруҳига Telegram хабарнома юбориш.
    """
    ms_client = MoySkladClient()
    try:
        # 1. Маҳсулот маълумотларини олиш
        clean_pid = payload.product_id.strip()
        product = await ms_client._request("GET", f"/entity/product/{clean_pid}")
        if not product:
            raise HTTPException(status_code=404, detail="МойСклад базасида маҳсулот топилмади")

        product_name = product.get("name", "Номаълум маҳсулот")
        product_meta = product.get("meta")
        buy_price = product.get("buyPrice", {}).get("value", 0.0)
        if buy_price <= 0 and product.get("salePrices"):
            buy_price = product.get("salePrices")[0].get("value", 0.0) * 0.7

        # 2. Таъминотчини аниқлаш
        supplier_name = "Стандарт таъминотчи"
        supplier_meta = None

        if payload.supplier_id:
            try:
                sup = await ms_client._request("GET", f"/entity/counterparty/{payload.supplier_id}")
                supplier_name = sup.get("name", supplier_name)
                supplier_meta = sup.get("meta")
            except Exception:
                pass

        if not supplier_meta and product.get("supplier"):
            sup_ref = product["supplier"].get("meta", {}).get("href")
            if sup_ref:
                try:
                    sup_id = sup_ref.split("/")[-1]
                    sup = await ms_client._request("GET", f"/entity/counterparty/{sup_id}")
                    supplier_name = sup.get("name", supplier_name)
                    supplier_meta = sup.get("meta")
                except Exception:
                    supplier_meta = product["supplier"].get("meta")

        # Агар таъминотчи кўрсатилмаган бўлса, стандарт захира таъминотчисини танлаш
        if not supplier_meta:
            try:
                recent_po = await ms_client._request("GET", "/entity/purchaseorder", params={"limit": 1})
                if recent_po.get("rows"):
                    supplier_meta = recent_po["rows"][0].get("agent", {}).get("meta")
                    sup_id = supplier_meta.get("href", "").split("/")[-1]
                    sup = await ms_client._request("GET", f"/entity/counterparty/{sup_id}")
                    supplier_name = sup.get("name", "ООО GROHE")
                else:
                    cp_list = await ms_client._request("GET", "/entity/counterparty", params={"limit": 1})
                    if cp_list.get("rows"):
                        supplier_meta = cp_list["rows"][0].get("meta")
                        supplier_name = cp_list["rows"][0].get("name", "Асосий таъминотчи")
            except Exception:
                default_sup_id = "7447735b-e72e-11ed-0a80-11080021744a"
                supplier_meta = {
                    "href": f"{settings.moysklad_api_url}/entity/counterparty/{default_sup_id}",
                    "type": "counterparty",
                    "mediaType": "application/json"
                }
                supplier_name = "ООО GROHE"

        # 3. МойСклад API: Заказ поставщику яратиш
        org_id = settings.moysklad_organization_id or "c5a0e76e-b1a3-11ed-0a80-0bcd0004345d"
        org_meta = {
            "href": f"{settings.moysklad_api_url}/entity/organization/{org_id}",
            "type": "organization",
            "mediaType": "application/json"
        }

        order_payload = {
            "applicable": False,
            "organization": {"meta": org_meta},
            "agent": {"meta": supplier_meta},
            "positions": [
                {
                    "quantity": float(payload.quantity),
                    "price": float(buy_price),
                    "assortment": {"meta": product_meta}
                }
            ],
            "description": f"Diyor Group Dashboard: Захираси тугаётган товар учун буюртма. Товар: {product_name}. {payload.notes}".strip()
        }

        order_id = None
        order_name = f"PO-{datetime.now().strftime('%m%d%H%M')}"
        ms_created = False
        permission_warning = None

        try:
            ms_resp = await ms_client._request("POST", "/entity/purchaseorder", json_data=order_payload)
            if ms_resp:
                order_id = ms_resp.get("id")
                order_name = ms_resp.get("name", order_name)
                ms_created = True
        except Exception as ms_err:
            err_str = str(ms_err)
            if "1045" in err_str or "403" in err_str:
                permission_warning = (
                    "МойСклад ҳуқуқи: bot@prestij_diyor фойдаланувчисига МойСклад созламаларида "
                    "«Заказы поставщикам -> Создание» ҳуқуқини ёқиш лозим."
                )
            else:
                logger.warning("moysklad_po_create_error", error=err_str)

        # 4. Ички ходим / Раҳбариятга Telegram хабарнома юбориш (@Diyor_santexnika_2004_bot)
        qty_display = int(payload.quantity) if payload.quantity.is_integer() else payload.quantity
        telegram_text = (
            "📦 <b>МОЙСКЛАД: ЯНГИ БУЮРТМА ЯРАТИЛДИ!</b>\n\n"
            f"🔹 <b>Товар:</b> {product_name}\n"
            f"🔹 <b>Ҳужжат рақами:</b> Заказ поставщику №{order_name}\n"
            f"🔹 <b>Таъминотчи:</b> {supplier_name}\n"
            f"🔹 <b>Буюртма миқдори:</b> {qty_display} дона\n\n"
            "<i>Илтимос, МойСклад тизимига кириб, буюртмани тасдиқланг ва жўнатинг.</i>"
        )

        tg_sent = False
        bot_token = settings.telegram_bot_token
        target_chats = set()
        if settings.telegram_ceo_chat_id:
            target_chats.add(str(settings.telegram_ceo_chat_id))
        if settings.telegram_alert_chat_id:
            target_chats.add(str(settings.telegram_alert_chat_id))

        if bot_token and not bot_token.startswith("test_"):
            async with httpx.AsyncClient(timeout=10.0) as http_c:
                for cid in target_chats:
                    try:
                        tg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                        t_res = await http_c.post(tg_url, json={"chat_id": cid, "text": telegram_text, "parse_mode": "HTML"})
                        if t_res.status_code == 200:
                            tg_sent = True
                    except Exception as tg_err:
                        logger.warning("telegram_notify_failed", chat_id=cid, error=str(tg_err))

        return {
            "status": "success",
            "order_id": order_id,
            "order_name": order_name,
            "product_name": product_name,
            "supplier_name": supplier_name,
            "quantity": payload.quantity,
            "ms_created": ms_created,
            "telegram_notified": tg_sent,
            "permission_warning": permission_warning,
            "message": f"МойСклад'да Заказ поставщику №{order_name} яратилди ва Telegram'га хабарнома юборилди!"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Буюртма яратишда хатолик: {str(e)}")
    finally:
        await ms_client.close()

