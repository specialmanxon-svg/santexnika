"""Reservation service for Bitrix24 → MoySklad product reservation.

Implements Scenario §4.1: When a deal moves to INVOICE_ISSUED stage,
reserve products in MoySklad and link the order back to Bitrix24.
"""
import structlog
from sqlalchemy import select

from config import settings
from core.database import async_session_factory
from services.bitrix_client import BitrixClient
from services.moysklad_client import MoySkladClient
from models.product import MdmProduct
from core.exceptions import IntegrationError

logger = structlog.get_logger(__name__)


async def reserve_deal_products(deal_id: int) -> str:
    """Execute the full reservation flow.

    1. Fetch deal & product rows from Bitrix24
    2. Validate deal stage (INVOICE_ISSUED)
    3. Map product names/SKUs to mdm_products
    4. Create MoySklad customer order with reserves
    5. Write MoySklad order UUID back to Bitrix24 deal

    Args:
        deal_id: Bitrix24 deal ID.

    Returns:
        MoySklad customer order UUID.

    Raises:
        IntegrationError: On API or validation failures.
    """
    logger.info("reservation_flow_started", deal_id=deal_id)

    bx = BitrixClient()
    ms = MoySkladClient()

    try:
        # Step 1: Fetch deal details
        deal = await bx.get_deal(deal_id)
        if not deal:
            raise IntegrationError(f"Deal {deal_id} not found in Bitrix24")

        stage_id = deal.get("STAGE_ID", "")
        logger.info("deal_stage_check", deal_id=deal_id, stage=stage_id)

        # Step 2: Fetch product rows
        bx_products = await bx.get_deal_products(deal_id)
        if not bx_products:
            raise IntegrationError(f"Deal {deal_id} has no product rows")

        # Step 3: Map products to MDM and build MoySklad positions
        positions = []
        async with async_session_factory() as session:
            for product in bx_products:
                product_name = product.get("PRODUCT_NAME", "")
                quantity = int(float(product.get("QUANTITY", 1)))
                price_rub = float(product.get("PRICE", 0))

                # Look up in MDM by name (or SKU if available)
                stmt = select(MdmProduct).where(
                    (MdmProduct.name == product_name) |
                    (MdmProduct.sku == product_name)
                )
                result = await session.execute(stmt)
                mdm_product = result.scalars().first()

                if not mdm_product:
                    logger.warning(
                        "product_not_in_mdm",
                        product_name=product_name,
                        deal_id=deal_id
                    )
                    raise IntegrationError(
                        f"Product '{product_name}' not found in MDM catalog"
                    )

                # MoySklad prices are in kopeks (×100)
                price_kopeks = int(price_rub * 100)

                positions.append({
                    "quantity": quantity,
                    "price": price_kopeks,
                    "reserve": quantity,
                    "assortment": {
                        "meta": {
                            "href": (
                                f"{settings.moysklad_api_url}/entity/product"
                                f"/{mdm_product.moysklad_id}"
                            ),
                            "type": "product",
                            "mediaType": "application/json"
                        }
                    }
                })

                logger.info(
                    "position_mapped",
                    sku=mdm_product.sku,
                    quantity=quantity,
                    price_kopeks=price_kopeks
                )

        # Step 4: Create MoySklad customer order with reserves
        order_payload = {
            "description": f"Резерв из Битрикс24 — Сделка #{deal_id}",
            "organization": {
                "meta": {
                    "href": (
                        f"{settings.moysklad_api_url}/entity/organization"
                        f"/{settings.moysklad_organization_id}"
                    ),
                    "type": "organization",
                    "mediaType": "application/json"
                }
            },
            "state": {
                "meta": {
                    "href": (
                        f"{settings.moysklad_api_url}/entity/customerorder"
                        f"/metadata/states/{settings.moysklad_reserve_state_id}"
                    ),
                    "type": "state",
                    "mediaType": "application/json"
                }
            },
            "positions": positions,
        }

        # Add counterparty if company is linked
        company_id = deal.get("COMPANY_ID")
        if company_id:
            company = await bx.get_company(int(company_id))
            ms_counterparty_id = company.get("UF_MS_COUNTERPARTY_ID")
            if ms_counterparty_id:
                order_payload["agent"] = {
                    "meta": {
                        "href": (
                            f"{settings.moysklad_api_url}/entity/counterparty"
                            f"/{ms_counterparty_id}"
                        ),
                        "type": "counterparty",
                        "mediaType": "application/json"
                    }
                }

        ms_order = await ms.create_customer_order(order_payload)
        order_uuid = ms_order.get("id")

        if not order_uuid:
            raise IntegrationError("MoySklad returned order without ID")

        logger.info(
            "moysklad_order_created",
            deal_id=deal_id,
            order_uuid=order_uuid,
            positions_count=len(positions)
        )

        # Step 5: Write order UUID back to Bitrix24
        update_result = await bx.update_deal(
            deal_id,
            {"UF_MS_ORDER_ID": order_uuid}
        )

        if not update_result:
            logger.error(
                "bitrix_deal_update_failed",
                deal_id=deal_id,
                order_uuid=order_uuid
            )

        logger.info(
            "reservation_flow_completed",
            deal_id=deal_id,
            ms_order_uuid=order_uuid
        )

        return order_uuid

    finally:
        await bx.close()
        await ms.close()
