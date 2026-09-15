"""Designer commission calculation service for Diyorgroup Native CRM."""
import structlog
from typing import Optional
from sqlalchemy import select
from core.database import async_session_factory
from services.moysklad_client import MoySkladClient
from services.diyor_crm_client import DiyorCrmClient
from services.telegram_notifier import TelegramNotifier
from models import ProductMDM, AuditLog, CrmDeal
from core.exceptions import ValidationError

logger = structlog.get_logger(__name__)

async def calculate_designer_commission(order_href: str) -> Optional[float]:
    """
    Implements designer commission logic (Scenario 4.2).
    1. Fetch MoySklad Order
    2. Check state 'Paid'
    3. Find Diyorgroup CRM Deal via moysklad_order_id
    4. Calculate Gross Margin (Retail - Purchase)
    5. Commission = Gross Margin * 7%
    6. Update CrmDeal & Notify Designer via Telegram
    """
    logger.info("commission_calculation_started", order_href=order_href)
    
    ms_client = MoySkladClient()
    crm_client = DiyorCrmClient()
    
    order = await ms_client.get(order_href.replace('https://api.moysklad.ru/api/remap/1.2/', ''))
    
    # Check State
    state_meta = order.get('state', {}).get('meta', {}).get('href', '')
    state = await ms_client.get(state_meta.replace('https://api.moysklad.ru/api/remap/1.2/', ''))
    if state.get('name') != 'Оплачен':
        logger.info("order_not_paid", order_href=order_href, state=state.get('name'))
        return None
        
    order_id = order.get('id')
    
    # Find Deal in Diyorgroup CRM database
    deal = None
    async with async_session_factory() as session:
        stmt = select(CrmDeal).where(CrmDeal.moysklad_order_id == order_id)
        result = await session.execute(stmt)
        deal = result.scalars().first()
        
        if not deal:
            # Fallback by order_id prefix
            stmt = select(CrmDeal).where(CrmDeal.moysklad_order_id.ilike(f"%{order_id}%"))
            result = await session.execute(stmt)
            deal = result.scalars().first()
            
        if not deal:
            logger.warning("deal_not_found_for_order", order_id=order_id)
            return None
            
        designer_username = deal.designer_username
        if not designer_username:
            logger.info("no_designer_for_deal", deal_id=str(deal.id))
            return None
            
        # Get Order Positions
        positions_href = order.get('positions', {}).get('meta', {}).get('href', '')
        positions_data = await ms_client.get(positions_href.replace('https://api.moysklad.ru/api/remap/1.2/', ''))
        positions = positions_data.get('rows', [])
        
        gross_margin = 0.0
        for pos in positions:
            assortment_href = pos.get('assortment', {}).get('meta', {}).get('href', '')
            product_id = assortment_href.split('/')[-1]
            
            stmt_prod = select(ProductMDM).where(ProductMDM.moysklad_id == product_id)
            res_prod = await session.execute(stmt_prod)
            mdm_product = res_prod.scalars().first()
            
            if mdm_product:
                retail_price = (pos.get('price', 0) / 100) * pos.get('quantity', 0)
                purchase_price = float(mdm_product.purchase_price) * pos.get('quantity', 0)
                gross_margin += (retail_price - purchase_price)
                
        # If no MDM match, calculate 7% based on deal total amount
        if gross_margin <= 0:
            gross_margin = deal.total_amount
            
        commission = round(gross_margin * 0.07, 2)
        
        # Update Diyorgroup CRM Deal
        deal.designer_commission = commission
        
        # Audit log
        log = AuditLog(
            event_type='COMMISSION_CALCULATED',
            actor_id='system',
            payload={'deal_id': str(deal.id), 'gross_margin': gross_margin, 'commission': commission, 'designer': designer_username}
        )
        session.add(log)
        await session.commit()
        
    # Notify designer
    notifier = TelegramNotifier()
    await notifier.notify_designer_commission(designer_username, deal.title, commission)
        
    logger.info("commission_calculation_completed", deal_id=str(deal.id), commission=commission)
    return commission
