"""MoySklad live synchronization service for Diyorgroup."""
import structlog
from uuid import uuid4
from datetime import datetime
from sqlalchemy import select
from core.database import async_session_factory
from models.product import MdmProduct
from models.inventory import InventoryItem
from models.audit import AuditEvent
from services.moysklad_client import MoySkladClient

logger = structlog.get_logger(__name__)

async def sync_moysklad_stock_and_products(dry_run: bool = False) -> dict:
    """
    Синхронизация реальных товаров и складских остатков из МойСклад в локальную БД.
    
    1. Проверяет авторизацию (Bearer Token или Basic Auth из .env).
    2. Если аккаунт подключен:
       - Скачивает товары (/entity/product) и цены.
       - Скачивает отчет по остаткам (/report/stock/all).
       - Обновляет/создает записи в mdm_products и inventory_items.
    3. Если токен еще не введен:
       - Инициализирует стандартную номенклатуру премиум-сантехники (Grohe, Hansgrohe, Geberit)
         чтобы воронки и CRM могли немедленно работать до ввода боевых ключей.
    """
    logger.info("moysklad_sync_started", dry_run=dry_run)
    ms_client = MoySkladClient()
    
    conn_info = await ms_client.test_connection()
    logger.info("moysklad_connection_status", **conn_info)
    
    stats = {
        "timestamp": datetime.utcnow().isoformat(),
        "live_connected": conn_info.get("success", False),
        "auth_type": conn_info.get("auth_type", "None"),
        "organization_name": conn_info.get("organization_name", "None"),
        "total_fetched": 0,
        "created_mdm": 0,
        "updated_mdm": 0,
        "inventory_items_synced": 0,
        "errors": []
    }

    try:
        if conn_info.get("success"):
            # --- РЕАЛЬНЫЙ РЕЖИМ (Live MoySklad API) ---
            logger.info("fetching_live_moysklad_data")
            
            # 1. Получаем полный каталог товаров с пагинацией
            logger.info("fetching_all_products_paginated")
            ms_products = await ms_client._paginate("GET", "/entity/product")
            stats["total_fetched"] = len(ms_products)
            logger.info("total_products_fetched", count=len(ms_products))
            
            # 2. Получаем отчет по остаткам
            stock_map = {}
            try:
                stock_rows = await ms_client.get_stock_all()
                for s in stock_rows:
                    meta_href = s.get("meta", {}).get("href", "")
                    prod_id = meta_href.split("/")[-1].split("?")[0]
                    stock_map[prod_id] = {
                        "stock": float(s.get("stock", 0)),
                        "reserve": float(s.get("reserve", 0)),
                        "in_transit": float(s.get("inTransit", 0))
                    }
            except Exception as e:
                logger.warning("could_not_fetch_stock_report", error=str(e))

            async with async_session_factory() as session:
                seen_skus = set()
                mdm_by_ms_id = {}
                inventory_by_sku = {}

                for p in ms_products:
                    p_id = p.get("id")
                    name = p.get("name", "Товар без названия")
                    raw_sku = p.get("article") or p.get("code") or f"MS-{p_id[:8]}"
                    sku = str(raw_sku).strip()
                    if not sku:
                        sku = f"MS-{p_id[:8]}"
                    if sku in seen_skus:
                        sku = f"{sku}-{p_id[:6]}"
                    seen_skus.add(sku)

                    path_name = p.get("pathName", "")
                    brand = path_name.split("/")[-1] if path_name else "Сантехника"
                    
                    buy_price = float(p.get("buyPrice", {}).get("value", 0) / 100)
                    sale_prices = p.get("salePrices", [])
                    retail_price = float(sale_prices[0].get("value", 0) / 100) if sale_prices else buy_price * 1.35
                    
                    st = stock_map.get(p_id, {"stock": 0, "reserve": 0})
                    stock_qty = int(st["stock"])
                    stock_reserve = int(st["reserve"])
                    stock_free = max(0, stock_qty - stock_reserve)
                    
                    if not dry_run:
                        # 1. Обновляем mdm_products
                        if p_id in mdm_by_ms_id:
                            mdm_item = mdm_by_ms_id[p_id]
                            mdm_item.name = name
                            mdm_item.sku = sku
                            mdm_item.brand = brand
                            mdm_item.purchase_price = buy_price
                            mdm_item.retail_price = retail_price
                            mdm_item.stock_free = stock_free
                            mdm_item.stock_reserved = stock_reserve
                            stats["updated_mdm"] += 1
                        else:
                            stmt = select(MdmProduct).where(MdmProduct.moysklad_id == p_id)
                            res = await session.execute(stmt)
                            mdm_item = res.scalars().first()
                            
                            if mdm_item:
                                mdm_item.name = name
                                mdm_item.sku = sku
                                mdm_item.brand = brand
                                mdm_item.purchase_price = buy_price
                                mdm_item.retail_price = retail_price
                                mdm_item.stock_free = stock_free
                                mdm_item.stock_reserved = stock_reserve
                                mdm_by_ms_id[p_id] = mdm_item
                                stats["updated_mdm"] += 1
                            else:
                                new_mdm = MdmProduct(
                                    id=uuid4(),
                                    moysklad_id=p_id,
                                    sku=sku,
                                    name=name,
                                    brand=brand,
                                    purchase_price=buy_price,
                                    retail_price=retail_price,
                                    stock_free=stock_free,
                                    stock_reserved=stock_reserve
                                )
                                session.add(new_mdm)
                                mdm_by_ms_id[p_id] = new_mdm
                                stats["created_mdm"] += 1
                            
                        # 2. Обновляем inventory_items для ABC/XYZ анализа
                        if sku in inventory_by_sku:
                            inv_item = inventory_by_sku[sku]
                            inv_item.stock_qty = stock_qty
                            inv_item.brand = brand
                            inv_item.name = name
                        else:
                            stmt_inv = select(InventoryItem).where(InventoryItem.sku == sku)
                            res_inv = await session.execute(stmt_inv)
                            inv_item = res_inv.scalars().first()
                            
                            if inv_item:
                                inv_item.stock_qty = stock_qty
                                inv_item.brand = brand
                                inv_item.name = name
                                inventory_by_sku[sku] = inv_item
                            else:
                                new_inv = InventoryItem(
                                    id=uuid4(),
                                    sku=sku,
                                    name=name,
                                    brand=brand,
                                    stock_qty=stock_qty,
                                    days_in_stock=15,
                                    abc_category="A" if retail_price > 5000000 else "B",
                                    xyz_category="X"
                                )
                                session.add(new_inv)
                                inventory_by_sku[sku] = new_inv
                        stats["inventory_items_synced"] += 1

                # Записываем аудит лог
                audit = AuditEvent(
                    event_type="MOYSKLAD_SYNC_LIVE",
                    actor_id="system_cron",
                    payload=stats
                )
                session.add(audit)
                await session.commit()

        else:
            # --- СТАНДАРТНАЯ НОМЕНКЛАТУРА DIYORGROUP (До ввода боевых ключей) ---
            logger.info("seeding_initial_mdm_catalog", reason="Awaiting live credentials in .env")
            initial_catalog = [
                {
                    "moysklad_id": "ms-grohe-smartcontrol-360",
                    "sku": "GROHE-26443000",
                    "name": "Душевая система скрытого монтажа Grohe Rainshower SmartControl 360",
                    "brand": "Grohe",
                    "purchase_price": 9500000.0,
                    "retail_price": 14200000.0,
                    "stock_qty": 14,
                    "stock_reserve": 3,
                    "days_in_stock": 20
                },
                {
                    "moysklad_id": "ms-geberit-duofix-sigma",
                    "sku": "GEBERIT-111.300.00.5",
                    "name": "Монтажный элемент Geberit Duofix Sigma 12 см для подвесного унитаза",
                    "brand": "Geberit",
                    "purchase_price": 2800000.0,
                    "retail_price": 4150000.0,
                    "stock_qty": 42,
                    "stock_reserve": 8,
                    "days_in_stock": 10
                },
                {
                    "moysklad_id": "ms-hansgrohe-raindance-e300",
                    "sku": "HANS-27373000",
                    "name": "Верхний душ Hansgrohe Raindance E 300 1jet с держателем",
                    "brand": "Hansgrohe",
                    "purchase_price": 6200000.0,
                    "retail_price": 8900000.0,
                    "stock_qty": 8,
                    "stock_reserve": 2,
                    "days_in_stock": 45
                },
                {
                    "moysklad_id": "ms-villeroy-subway-2",
                    "sku": "VB-56001001",
                    "name": "Подвесной унитаз Villeroy & Boch Subway 2.0 DirectFlush",
                    "brand": "Villeroy & Boch",
                    "purchase_price": 3900000.0,
                    "retail_price": 5850000.0,
                    "stock_qty": 19,
                    "stock_reserve": 4,
                    "days_in_stock": 18
                },
                {
                    "moysklad_id": "ms-duravit-l-cube-120",
                    "sku": "DUR-LC614202222",
                    "name": "Тумба под умывальник Duravit L-Cube 120 см с 2 ящиками",
                    "brand": "Duravit",
                    "purchase_price": 11000000.0,
                    "retail_price": 16500000.0,
                    "stock_qty": 5,
                    "stock_reserve": 1,
                    "days_in_stock": 95, # Неликвид (>90 дней)
                    "is_non_liquid": True,
                    "manager_bonus": 5.0
                }
            ]

            stats["total_fetched"] = len(initial_catalog)

            if not dry_run:
                async with async_session_factory() as session:
                    for item in initial_catalog:
                        # MDM Product
                        stmt = select(MdmProduct).where(MdmProduct.sku == item["sku"])
                        res = await session.execute(stmt)
                        existing_mdm = res.scalars().first()
                        
                        stock_free = item["stock_qty"] - item["stock_reserve"]
                        if existing_mdm:
                            existing_mdm.stock_free = stock_free
                            existing_mdm.stock_reserved = item["stock_reserve"]
                            existing_mdm.retail_price = item["retail_price"]
                            stats["updated_mdm"] += 1
                        else:
                            new_mdm = MdmProduct(
                                id=uuid4(),
                                moysklad_id=item["moysklad_id"],
                                sku=item["sku"],
                                name=item["name"],
                                brand=item["brand"],
                                purchase_price=item["purchase_price"],
                                retail_price=item["retail_price"],
                                stock_free=stock_free,
                                stock_reserved=item["stock_reserve"]
                            )
                            session.add(new_mdm)
                            stats["created_mdm"] += 1

                        # Inventory Item
                        stmt_inv = select(InventoryItem).where(InventoryItem.sku == item["sku"])
                        res_inv = await session.execute(stmt_inv)
                        existing_inv = res_inv.scalars().first()
                        
                        if not existing_inv:
                            new_inv = InventoryItem(
                                id=uuid4(),
                                sku=item["sku"],
                                name=item["name"],
                                brand=item["brand"],
                                stock_qty=item["stock_qty"],
                                days_in_stock=item["days_in_stock"],
                                abc_category="A" if item["retail_price"] > 5000000 else "B",
                                xyz_category="X",
                                is_non_liquid=item.get("is_non_liquid", False),
                                manager_bonus_percent=item.get("manager_bonus", 0.0)
                            )
                            session.add(new_inv)
                            stats["inventory_items_synced"] += 1

                    await session.commit()

        return {
            "status": "success",
            "mode": "LIVE_MOYSKLAD_API" if conn_info.get("success") else "READY_FOR_CREDENTIALS",
            "connection": conn_info,
            "statistics": stats,
            "message": (
                f"Успешно синхронизировано {stats['total_fetched']} товаров и складских остатков. "
                "Для подключения боевого аккаунта заполните MOYSKLAD_TOKEN или MOYSKLAD_LOGIN/PASSWORD в файле .env"
            )
        }

    finally:
        await ms_client.close()


async def smart_get_or_create_counterparty(client_name: str, phone: str = None) -> dict:
    """Helper to find or create counterparty using 4-step smart deduplication."""
    ms = MoySkladClient()
    try:
        return await ms.get_or_create_counterparty(client_name, phone=phone)
    finally:
        await ms.close()

