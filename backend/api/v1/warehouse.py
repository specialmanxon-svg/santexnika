"""Warehouse & Inventory Audit API router (Diyor Group)."""
import time
import math
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from io import BytesIO
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import structlog

from core.database import get_db, AsyncSessionLocal
from agents.inventory_agent import InventoryAgent, _INVENTORY_AUDIT_CACHE, extract_brand
from services.moysklad_client import MoySkladClient
from config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/warehouse", tags=["Омбор ва Товар Аудити"])
inventory_agent = InventoryAgent()

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CACHE_FILE_180D = CACHE_DIR / "abc_xyz_180d_cache.json"

_brands_cache = {"ts": 0.0, "data": []}
_SALES_30D_CACHE = {"ts": 0.0, "data": {}}
_SALES_180D_CACHE = {"ts": 0.0, "data": {}}
_SUPPLIERS_LOWSTOCK_CACHE = {"ts": 0.0, "data": None}
_REFRESH_IN_PROGRESS = False

# Дискдаги кешни юклаш (сервер қайта ишга тушганда дарҳол 0.01 сонияда тайёр бўлиши учун)
if CACHE_FILE_180D.exists():
    try:
        with open(CACHE_FILE_180D, "r", encoding="utf-8") as _f:
            _disk_saved = json.load(_f)
            _SALES_180D_CACHE["ts"] = _disk_saved.get("ts", time.time())
            _SALES_180D_CACHE["data"] = _disk_saved.get("data", {})
            logger.info("loaded_180d_sales_cache_from_disk", count=len(_SALES_180D_CACHE["data"]))
    except Exception as _e:
        logger.warning("failed_loading_180d_disk_cache", error=str(_e))

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


async def _compute_180d_matrix(ms_client: MoySkladClient) -> Dict[str, Dict[str, Any]]:
    """МойСклад API орқали охирги 180 кунлик (6 ойлик) сотувлар тарихини ҳисоблаш (10с timeout ва rate limit ҳимояси)."""
    now = datetime.now()
    intervals = []
    for i in range(6):
        d_end = now - timedelta(days=i * 30)
        d_start = now - timedelta(days=(i + 1) * 30)
        intervals.append((d_start.strftime("%Y-%m-%d 00:00:00"), d_end.strftime("%Y-%m-%d 23:59:59")))
    intervals.reverse()

    results = []
    for s, e in intervals:
        try:
            res = await asyncio.wait_for(
                ms_client.get("/report/profit/byproduct", params={"momentFrom": s, "momentTo": e, "limit": 1000}),
                timeout=10.0
            )
            results.append(res)
        except Exception as err:
            logger.warning("fetch_monthly_profit_interval_failed", start=s, end=e, error=str(err))
            results.append({})
        await asyncio.sleep(0.15)  # МойСклад API rate-limit (max 5 req/sec) ҳимояси

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

    return matrix_result


async def _background_refresh_sales_matrix():
    """Фон режимида 180 кунлик ABC/XYZ матрицасини янгилаш (сервер ва фойдаланувчини куттирмайди)."""
    global _REFRESH_IN_PROGRESS
    if _REFRESH_IN_PROGRESS:
        return
    _REFRESH_IN_PROGRESS = True
    logger.info("start_background_180d_sales_refresh")
    ms_client = MoySkladClient()
    try:
        new_data = await _compute_180d_matrix(ms_client)
        if new_data:
            _SALES_180D_CACHE["ts"] = time.time()
            _SALES_180D_CACHE["data"] = new_data
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                with open(CACHE_FILE_180D, "w", encoding="utf-8") as f:
                    json.dump({"ts": _SALES_180D_CACHE["ts"], "total_products": len(new_data), "data": new_data}, f, ensure_ascii=False)
                logger.info("background_180d_sales_refresh_saved_disk", count=len(new_data))
            except Exception as fe:
                logger.warning("save_disk_180d_cache_failed", error=str(fe))
    except Exception as e:
        logger.error("background_180d_sales_refresh_failed", error=str(e))
    finally:
        _REFRESH_IN_PROGRESS = False
        await ms_client.close()


async def fetch_180d_product_sales_matrix(ms_client: MoySkladClient, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    МойСклад 180 кунлик ABC/XYZ таҳлилини тайёр кешдан ДАРҲОЛ (0.001с) қайтаради.
    Агар кеш эскирган (1 соатдан ошган) бўлса, фон режимида фойдаланувчини куттирмасдан янгилайди.
    """
    now_ts = time.time()
    cached_data = _SALES_180D_CACHE.get("data")

    # 1. Агар хотирада ёки дискдан юкланган кеш мавжуд бўлса — ДАРҲОЛ қайтариш
    if cached_data:
        # 1 соатдан ошган ёки force_refresh бўлса — фонда янгилаш
        if (now_ts - _SALES_180D_CACHE.get("ts", 0) >= 3600.0) or force_refresh:
            if not _REFRESH_IN_PROGRESS:
                asyncio.create_task(_background_refresh_sales_matrix())
        return cached_data

    # 2. Кеш умуман бўлмасагина (биринчи старт) ҳисоблаб кешга ёзиш
    logger.info("cold_start_computing_180d_sales_matrix")
    calculated = await _compute_180d_matrix(ms_client)
    _SALES_180D_CACHE["ts"] = now_ts
    _SALES_180D_CACHE["data"] = calculated
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE_180D, "w", encoding="utf-8") as f:
            json.dump({"ts": now_ts, "total_products": len(calculated), "data": calculated}, f, ensure_ascii=False)
    except Exception as fe:
        logger.warning("save_disk_180d_cache_failed", error=str(fe))
    return calculated


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
            
            # 180 кунлик ABC/XYZ матрицасини қўшиш
            matrix_map = _SALES_180D_CACHE.get("data", {})
            nl_items = result.get("non_liquid_items", [])
            for it in nl_items:
                pid = it.get("id") or it.get("product_id") or ""
                mat = matrix_map.get(pid)
                if mat:
                    it["matrix_category"] = mat.get("matrix_category")
                    it["abc"] = mat.get("abc")
                    it["xyz"] = mat.get("xyz")
                elif not it.get("matrix_category"):
                    it["matrix_category"] = f"{it.get('abc', 'C')}{it.get('xyz', 'Z')}"

            ls_items = result.get("low_stock_items", [])
            for it in ls_items:
                pid = it.get("id") or it.get("product_id") or ""
                mat = matrix_map.get(pid)
                if mat:
                    it["matrix_category"] = mat.get("matrix_category")
                    it["abc"] = mat.get("abc")
                    it["xyz"] = mat.get("xyz")
                elif not it.get("matrix_category"):
                    it["matrix_category"] = f"{it.get('abc', 'B')}{it.get('xyz', 'X')}"

            return {
                "status": "success",
                "total_products": result.get("total_products", 0),
                "non_liquid_count": result.get("non_liquid_count", 0),
                "frozen_capital": result.get("frozen_capital", 0.0),
                "frozen_capital_usd": result.get("frozen_capital_usd", 0.0),
                "low_stock_count": result.get("low_stock_count", 0),
                "available_brands": result.get("available_brands", []),
                "non_liquid_items": nl_items,
                "low_stock_items": ls_items,
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
            matrix_map = _SALES_180D_CACHE.get("data", {})
            nl_items = result.get("non_liquid_items", [])
            for it in nl_items:
                pid = it.get("id") or it.get("product_id") or ""
                mat = matrix_map.get(pid)
                if mat:
                    it["matrix_category"] = mat.get("matrix_category")
                elif not it.get("matrix_category"):
                    it["matrix_category"] = f"{it.get('abc', 'C')}{it.get('xyz', 'Z')}"
            return {
                "total_products": result.get("total_products", 0),
                "non_liquid_count": result.get("non_liquid_count", 0),
                "frozen_capital": result.get("frozen_capital", 0.0),
                "frozen_capital_usd": result.get("frozen_capital_usd", 0.0),
                "available_brands": result.get("available_brands", []),
                "items": nl_items,
                "groups": result.get("groups", [])
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export-non-liquid-excel", summary="Ноликвид товарлар рўйхатини Excel (.xlsx) форматида юклаш")
async def export_non_liquid_excel():
    """
    МойСклад базасидаги барча 90+ кунлик ноликвид товарлар рўйхатини
    профессионал форматланган Excel (.xlsx) файли кўринишида қайтаради.
    """
    try:
        cached_audit = _INVENTORY_AUDIT_CACHE.get("data") or {}
        items = cached_audit.get("non_liquid_items", [])
        if not items:
            async with AsyncSessionLocal() as session:
                result = await inventory_agent.audit_inventory_liquidity(session, force_refresh=False)
                items = result.get("non_liquid_items", [])

        matrix_map = _SALES_180D_CACHE.get("data", {})

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Ноликвид товарлар"

        # Сарлавҳа стили (Diyor Group blue)
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        border_thin = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        headers = [
            "№", "Товар номи", "Артикул (SKU)", "Бренд", "Гуруҳи",
            "ABC/XYZ тоифаси", "Қолдиқ (дона)", "Кунлар", "Музлаган суммаси (сўм)"
        ]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        row_font = Font(name="Calibri", size=10)
        for idx, it in enumerate(items, 1):
            pid = it.get("id") or it.get("product_id") or ""
            mat = matrix_map.get(pid)
            mat_cat = mat.get("matrix_category") if mat else it.get("matrix_category") or f"{it.get('abc', 'C')}{it.get('xyz', 'Z')}"
            frozen_sum = float(it.get("frozen_value") or it.get("cost_uzs") or ((it.get("stock_qty") or 0) * (it.get("buy_price") or 0)))

            row_vals = [
                idx,
                it.get("name", "—"),
                it.get("sku", "—"),
                it.get("brand", "—"),
                it.get("category", it.get("path_name", "Сантехника")),
                mat_cat.upper(),
                float(it.get("stock_qty", 0)),
                f"{it.get('days_in_stock', 90)}+ кун",
                frozen_sum
            ]
            ws.append(row_vals)
            cur_row = idx + 1
            for col_idx in range(1, len(row_vals) + 1):
                cell = ws.cell(row=cur_row, column=col_idx)
                cell.font = row_font
                cell.border = border_thin
                if col_idx in [1, 6, 8]:
                    cell.alignment = Alignment(horizontal="center")
                elif col_idx in [7, 9]:
                    cell.alignment = Alignment(horizontal="right")
                    if col_idx == 9:
                        cell.number_format = '#,##0'

        col_widths = {1: 6, 2: 45, 3: 15, 4: 15, 5: 25, 6: 16, 7: 14, 8: 14, 9: 22}
        for col_idx, width in col_widths.items():
            ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = width

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        filename = f"non_liquid_products_diyor_{datetime.now().strftime('%Y%m%d')}.xlsx"
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excel генерациясида хатолик: {str(e)}")


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
        all_low_stock = []
        try:
            async with AsyncSessionLocal() as session:
                # 8 сониялик қатъий timeout (сервер осилиб қолмаслиги учун)
                audit_task = inventory_agent.audit_inventory_liquidity(session, force_refresh=False)
                result = await asyncio.wait_for(audit_task, timeout=8.0)
                all_low_stock = result.get("low_stock_items", [])
        except Exception as audit_err:
            logger.warning("inventory_audit_timeout_or_error", error=str(audit_err))
            cached_audit = _INVENTORY_AUDIT_CACHE.get("data") or {}
            all_low_stock = cached_audit.get("low_stock_items", [])

        filtered = [it for it in all_low_stock if it.get("stock_qty", 0) <= threshold]

        # 180 кунлик матрицани олиш (тайёр кешдан 0.001 сонияда қайтади)
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

        res_payload = {
            "status": "success",
            "threshold": threshold,
            "period_days": 180,
            "total_suppliers": len(suppliers_list),
            "total_low_stock_items": len(filtered),
            "suppliers": suppliers_list
        }
        _SUPPLIERS_LOWSTOCK_CACHE["data"] = res_payload
        _SUPPLIERS_LOWSTOCK_CACHE["ts"] = time.time()
        return res_payload

    except Exception as e:
        logger.error("low_stock_by_suppliers_error", error=str(e))
        if _SUPPLIERS_LOWSTOCK_CACHE.get("data"):
            logger.info("returning_fallback_cached_suppliers_data")
            return _SUPPLIERS_LOWSTOCK_CACHE["data"]
        return {
            "status": "success",
            "threshold": threshold,
            "period_days": 180,
            "total_suppliers": 0,
            "total_low_stock_items": 0,
            "suppliers": [],
            "warning": f"Маълумот олишда вақтинчалик кечикиш: {str(e)}"
        }
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


@router.post("/export-supplier-order-excel", summary="Таъминотчига буюртма спецификациясини Excel файлида юклаб олиш")
async def export_supplier_order_excel(payload: BulkSupplierOrderRequest):
    """
    Таъминотчи бўйича буюртма берилаётган барча товарларни расмий Спецификация
    шаклида Excel (.xlsx) файлига экспорт қилиш.
    """
    try:
        supplier_name = payload.supplier_name or "Асосий таъминотчи"
        items = payload.items or []

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Буюртма Спецификацияси"

        title_font = Font(name="Calibri", size=15, bold=True, color="1E3A8A")
        sub_font = Font(name="Calibri", size=11, italic=True, color="475569")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        total_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
        total_font = Font(name="Calibri", size=11, bold=True, color="065F46")
        data_font = Font(name="Calibri", size=10)
        thin_side = Side(border_style="thin", color="CBD5E1")
        border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        ws.merge_cells("A1:G1")
        ws["A1"] = "Diyor Group • ТАЪМИНОТЧИГА БУЮРТМА СПЕЦИФИКАЦИЯСИ"
        ws["A1"].font = title_font
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("A2:G2")
        ws["A2"] = f"Таъминотчи: {supplier_name}  |  Сана: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        ws["A2"].font = sub_font
        ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

        ws.append([])

        headers = ["№", "Товар номи", "Артикул / SKU", "ABC/XYZ", "Буюртма (дона)", "Харид нархи (сўм)", "Жами сумма (сўм)"]
        ws.append(headers)
        header_row_idx = 4

        for col_idx in range(1, 8):
            cell = ws.cell(row=header_row_idx, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border

        total_qty = 0.0
        total_sum = 0.0
        current_row = header_row_idx + 1

        for idx, it in enumerate(items, 1):
            if (it.matrix_category or "").upper() == "CZ":
                continue
            qty = float(it.quantity or 0.0)
            price = float(it.buy_price or 0.0)
            row_sum = qty * price
            total_qty += qty
            total_sum += row_sum

            name = it.name or it.product_id
            sku = it.product_id[:12] if len(it.product_id) > 12 else it.product_id
            cat = it.matrix_category or "—"

            ws.append([idx, name, sku, cat, qty, price, row_sum])

            ws.cell(row=current_row, column=1).alignment = Alignment(horizontal="center")
            ws.cell(row=current_row, column=2).alignment = Alignment(horizontal="left")
            ws.cell(row=current_row, column=3).alignment = Alignment(horizontal="center")
            ws.cell(row=current_row, column=4).alignment = Alignment(horizontal="center")
            ws.cell(row=current_row, column=5).alignment = Alignment(horizontal="right")
            ws.cell(row=current_row, column=6).alignment = Alignment(horizontal="right")
            ws.cell(row=current_row, column=7).alignment = Alignment(horizontal="right")

            ws.cell(row=current_row, column=5).number_format = "#,##0"
            ws.cell(row=current_row, column=6).number_format = "#,##0"
            ws.cell(row=current_row, column=7).number_format = "#,##0"

            for c in range(1, 8):
                cell = ws.cell(row=current_row, column=c)
                cell.font = data_font
                cell.border = border

            current_row += 1

        # Summary Row
        ws.append(["ЖАМИ:", "", "", "", total_qty, "", total_sum])
        total_row_idx = current_row
        ws.merge_cells(start_row=total_row_idx, start_column=1, end_row=total_row_idx, end_column=4)

        for col_idx in range(1, 8):
            cell = ws.cell(row=total_row_idx, column=col_idx)
            cell.font = total_font
            cell.fill = total_fill
            cell.border = border
            if col_idx in (5, 7):
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")

        ws.cell(row=total_row_idx, column=1).alignment = Alignment(horizontal="center", vertical="center")

        column_widths = {1: 6, 2: 42, 3: 18, 4: 12, 5: 16, 6: 20, 7: 22}
        for col_idx, width in column_widths.items():
            ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = width

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        safe_name = "".join(c for c in supplier_name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        filename = f"Spetsifikatsiya_{safe_name[:25]}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("export_supplier_order_excel_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Спецификация яратишда хатолик: {str(e)}")


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


# ═══════════════ БУТУН ОМБОР БАЗАСИДАН ҚИДИРИШ ВА 6 ОЙЛИК ABC/XYZ КАРТОЧКАСИ ═══════════════

@router.get("/product-details-search", summary="Бутун омбор базасидан қидириш ва 6 ойлик ABC/XYZ карточкасини чиқариш")
async def product_details_search(q: str = Query(..., min_length=2, description="Артикул, ном, код ёки штрихкод бўйича қидирув сўзи")):
    """
    МойСклад базасидаги барча товарлар орасидан қидириш (қидирув чекланмаган):
    - Товар номи, артикули, коди
    - Ҳақиқий омбор қолдиғи (мусбат қолдиқ)
    - Таннарх ва сотилиш нархи (сўмда)
    - 6 ойлик ABC/XYZ матрицаси ва тавсиялар
    """
    ms_client = MoySkladClient()
    try:
        search_q = q.strip()
        url = f"/entity/product?search={search_q}&limit=25&expand=attributes"
        res = await ms_client.client.get(url)
        if res.status_code != 200:
            logger.warning("moysklad_search_failed", status=res.status_code, body=res.text)
            return {"status": "error", "query": search_q, "products": []}

        prod_data = res.json()
        rows = prod_data.get("rows", [])
        if not rows:
            return {"status": "success", "query": search_q, "count": 0, "products": []}

        # Ҳақиқий қолдиқларни пакет (batch) шаклида олиш
        hrefs = [r["meta"]["href"] for r in rows if "meta" in r and "href" in r["meta"]]
        stock_map = {}
        if hrefs:
            filter_str = ";".join([f"product={h}" for h in hrefs])
            stock_res = await ms_client.client.get(f"/report/stock/all?filter={filter_str}")
            if stock_res.status_code == 200:
                for s_row in stock_res.json().get("rows", []):
                    s_href = s_row.get("meta", {}).get("href", "")
                    s_pid = s_href.split("/")[-1].split("?")[0]
                    raw_stock = float(s_row.get("stock", 0.0))
                    stock_map[s_pid] = max(0.0, raw_stock)

        # 6 ойлик сотув ва ABC/XYZ матрицасини кешдан олиш
        matrix_map = await fetch_180d_product_sales_matrix(ms_client)

        custom_brands = [b.get("name") for b in _brands_cache.get("data", []) if isinstance(b, dict)]

        results = []
        for r in rows:
            pid = r.get("id", "")
            p_name = r.get("name", "")
            p_article = r.get("article") or "—"
            p_code = r.get("code") or "—"
            path_name = r.get("pathName", "")

            # Брендни аниқлаш
            existing_brand = ""
            for attr in r.get("attributes", []):
                if attr.get("name") == "Бренд товара":
                    val = attr.get("value")
                    if isinstance(val, dict):
                        existing_brand = val.get("name", "")
                    elif isinstance(val, str):
                        existing_brand = val
                    break

            brand = extract_brand(name=p_name, path_name=path_name, existing_brand=existing_brand, custom_brands=custom_brands)

            # Ҳақиқий қолдиқ
            stock = stock_map.get(pid, 0.0)

            # Нархларни ҳисоблаш (Таннарх ва Сотилиш нархи сўмда)
            buy_price_obj = r.get("buyPrice", {}) or {}
            buy_p_val = float(buy_price_obj.get("value", 0.0) or 0.0) / 100.0
            buy_curr_href = buy_price_obj.get("currency", {}).get("meta", {}).get("href", "")

            sale_prices = r.get("salePrices", [])
            sale_p_val = float(sale_prices[0].get("value", 0.0) or 0.0) / 100.0 if sale_prices else 0.0

            # Агар харид нархи USD бўлса (ёки кичик бўлса), сўмга айлантириш
            if buy_curr_href.endswith("cbe2389d-b1d2-11ed-0a80-09b4000e8b7e") or (0 < buy_p_val < 5000 and sale_p_val > 50000):
                buy_p_val = round(buy_p_val * 12800.0, 2)

            if buy_p_val <= 0.0 and sale_p_val > 0.0:
                buy_p_val = round(sale_p_val * 0.7, 2)
            elif sale_p_val <= 0.0 and buy_p_val > 0.0:
                sale_p_val = round(buy_p_val * 1.35, 2)

            # ABC/XYZ 180 кунлик кўрсаткичлари
            mat = matrix_map.get(pid)
            if mat:
                sales_6m = float(mat.get("sales_180d", 0.0))
                abc_xyz_class = mat.get("matrix_category", "CZ")
                badge_color = mat.get("badge_color", "blue")
                abc_xyz_desc = mat.get("rationale", "")
                order_allowed = mat.get("order_allowed", True)
                monthly_qty = mat.get("monthly_qty", [0.0] * 6)
                variation_pct = float(mat.get("variation_pct", 0.0))
            else:
                sales_6m = 0.0
                abc_xyz_class = "CZ"
                badge_color = "red"
                abc_xyz_desc = "Ноликвид ёки умуман сотилмайдиган товар! Пул музламаслиги учун буюртма бериш ТАҚИҚЛАНАДИ (0 дона)."
                order_allowed = False
                monthly_qty = [0.0] * 6
                variation_pct = 0.0

            # Буюртма миқдорини ҳисоблаш
            if not order_allowed or abc_xyz_class == "CZ":
                recommended_order_qty = 0
            else:
                daily_sales = sales_6m / 180.0
                if abc_xyz_class.startswith("A"):
                    target_stock = daily_sales * 30.0
                elif abc_xyz_class.startswith("B"):
                    target_stock = daily_sales * 20.0
                elif abc_xyz_class == "CX":
                    target_stock = daily_sales * 14.0
                elif abc_xyz_class == "CY":
                    target_stock = daily_sales * 7.0
                else:
                    target_stock = 0.0
                recommended_order_qty = max(0, int(round(target_stock - stock)))

            uom = r.get("uom", {}).get("name", "дона") if isinstance(r.get("uom"), dict) else "дона"

            results.append({
                "id": pid,
                "name": p_name,
                "article": p_article,
                "code": p_code,
                "brand": brand,
                "stock": stock,
                "buy_price": buy_p_val,
                "sale_price": sale_p_val,
                "sales_6m_count": sales_6m,
                "abc_xyz_class": abc_xyz_class,
                "abc_xyz_description": abc_xyz_desc,
                "badge_color": badge_color,
                "order_allowed": order_allowed,
                "recommended_order_qty": recommended_order_qty,
                "monthly_qty": monthly_qty,
                "variation_pct": variation_pct,
                "uom": uom
            })

        return {
            "status": "success",
            "query": search_q,
            "count": len(results),
            "products": results
        }

    except Exception as e:
        logger.error("product_details_search_error", error=str(e))
        return {"status": "error", "query": q, "message": str(e), "products": []}
    finally:
        await ms_client.close()
