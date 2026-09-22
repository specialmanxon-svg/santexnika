"""Designer commission calculation service for Diyorgroup Native CRM."""
import structlog
from typing import Optional
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import async_session_factory
from services.moysklad_client import MoySkladClient
from services.telegram_notifier import TelegramNotifier
from models.product import MdmProduct
from models.audit import AuditEvent
from models.crm import CrmDeal

logger = structlog.get_logger(__name__)

async def calculate_designer_commission(deal_id: str, session: AsyncSession = None) -> Optional[dict]:
    """
    Calculate 5% referral bonus when deal reaches PAID stage.
    
    Steps:
    1. Find the deal by ID in CrmDeal table
    2. Check designer_username (referral) exists
    3. If deal has moysklad_order_id, try to get positions from MoySklad for exact gross margin
    4. Fallback: commission = deal.total_amount * 0.05
    5. Update deal.designer_commission
    6. Send Telegram notification to referral and CEO
    7. Log audit event
    """
    own_session = False
    if session is None:
        session = async_session_factory()
        own_session = True
    
    try:
        # Find deal
        from uuid import UUID
        try:
            deal_uuid = UUID(deal_id)
            stmt = select(CrmDeal).where(CrmDeal.id == deal_uuid)
        except ValueError:
            stmt = select(CrmDeal).where(CrmDeal.title.ilike(f"%{deal_id}%"))
        
        result = await session.execute(stmt)
        deal = result.scalars().first()
        
        if not deal:
            logger.warning("deal_not_found", deal_id=deal_id)
            return None
        
        if not deal.designer_username:
            logger.info("no_referral_for_deal", deal_id=deal_id)
            return None
        
        # Determine strict order total amount
        total_order_sum = float(deal.total_amount or 0.0)
        if total_order_sum <= 0 and deal.items:
            total_order_sum = sum(float(it.get("price", 0.0)) * float(it.get("quantity", 1)) for it in deal.items)

        gross_margin = 0.0
        ms_method = "order_cart_margin"
        
        # 1. Try to fetch positions from MoySklad customer order if moysklad_order_id exists
        positions = []
        currency_rates = {}
        ms_client = MoySkladClient()
        try:
            if await ms_client.is_configured():
                currency_rates = await ms_client.get_currency_rates()
                if deal.moysklad_order_id:
                    order = None
                    # If UUID (36 chars with dashes)
                    if len(deal.moysklad_order_id) == 36 and "-" in deal.moysklad_order_id:
                        try:
                            order = await ms_client._request("GET", f"/entity/customerorder/{deal.moysklad_order_id}")
                        except Exception:
                            order = None
                    
                    # If not found by UUID or if stored as an order name
                    if not order:
                        orders_resp = await ms_client._request("GET", "/entity/customerorder", 
                            params={"filter": f"name={deal.moysklad_order_id}", "order": "moment,desc", "limit": 10})
                        rows = orders_resp.get("rows", [])
                        for r in rows:
                            r_sum = float(r.get("sum", 0.0)) / 100
                            if abs(r_sum - total_order_sum) < 1.0 or (deal.counterparty_name and deal.counterparty_name in (r.get("description") or "")):
                                order = r
                                break
                    
                    if order:
                        positions_href = order.get("positions", {}).get("meta", {}).get("href", "")
                        if positions_href:
                            endpoint = positions_href.replace("https://api.moysklad.ru/api/remap/1.2", "")
                            positions_data = await ms_client.get(endpoint)
                            positions = positions_data.get("rows", [])
        except Exception as e:
            logger.warning("moysklad_order_fetch_failed", error=str(e))

        total_cost = 0.0
        # 2. Calculate per-position margin: (Sale Price - Cost Price) * Quantity
        if positions:
            for pos in positions:
                sale_price = float(pos.get("price", 0.0)) / 100  # tiyins to UZS
                quantity = float(pos.get("quantity", 1.0))
                
                # Fetch product details for true purchase price and currency
                cost_uzs = 0.0
                assortment_href = pos.get("assortment", {}).get("meta", {}).get("href", "")
                product_id = assortment_href.split("/")[-1].split("?")[0]
                
                try:
                    prod_data = await ms_client._request("GET", f"/entity/product/{product_id}")
                    bp = prod_data.get("buyPrice", {})
                    val = float(bp.get("value", 0.0)) / 100
                    curr_href = bp.get("currency", {}).get("meta", {}).get("href", "")
                    curr_id = curr_href.split("/")[-1] if curr_href else ""
                    rate = currency_rates.get(curr_href) or currency_rates.get(curr_id) or 1.0
                    if val > 0:
                        cost_uzs = val * rate
                except Exception:
                    cost_uzs = 0.0
                
                # Fallback to MDM purchase_price if MoySklad buyPrice was 0
                if cost_uzs <= 0:
                    stmt_prod = select(MdmProduct).where(MdmProduct.moysklad_id == product_id)
                    res_prod = await session.execute(stmt_prod)
                    mdm_product = res_prod.scalars().first()
                    if mdm_product and float(mdm_product.purchase_price) > 0:
                        cost_uzs = float(mdm_product.purchase_price)
                
                total_cost += cost_uzs * quantity
                if cost_uzs > 0:
                    gross_margin += max(0.0, sale_price - cost_uzs) * quantity
                else:
                    gross_margin += sale_price * quantity
        elif deal.items:
            # Fallback to deal.items from database
            for item in deal.items:
                sale_price = float(item.get("price", 0.0))
                quantity = float(item.get("quantity", 1.0))
                cost_uzs = float(item.get("purchase_price", 0.0))
                
                # If product moysklad_id is present, try to get real buyPrice from MoySklad
                moysklad_id = item.get("moysklad_id")
                if cost_uzs <= 0 and moysklad_id:
                    try:
                        prod_data = await ms_client._request("GET", f"/entity/product/{moysklad_id}")
                        bp = prod_data.get("buyPrice", {})
                        val = float(bp.get("value", 0.0)) / 100
                        curr_href = bp.get("currency", {}).get("meta", {}).get("href", "")
                        curr_id = curr_href.split("/")[-1] if curr_href else ""
                        rate = currency_rates.get(curr_href) or currency_rates.get(curr_id) or 1.0
                        if val > 0:
                            cost_uzs = val * rate
                    except Exception:
                        pass

                total_cost += cost_uzs * quantity
                if cost_uzs > 0:
                    gross_margin += max(0.0, sale_price - cost_uzs) * quantity
                else:
                    gross_margin += sale_price * quantity
        else:
            gross_margin = total_order_sum
            ms_method = "fallback_total"

        try:
            await ms_client.close()
        except Exception:
            pass

        if total_cost > 0:
            ms_method = "exact_cost_margin"

        # STRICT SAFETY RULE: Gross margin can NEVER exceed total order sum
        if total_order_sum > 0:
            gross_margin = min(gross_margin, total_order_sum)
        if gross_margin < 0:
            gross_margin = 0.0
        elif gross_margin == 0 and total_cost == 0:
            gross_margin = total_order_sum
            ms_method = "fallback_total"
        
        # USER REQUIREMENT: Calculate 5% referral bonus strictly from total sale price (сотув нархидан 5%)
        commission = round(total_order_sum * 0.05, 2)
        ms_method = "sale_amount_5pct"
        
        # Update deal
        deal.designer_commission = commission
        
        # Audit log
        audit = AuditEvent(
            id=uuid4(),
            event_type='COMMISSION_CALCULATED',
            actor_id='system',
            payload={
                'deal_id': str(deal.id),
                'deal_title': deal.title,
                'total_order_sum': total_order_sum,
                'total_cost': total_cost,
                'gross_margin': gross_margin,
                'commission': commission,
                'referral': deal.designer_username,
                'method': ms_method
            }
        )
        session.add(audit)
        await session.commit()
        
        # Send Telegram notifications
        notifier = TelegramNotifier()
        
        # To referral
        await notifier.notify_designer_commission(
            deal.designer_username, deal.title, commission, total_amount=total_order_sum
        )
        
        # To CEO
        ceo_text = (
            f"💰 <b>Бонус Реферала начислен</b>\n\n"
            f"Сделка: <i>{deal.title}</i>\n"
            f"Реферал: {deal.designer_username}\n"
            f"Савдо суммаси: <b>{total_order_sum:,.0f} сўм</b>\n"
            f"Бонус Реферала (сотувдан 5%): <b>{commission:,.0f} сўм</b>\n"
        )
        if total_cost > 0:
            ceo_text += (
                f"\n<i>Маълумот учун (МойСклад):</i>\n"
                f"Таннарх: <b>{total_cost:,.0f} сўм</b>\n"
                f"Соф маржа: <b>{gross_margin:,.0f} сўм</b>\n"
            )
        ceo_text += f"\nМетод: {ms_method}"
        await notifier.send_ceo_message(ceo_text)
        
        logger.info("referral_bonus_calculated", deal_id=str(deal.id), commission=commission, method=ms_method)
        
        return {
            "deal_id": str(deal.id),
            "deal_title": deal.title,
            "referral": deal.designer_username,
            "gross_margin": gross_margin,
            "commission": commission,
            "method": ms_method
        }
    
    finally:
        if own_session:
            await session.close()
