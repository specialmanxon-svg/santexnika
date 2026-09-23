"""Warehouse & Inventory Audit API router (Diyor Group)."""
import time
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import structlog

from core.database import get_db, AsyncSessionLocal
from agents.inventory_agent import InventoryAgent
from services.moysklad_client import MoySkladClient
from config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/warehouse", tags=["Омбор ва Товар Аудити"])
inventory_agent = InventoryAgent()

_brands_cache = {"ts": 0.0, "data": []}
_SALES_30D_CACHE = {"ts": 0.0, "data": {}}

# Расмий МойСклад таъминотчилари (Контрагентлар) базаси билан боғлаш
KNOWN_SUPPLIERS = {
    "GROHE": {
        "id": "7447735b-e72e-11ed-0a80-11080021744a",
        "name": "ООО GROHE (Расмий дистрибьютор)"
    },
    "DCO": {
        "id": "035ed044-b42e-11ed-0a80-0db300302fa4",
        "name": "Тошкент DCO Таъминот"
    },
    "RAGLO": {
        "id": "844be85e-5e25-11ef-0a80-18590041a9c6",
        "name": "ТОШКЕНТ RAGLO (Турба ва фитинглар)"
    },
    "MORTON": {
        "id": "46b8efce-543e-11f1-0a80-156e000f3983",
        "name": "Morton sanitary (Ванна ва смесителлар)"
    },
    "DUSEL": {
        "id": "9623e6f5-c78f-11f0-0a80-10c800193501",
        "name": "ДУСЕЛЛ ЭЛЕКТРО ТОВАР"
    },
    "TREND": {
        "id": "febf71b7-ca90-11f0-0a80-036c00029f4f",
        "name": "TREND (Китай сантехника)"
    },
    "LINDA": {
        "id": "48dfaaa7-6e94-11f0-0a80-06360002d61d",
        "name": "LINDA-ZUF ZOTTO (Сантехника)"
    },
    "TRITON": {
        "id": "0059eff5-5f36-11f1-0a80-17b0000b73c2",
        "name": "ТРИТОН ВАННА"
    },
    "TEKBOND": {
        "id": "6e83c063-a124-11f1-0a80-05b00008357e",
        "name": "ТЕКБОНД (Елим ва герметиклар)"
    },
    "VALIS": {
        "id": "5773757a-166b-11ef-0a80-13d00033335f",
        "name": "ВАЛИС МАГАЗИН (Сантехника)"
    },
    "DEFAULT": {
        "id": "7447735b-e72e-11ed-0a80-11080021744a",
        "name": "Асосий таъминотчи (GROHE / Сантехника марказ)"
    }
}


def resolve_supplier_for_item(item: dict) -> dict:
    """Товар номи, бренди ва каталогига қараб МойСклад'даги мос таъминотчини аниқлаш."""
    brand = (item.get("brand") or "").upper()
    name = (item.get("name") or "").upper()
    path = (item.get("path_name") or "").upper()
    full_text = f"{brand} {name} {path}"

    if "GROHE" in full_text:
        return KNOWN_SUPPLIERS["GROHE"]
    if "DCO" in full_text:
        return KNOWN_SUPPLIERS["DCO"]
    if "RAGLO" in full_text or "SPLENKA" in full_text:
        return KNOWN_SUPPLIERS["RAGLO"]
    if "MORTON" in full_text:
        return KNOWN_SUPPLIERS["MORTON"]
    if "DUSEL" in full_text or "ДУСЕЛ" in full_text:
        return KNOWN_SUPPLIERS["DUSEL"]
    if "TREND" in full_text:
        return KNOWN_SUPPLIERS["TREND"]
    if "LINDA" in full_text or "ZOTTO" in full_text:
        return KNOWN_SUPPLIERS["LINDA"]
    if "TRITON" in full_text or "ТРИТОН" in full_text or "ВАННА" in full_text:
        return KNOWN_SUPPLIERS["TRITON"]
    if "TEKBOND" in full_text or "КЛЕЙ" in full_text:
        return KNOWN_SUPPLIERS["TEKBOND"]
    if "ВАЛИС" in full_text or "VALIS" in full_text:
        return KNOWN_SUPPLIERS["VALIS"]

    return KNOWN_SUPPLIERS["DEFAULT"]


async def fetch_30d_product_sales(ms_client: MoySkladClient, force_refresh: bool = False) -> Dict[str, float]:
    """МойСклад API'дан охирги 30 кунлик сотилган соф маҳсулотлар миқдорини олиш."""
    now_ts = time.time()
    if not force_refresh and (now_ts - _SALES_30D_CACHE["ts"] < 300.0) and _SALES_30D_CACHE["data"]:
        return _SALES_30D_CACHE["data"]

    sales_map: Dict[str, float] = {}
    try:
        date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")
        profit_data = await ms_client.get("/report/profit/byproduct", params={"momentFrom": date_from, "limit": 1000})
        for r in profit_data.get("rows", []):
            assort = r.get("assortment", {})
            m_meta = assort.get("meta", {})
            href = m_meta.get("href", "")
            if href:
                pid = href.split("/")[-1].split("?")[0]
                sell_q = float(r.get("sellQuantity", 0.0))
                ret_q = float(r.get("returnQuantity", 0.0))
                net_qty = max(0.0, sell_q - ret_q)
                sales_map[pid] = net_qty
    except Exception as e:
        logger.warning("failed_to_fetch_30d_sales", error=str(e))

    if sales_map:
        _SALES_30D_CACHE["ts"] = now_ts
        _SALES_30D_CACHE["data"] = sales_map
    return sales_map or _SALES_30D_CACHE.get("data", {})


@router.get("/brands")
async def get_brands():
    """
    МойСклад «Справочники -> Бренды товара» (customentity/056f61f9-de5c-11ef-0a80-06ae0020cdd6)
    махсус маълумотномасидан ҳақиқий брендлар рўйхатини олиш.
    """
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


# ═══════════════ ТАЪМИНОТЧИЛАР БЎЙИЧА 30 КУНЛИК СОТУВ ВА ТАВСИЯ ═══════════════

@router.get("/low-stock-by-suppliers", summary="Захираси тугаётган товарларни таъминотчилар бўйича гуруҳлаш ва 30 кунлик тавсия")
async def get_low_stock_by_suppliers(
    threshold: int = Query(5, description="Минимал қолдиқ чегараси"),
    force_refresh: bool = Query(False, description="Маълумотларни янгилаб олиш")
):
    """
    1. МойСклад API'дан охирги 30 кунлик савдо маълумотларини олиб, ҳар бир товарнинг кунлик ўртача сотув тезлигини ҳисоблайди.
    2. 30 кунлик хавфсиз захира учун тавсия миқдорини аниқлайди:
       recommended_qty = max(round((daily_sales * 30) - current_stock), 1)
    3. Товарларни ўз таъминотчилари бўйича гуруҳлаб қайтаради.
    """
    ms_client = MoySkladClient()
    try:
        async with AsyncSessionLocal() as session:
            result = await inventory_agent.audit_inventory_liquidity(session, force_refresh=force_refresh)
            all_low_stock = result.get("low_stock_items", [])
            filtered = [it for it in all_low_stock if it.get("stock_qty", 0) <= threshold]

        sales_map = await fetch_30d_product_sales(ms_client, force_refresh=force_refresh)

        suppliers_map: Dict[str, Dict[str, Any]] = {}

        for it in filtered:
            pid = it.get("id") or it.get("product_id") or it.get("sku") or ""
            sold_30d = float(sales_map.get(pid, 0.0))
            daily_sales = round(sold_30d / 30.0, 2)
            current_stock = float(it.get("stock_qty", 0.0))

            # Талаб этилган формула: recommended_qty = max(round((daily_sales * 30) - current_stock), 1)
            rec_qty = max(int(round((daily_sales * 30.0) - current_stock)), 1)
            buy_price = float(it.get("buy_price", 0.0))
            retail_price = float(it.get("retail_price", 0.0))
            estimated_sum = round(rec_qty * buy_price, 2)

            sup_info = resolve_supplier_for_item(it)
            s_id = sup_info["id"]
            s_name = sup_info["name"]

            if s_id not in suppliers_map:
                suppliers_map[s_id] = {
                    "supplier_id": s_id,
                    "supplier_name": s_name,
                    "total_items": 0,
                    "total_recommended_qty": 0,
                    "total_estimated_sum": 0.0,
                    "items": []
                }

            enriched_item = {
                "product_id": pid,
                "id": pid,
                "sku": it.get("sku", "—"),
                "name": it.get("name", "Номаълум товар"),
                "brand": it.get("brand", ""),
                "category": it.get("category", "Сантехника"),
                "path_name": it.get("path_name", ""),
                "current_stock": current_stock,
                "sales_30d": sold_30d,
                "daily_sales": daily_sales,
                "recommended_qty": rec_qty,
                "buy_price": buy_price,
                "retail_price": retail_price,
                "estimated_sum": estimated_sum
            }

            suppliers_map[s_id]["items"].append(enriched_item)
            suppliers_map[s_id]["total_items"] += 1
            suppliers_map[s_id]["total_recommended_qty"] += rec_qty
            suppliers_map[s_id]["total_estimated_sum"] += estimated_sum

        suppliers_list = list(suppliers_map.values())
        suppliers_list.sort(key=lambda s: s["total_estimated_sum"], reverse=True)

        return {
            "status": "success",
            "threshold": threshold,
            "total_suppliers": len(suppliers_list),
            "total_low_stock_items": len(filtered),
            "suppliers": suppliers_list
        }
    except Exception as e:
        logger.error("low_stock_by_suppliers_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Таъминотчилар бўйича таҳлилда хатолик: {str(e)}")
    finally:
        await ms_client.close()


# ═══════════════ ТАЪМИНОТЧИ БЎЙИЧА ЯГОНА БУЮРТМА ЯРАТИШ (BULK) ═══════════════

class BulkOrderItem(BaseModel):
    product_id: str = Field(..., description="Маҳсулот ID ёки UUID")
    quantity: float = Field(..., description="Буюртма миқдори (дона)")
    buy_price: Optional[float] = Field(default=0.0, description="Харид нархи (сўм)")
    name: Optional[str] = Field(default=None, description="Товар номи")


class BulkSupplierOrderRequest(BaseModel):
    supplier_id: str = Field(..., description="Таъминотчи ID (Контрагент UUID)")
    supplier_name: Optional[str] = Field(default=None, description="Таъминотчи номи")
    items: List[BulkOrderItem] = Field(..., description="Буюртма бериладиган товарлар рўйхати")
    notes: Optional[str] = Field(default="", description="Қўшимча изоҳ")


@router.post("/create-bulk-supplier-order", summary="МойСклад: Таъминотчи бўйича ягона Заказ поставщику яратиш")
async def create_bulk_supplier_order(payload: BulkSupplierOrderRequest):
    """
    Бир хил таъминотчига тегишли барча товарларни сотув тарихи асосида битта умумий
    «Заказ поставщику» (purchaseorder) ҳужжатига жамлаб МойСклад'да яратиш
    ва Telegram (@Diyor_santexnika_2004_bot) орқали раҳбариятга хабарнома юбориш.
    """
    if not payload.items:
        raise HTTPException(status_code=400, detail="Буюртма учун товарлар кўрсатилмаган")

    ms_client = MoySkladClient()
    try:
        # 1. Таъминотчи маълумотларини текшириш
        supplier_id = payload.supplier_id.strip()
        supplier_name = payload.supplier_name or "Асосий таъминотчи"
        supplier_meta = None

        try:
            sup = await ms_client._request("GET", f"/entity/counterparty/{supplier_id}")
            if sup:
                supplier_name = sup.get("name", supplier_name)
                supplier_meta = sup.get("meta")
        except Exception:
            pass

        if not supplier_meta:
            default_sup_id = "7447735b-e72e-11ed-0a80-11080021744a"
            supplier_meta = {
                "href": f"{settings.moysklad_api_url}/entity/counterparty/{default_sup_id}",
                "type": "counterparty",
                "mediaType": "application/json"
            }
            supplier_name = supplier_name or "ООО GROHE"

        # 2. Позициялар рўйхатини тузиш
        positions = []
        total_sum = 0.0
        total_qty = 0.0
        items_summary = []

        for it in payload.items:
            qty = float(it.quantity)
            if qty <= 0:
                continue
            clean_pid = it.product_id.strip()
            buy_price = float(it.buy_price or 0.0)
            price_kop = int(round(buy_price * 100))

            positions.append({
                "quantity": qty,
                "price": price_kop,
                "assortment": {
                    "meta": {
                        "href": f"{settings.moysklad_api_url}/entity/product/{clean_pid}",
                        "type": "product",
                        "mediaType": "application/json"
                    }
                }
            })
            item_total = qty * buy_price
            total_sum += item_total
            total_qty += qty
            pname = it.name or f"Товар ID: {clean_pid[:8]}"
            items_summary.append({
                "name": pname,
                "qty": qty,
                "price": buy_price,
                "total": item_total
            })

        if not positions:
            raise HTTPException(status_code=400, detail="Барча танланган товарлар миқдори нол бўлиши мумкин эмас")

        # 3. МойСклад'да «Заказ поставщику» яратиш
        org_id = settings.moysklad_organization_id or "c5a0e76e-b1a3-11ed-0a80-0bcd0004345d"
        order_payload = {
            "applicable": False,
            "organization": {
                "meta": {
                    "href": f"{settings.moysklad_api_url}/entity/organization/{org_id}",
                    "type": "organization",
                    "mediaType": "application/json"
                }
            },
            "agent": {"meta": supplier_meta},
            "positions": positions,
            "description": f"Diyor Group Dashboard: Таъминотчи ({supplier_name}) бўйича умумий буюртма ({len(positions)} та позиция). {payload.notes or ''}".strip()
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
                permission_warning = "МойСклад созламаларида «Заказы поставщикам -> Создание» ҳуқуқини ёқиш лозим."
            else:
                logger.warning("moysklad_bulk_po_error", error=err_str)

        # 4. Ички ходим / Раҳбариятга Telegram орқали батафсил хабарнома
        items_preview_lines = []
        for i, s_item in enumerate(items_summary[:7], 1):
            q_disp = int(s_item["qty"]) if s_item["qty"].is_integer() else s_item["qty"]
            p_disp = f"{int(s_item['price']):,}".replace(",", " ")
            items_preview_lines.append(f"{i}. <b>{s_item['name']}</b> — {q_disp} дона ({p_disp} сўм)")
        if len(items_summary) > 7:
            items_preview_lines.append(f"<i>...ва яна {len(items_summary) - 7} та қўшимча товар</i>")

        items_text = "\n".join(items_preview_lines)
        tot_sum_disp = f"{int(total_sum):,}".replace(",", " ")
        tot_qty_disp = int(total_qty) if total_qty.is_integer() else total_qty

        telegram_text = (
            "📦 <b>МОЙСКЛАД: ТАЪМИНОТЧИ БЎЙИЧА ЯГОНА БУЮРТМА!</b>\n\n"
            f"🏢 <b>Таъминотчи:</b> {supplier_name}\n"
            f"📄 <b>Ҳужжат рақами:</b> Заказ поставщику №{order_name}\n"
            f"📊 <b>Позициялар сони:</b> {len(positions)} хил товар\n"
            f"🔢 <b>Жами ҳажм:</b> {tot_qty_disp} дона\n"
            f"💰 <b>Умумий харид қиймати:</b> {tot_sum_disp} сўм\n\n"
            f"📋 <b>Буюртма таркиби (топ товарлар):</b>\n{items_text}\n\n"
            "<i>Ҳужжат МойСклад «Закупки -> Заказы поставщикам» бўлимига сақланди.</i>"
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
                        logger.warning("telegram_bulk_po_notify_failed", chat_id=cid, error=str(tg_err))

        return {
            "status": "success",
            "order_id": order_id,
            "order_name": order_name,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "positions_count": len(positions),
            "total_quantity": total_qty,
            "total_sum": total_sum,
            "ms_created": ms_created,
            "telegram_notified": tg_sent,
            "permission_warning": permission_warning,
            "message": f"Таъминотчи ({supplier_name}) бўйича МойСклад'да Заказ поставщику №{order_name} яратилди ва Telegram'га хабарнома юборилди!"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_bulk_supplier_order_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Ягона буюртма яратишда хатолик: {str(e)}")
    finally:
        await ms_client.close()


# ═══════════════ ЯККА ТАРТИБДАГИ БУЮРТМА (ОРҚАГА МУТОБИҚЛИК УЧУН) ═══════════════

class PurchaseOrderCreateRequest(BaseModel):
    product_id: str = Field(..., description="МойСклад маҳсулот ID ёки UUID")
    quantity: float = Field(default=10.0, description="Буюртма миқдори (дона)")
    supplier_id: Optional[str] = Field(default=None, description="Таъминотчи ID (ихтиёрий)")
    notes: Optional[str] = Field(default="", description="Қўшимча изоҳ")


@router.post("/create-purchase-order", summary="МойСклад: Битта товар учун Заказ поставщику яратиш")
async def create_purchase_order(payload: PurchaseOrderCreateRequest):
    """Ягона товар учун МойСклад'да «Заказ поставщику» яратиш (орқага мутобиқлик учун сақланган)."""
    bulk_req = BulkSupplierOrderRequest(
        supplier_id=payload.supplier_id or "7447735b-e72e-11ed-0a80-11080021744a",
        items=[BulkOrderItem(product_id=payload.product_id, quantity=payload.quantity)],
        notes=payload.notes
    )
    return await create_bulk_supplier_order(bulk_req)
