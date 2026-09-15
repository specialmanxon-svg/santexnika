import structlog
from typing import Optional
from sqlalchemy import select
from core.database import async_session_factory
from services.moysklad_client import MoySkladClient
from services.bitrix_client import BitrixClient
from services.telegram_notifier import TelegramNotifier
from models import ProductMDM, AuditLog
from core.exceptions import ValidationError

logger = structlog.get_logger(__name__)

async def calculate_designer_commission(order_href: str) -> Optional[float]:
    """
    Implements designer commission logic (Scenario 4.2).
    1. Fetch MS Order
    2. Check state 'Paid'
    3. Find Bitrix Deal via UF_MS_ORDER_ID
    4. Calculate Gross Margin (Retail - Purchase)
    5. Commission = Gross Margin * 7%
    6. Update Deal & Notify Designer
    """
    logger.info("commission_calculation_started", order_href=order_href)
    
    ms_client = MoySkladClient()
    bx_client = BitrixClient()
    
    order = await ms_client.get(order_href.replace('https://api.moysklad.ru/api/remap/1.2/', ''))
    
    # Check State
    state_meta = order.get('state', {}).get('meta', {}).get('href', '')
    state = await ms_client.get(state_meta.replace('https://api.moysklad.ru/api/remap/1.2/', ''))
    if state.get('name') != 'Оплачен':
        logger.info("order_not_paid", order_href=order_href, state=state.get('name'))
        return None
        
    order_id = order.get('id')
    
    # Find Bitrix Deal
    # Note: Bitrix REST API filter
    deals = await bx_client.call_api('crm.deal.list', {
        'filter': {'UF_MS_ORDER_ID': order_id},
        'select': ['ID', 'TITLE', 'UF_DESIGNER_ID']
    })
    
    if not deals:
        logger.warning("deal_not_found_for_order", order_id=order_id)
        return None
        
    deal = deals[0]
    designer_id = deal.get('UF_DESIGNER_ID')
    if not designer_id:
        logger.info("no_designer_for_deal", deal_id=deal.get('ID'))
        return None
        
    # Get Order Positions
    positions_href = order.get('positions', {}).get('meta', {}).get('href', '')
    positions_data = await ms_client.get(positions_href.replace('https://api.moysklad.ru/api/remap/1.2/', ''))
    positions = positions_data.get('rows', [])
    
    gross_margin = 0.0
    
    async with async_session_factory() as session:
        for pos in positions:
            assortment_href = pos.get('assortment', {}).get('meta', {}).get('href', '')
            product_id = assortment_href.split('/')[-1]
            
            stmt = select(ProductMDM).where(ProductMDM.ms_id == product_id)
            result = await session.execute(stmt)
            mdm_product = result.scalars().first()
            
            if mdm_product:
                retail_price = (pos.get('price', 0) / 100) * pos.get('quantity', 0)
                purchase_price = mdm_product.purchase_price * pos.get('quantity', 0)
                gross_margin += (retail_price - purchase_price)
                
    if gross_margin <= 0:
        logger.warning("zero_or_negative_margin", deal_id=deal.get('ID'), margin=gross_margin)
        return 0.0
        
    commission = gross_margin * 0.07
    
    # Update Bitrix Deal
    await bx_client.call_api('crm.deal.update', {
        'id': deal.get('ID'),
        'fields': {
            'UF_DESIGNER_COMMISSION': commission
        }
    })
    
    # Notify designer
    # In real world, look up designer's telegram ID based on Bitrix ID mapping
    designer_telegram_id = f"bitrix_{designer_id}" # Placeholder mapping
    notifier = TelegramNotifier()
    await notifier.notify_designer_commission(designer_telegram_id, deal.get('TITLE', ''), commission)
    
    # Audit log
    async with async_session_factory() as session:
        log = AuditLog(
            entity_type='deal',
            entity_id=str(deal.get('ID')),
            action='COMMISSION_CALCULATED',
            details={'gross_margin': gross_margin, 'commission': commission}
        )
        session.add(log)
        await session.commit()
        
    logger.info("commission_calculation_completed", deal_id=deal.get('ID'), commission=commission)
    return commission
