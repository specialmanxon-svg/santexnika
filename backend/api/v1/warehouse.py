"""Warehouse & Inventory Audit API router (Diyor Group)."""
import time
import math
import asyncio
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
_SALES_180D_CACHE = {"ts": 0.0, "data": {}}

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


async def fetch_180d_product_sales_matrix(ms_client: MoySkladClient, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    МойСклад API орқали охирги 180 кунлик (6 ойлик) сотув ҳужжатлари тарихини олиш ва
    ABC (80% / 15% / 5%) ҳамда XYZ (v < 10% / 10-25% / > 25%) тоифаларини ҳисоблаш.
    """
    now_ts = time.time()
    if not force_refresh and (now_ts - _SALES_180D_CACHE["ts"] < 600.0) and _SALES_180D_CACHE["data"]:
        return _SALES_180D_CACHE["data"]

    now = datetime.now()
    intervals = []
    for i in range(6):
        d_end = now - timedelta(days=i * 30)
        d_start = now - timedelta(days=(i + 1) * 30)
        intervals.append((d_start.strftime("%Y-%m-%d 00:00:00"), d_end.strftime("%Y-%m-%d 23:59:59")))
    intervals.reverse()

    tasks = [
        ms_client.get("/report/profit/byproduct", params={"momentFrom": s, "momentTo": e, "limit": 1000})
        for s, e in intervals
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    product_stats: Dict[str, Dict[str, Any]] = {}

    for m_idx, res in enumerate(results):
        if isinstance(res, dict):
            for r in res.get("rows", []):
                href = r.get("assortment", {}).get("meta", {}).get("href", "")
                if not href:
                    continue
                pid = href.split("/")[-1].split("?")[0]
                if pid not in product_stats:
                    product_stats[pid] = {
                        "name": r.get("assortment", {}).get("name", ""),
                        "monthly_qty": [0.0] * 6,
                        "revenue": 0.0,
                        "total_qty": 0.0
                    }
                q = max(0.0, float(r.get("sellQuantity", 0.0)) - float(r.get("returnQuantity", 0.0)))
                rev = max(0.0, float(r.get("sellSum", 0.0) - r.get("returnSum", 0.0)) / 100.0)
                product_stats[pid]["monthly_qty"][m_idx] = q
                product_stats[pid]["total_qty"] += q
                product_stats[pid]["revenue"] += rev

    # 1. ABC Ҳисоблаш (Даромад улуши: A=80%, B=15%, C=5%)
    sorted_prods = sorted(product_stats.items(), key=lambda x: x[1]["revenue"], reverse=True)
    total_revenue = sum(p["revenue"] for _, p in sorted_prods) or 1.0

    matrix_result: Dict[str, Dict[str, Any]] = {}
    cum_rev = 0.0

    for pid, p in sorted_prods:
        cum_rev += p["revenue"]
        share = cum_rev / total_revenue
        if share <= 0.80:
            abc = "A"
        elif share <= 0.95:
            abc = "B"
        else:
            abc = "C"

        # 2. XYZ Ҳисоблаш (Вариация коэффициенти v = std / mean)
        q_list = p["monthly_qty"]
        mean_q = sum(q_list) / 6.0
        tot_q = p["total_qty"]

        if mean_q <= 0:
            xyz = "Z"
            v = 1.0
        else:
            var = sum((x - mean_q) ** 2 for x in q_list) / 6.0
            std_dev = math.sqrt(var)
            v = std_dev / mean_q
            if v < 0.10:
                xyz = "X"
            elif v <= 0.25:
                xyz = "Y"
            else:
                xyz = "Z"

        matrix_cat = f"{abc}{xyz}"
        v_pct = round(v * 100, 1)

        # 3. Барча 9 та тоифа бўйича буюртма қоидалари ва ранглари
        if matrix_cat == "AX":
            rationale = "Юқори фойдали ва жуда барқарор товар. 30 кунлик захира кафолатланиши шарт (катта буюртма)."
            order_allowed = True
            badge_color = "green"
        elif matrix_cat == "AY":
            rationale = "Юқори фойдали, талаби ўзгариб турадиган товар. Мавсумни ҳисобга олган ҳолда 30 кунлик захира."
            order_allowed = True
            badge_color = "green"
        elif matrix_cat == "AZ":
            rationale = "Юқори фойдали, лекин сотуви нотекис товар. Фақат факт бўйича (аниқ эҳтиёжга кўра) ёки кичик партия."
            order_allowed = True
            badge_color = "blue"
        elif matrix_cat == "BX":
            rationale = "Ўртача фойдали, талаби барқарор. Меъёрий 30 кунлик хавфсиз захира тавсия этилади."
            order_allowed = True
            badge_color = "green"
        elif matrix_cat == "BY":
            rationale = "Ўртача фойдали, сотуви тебраниб туради. Ўртача хавфсиз захира буюртмаси."
            order_allowed = True
            badge_color = "yellow"
        elif matrix_cat == "BZ":
            rationale = "Ўртача фойдали, лекин тасодифий сотилади. Минимал партия билан буюртма қилиш тавсия этилади."
            order_allowed = True
            badge_color = "orange"
        elif matrix_cat == "CX":
            rationale = "Фойдаси кам, лекин мунтазам олинадиган майда товар (ассортимент учун). Меъёрида буюртма бериш."
            order_allowed = True
            badge_color = "blue"
        elif matrix_cat == "CY":
            rationale = "Фойдаси кам, сотуви беқарор. Фақат муҳим бўлса кичик миқдорда буюртма бериш."
            order_allowed = True
            badge_color = "orange"
        else:  # CZ
            rationale = "Ноликвид ёки умуман сотилмайдиган товар! Пул музламаслиги учун буюртма бериш ТАҚИҚЛАНАДИ (0 дона)."
            order_allowed = False
            badge_color = "red"

        matrix_result[pid] = {
            "sales_180d": tot_q,
            "revenue_180d": p["revenue"],
            "monthly_qty": q_list,
            "abc": abc,
            "xyz": xyz,
            "matrix_category": matrix_cat,
            "variation_pct": v_pct,
            "rationale": rationale,
            "order_allowed": order_allowed,
            "badge_color": badge_color
        }

    _SALES_180D_CACHE["ts"] = now_ts
    _SALES_180D_CACHE["data"] = matrix_result
    return matrix_result


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


# ═══════════════ ТАЪМИНОТЧИЛАР БЎЙИЧА 6 ОЙЛИК (180 КУНЛИК) ABC/XYZ ТАҲЛИЛИ ═══════════════

@router.get("/low-stock-by-suppliers", summary="Захираси тугаётган товарларни таъминотчилар бўйича гуруҳлаш ва 6 ойлик ABC/XYZ таҳлили")
async def get_low_stock_by_suppliers(
    threshold: int = Query(5, description="Минимал қолдиқ чегараси"),
    force_refresh: bool = Query(False, description="Маълумотларни янгилаб олиш")
):
    """
    1. МойСклад API орқали охирги 180 кунлик (6 ойлик) сотувлар тарихини олиб,
       ABC (80%/15%/5%) ва XYZ (v < 10% / 10-25% / > 25%) бўйича 9 тоифага ажратади.
    2. Ҳар бир тоифа (AX, AY, AZ, BX, BY, BZ, CX, CY, CZ) бўйича буюртма асослари ва
       тавсия этилган миқдорни шакллантиради (CZ товарлари блокланади).
    3. Товарларни ўз таъминотчилари бўйича гуруҳлаб қайтаради.
    """
    ms_client = MoySkladClient()
    try:
        async with AsyncSessionLocal() as session:
            result = await inventory_agent.audit_inventory_liquidity(session, force_refresh=force_refresh)
            all_low_stock = result.get("low_stock_items", [])
            filtered = [it for it in all_low_stock if it.get("stock_qty", 0) <= threshold]

        matrix_map = await fetch_180d_product_sales_matrix(ms_client, force_refresh=force_refresh)

        suppliers_map: Dict[str, Dict[str, Any]] = {}

        for it in filtered:
            pid = it.get("id") or it.get("product_id") or it.get("sku") or ""
            current_stock = float(it.get("stock_qty", 0.0))
            buy_price = float(it.get("buy_price", 0.0))
            retail_price = float(it.get("retail_price", 0.0))

            # ABC/XYZ Матрица маълумотлари (6 ой давомида сотилмаган бўлса автоматик CZ)
            mat_info = matrix_map.get(pid, {
                "sales_180d": 0.0,
                "revenue_180d": 0.0,
                "monthly_qty": [0.0] * 6,
                "abc": "C",
                "xyz": "Z",
                "matrix_category": "CZ",
                "variation_pct": 0.0,
                "rationale": "Ноликвид ёки умуман сотилмайдиган товар! Пул музламаслиги учун буюртма бериш ТАҚИҚЛАНАДИ (0 дона).",
                "order_allowed": False,
                "badge_color": "red"
            })

            sales_180d = float(mat_info["sales_180d"])
            daily_sales = round(sales_180d / 180.0, 2)
            mat_cat = mat_info["matrix_category"]

            # Тавсия этилган буюртма миқдори
            if mat_cat == "CZ":
                rec_qty = 0
            else:
                target_30d = daily_sales * 30.0
                rec_qty = max(int(round(target_30d - current_stock)), 1)

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
                    "active_order_items": 0,
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
                "sales_180d": sales_180d,
                "monthly_qty": mat_info.get("monthly_qty", [0.0] * 6),
                "daily_sales": daily_sales,
                "abc": mat_info.get("abc", "C"),
                "xyz": mat_info.get("xyz", "Z"),
                "matrix_category": mat_cat,
                "variation_pct": mat_info.get("variation_pct", 0.0),
                "rationale": mat_info.get("rationale", "Буюртма асоси кўрсатилмаган"),
                "order_allowed": mat_info.get("order_allowed", False),
                "badge_color": mat_info.get("badge_color", "red"),
                "recommended_qty": rec_qty,
                "buy_price": buy_price,
                "retail_price": retail_price,
                "estimated_sum": estimated_sum
            }

            suppliers_map[s_id]["items"].append(enriched_item)
            suppliers_map[s_id]["total_items"] += 1
            if mat_info.get("order_allowed", False):
                suppliers_map[s_id]["total_recommended_qty"] += rec_qty
                suppliers_map[s_id]["total_estimated_sum"] += estimated_sum
                suppliers_map[s_id]["active_order_items"] += 1

        suppliers_list = list(suppliers_map.values())
        suppliers_list.sort(key=lambda s: s["total_estimated_sum"], reverse=True)

        return {
            "status": "success",
            "threshold": threshold,
            "period_days": 180,
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
    matrix_category: Optional[str] = Field(default=None, description="ABC/XYZ тоифаси (масалан: AX, BY, CZ)")


class BulkSupplierOrderRequest(BaseModel):
    supplier_id: str = Field(..., description="Таъминотчи ID (Контрагент UUID)")
    supplier_name: Optional[str] = Field(default=None, description="Таъминотчи номи")
    items: List[BulkOrderItem] = Field(..., description="Буюртма бериладиган товарлар рўйхати")
    notes: Optional[str] = Field(default="", description="Қўшимча изоҳ")


@router.post("/create-bulk-supplier-order", summary="МойСклад: Таъминотчи бўйича ягона Заказ поставщику яратиш")
async def create_bulk_supplier_order(payload: BulkSupplierOrderRequest):
    """
    Бир хил таъминотчига тегишли барча товарларни 6 ойлик сотув тарихи ва ABC/XYZ
    матрицаси асосида битта умумий «Заказ поставщику» (purchaseorder) ҳужжатига
    жамлаб МойСклад'да яратиш. CZ (ноликвид) товарлари автоматик равишда чиқариб ташланади.
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

        # 2. Позициялар рўйхатини тузиш (CZ товарлари автоматик чиқарилади)
        positions = []
        total_sum = 0.0
        total_qty = 0.0
        items_summary = []
        skipped_cz_count = 0

        for it in payload.items:
            # CZ тоифасидаги ноликвид товарлар буюртмага қўшилмайди
            mat_cat = (it.matrix_category or "").upper()
            if mat_cat == "CZ":
                skipped_cz_count += 1
                continue

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
                "total": item_total,
                "category": mat_cat
            })

        if not positions:
            if skipped_cz_count > 0:
                raise HTTPException(status_code=400, detail="Барча товарлар CZ (ноликвид) бўлгани сабабли буюртма бериш тақиқланган.")
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
            "description": f"Diyor Group Dashboard: Таъминотчи ({supplier_name}) бўйича 6 ойлик ABC/XYZ таҳлили асосидаги буюртма ({len(positions)} та позиция). {payload.notes or ''}".strip()
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
            cat_badge = f"[{s_item['category']}]" if s_item['category'] else ""
            items_preview_lines.append(f"{i}. <b>{s_item['name']}</b> {cat_badge} — {q_disp} дона ({p_disp} сўм)")
        if len(items_summary) > 7:
            items_preview_lines.append(f"<i>...ва яна {len(items_summary) - 7} та қўшимча товар</i>")

        items_text = "\n".join(items_preview_lines)
        tot_sum_disp = f"{int(total_sum):,}".replace(",", " ")
        tot_qty_disp = int(total_qty) if total_qty.is_integer() else total_qty

        cz_note = f"\n⚠️ <i>{skipped_cz_count} та CZ (ноликвид) товар буюртмадан автоматик чиқариб ташланди.</i>" if skipped_cz_count > 0 else ""

        telegram_text = (
            "📦 <b>МОЙСКЛАД: ТАЪМИНОТЧИ БЎЙИЧА 6 ОЙЛИК ABC/XYZ БУЮРТМА!</b>\n\n"
            f"🏢 <b>Таъминотчи:</b> {supplier_name}\n"
            f"📄 <b>Ҳужжат рақами:</b> Заказ поставщику №{order_name}\n"
            f"📊 <b>Позициялар сони:</b> {len(positions)} хил товар\n"
            f"🔢 <b>Жами ҳажм:</b> {tot_qty_disp} дона\n"
            f"💰 <b>Умумий харид қиймати:</b> {tot_sum_disp} сўм\n\n"
            f"📋 <b>Буюртма таркиби:</b>\n{items_text}{cz_note}\n\n"
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
            "skipped_cz_count": skipped_cz_count,
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
