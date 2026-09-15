import asyncio
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from workers.celery_app import celery_app
from core.database import async_session_factory
from services.moysklad_client import MoySkladClient
from models import ProductMDM

logger = structlog.get_logger(__name__)

async def process_sync_products():
    logger.info("sync_products_started")
    ms_client = MoySkladClient()
    
    # Fetch products and stock (dummy combined approach, reality requires joining assortment/stock)
    assortment = await ms_client.get('entity/assortment')
    
    new_cnt, updated_cnt = 0, 0
    
    async with async_session_factory() as session:
        for item in assortment.get('rows', []):
            ms_id = item.get('id')
            name = item.get('name')
            article = item.get('article', '')
            # Extract prices (assuming MS salePrices structure)
            prices = item.get('salePrices', [])
            retail_price = 0.0
            purchase_price = (item.get('buyPrice', {}).get('value', 0)) / 100
            
            for p in prices:
                if p.get('priceType', {}).get('name') == 'Цена продажи':
                    retail_price = p.get('value', 0) / 100
                    
            stock = item.get('stock', 0)
            reserve = item.get('reserve', 0)
            stock_free = stock - reserve
            
            stmt = insert(ProductMDM).values(
                ms_id=ms_id,
                name=name,
                sku=article,
                retail_price=retail_price,
                purchase_price=purchase_price,
                stock_total=stock,
                stock_reserved=reserve,
                stock_free=stock_free
            ).on_conflict_do_update(
                index_elements=['ms_id'],
                set_={
                    'name': name,
                    'sku': article,
                    'retail_price': retail_price,
                    'purchase_price': purchase_price,
                    'stock_total': stock,
                    'stock_reserved': reserve,
                    'stock_free': stock_free
                }
            )
            
            await session.execute(stmt)
            updated_cnt += 1 # In real scenario, track new vs updated accurately
            
        await session.commit()
        
    logger.info("sync_products_completed", processed=updated_cnt)

@celery_app.task(bind=True)
def sync_moysklad_products(self):
    asyncio.run(process_sync_products())
