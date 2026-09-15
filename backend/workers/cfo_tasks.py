import asyncio
import structlog
from datetime import datetime, timezone
from sqlalchemy import select
from workers.celery_app import celery_app
from core.database import async_session_factory
from services.moysklad_client import MoySkladClient
from services.bitrix_client import BitrixClient
from services.telegram_notifier import TelegramNotifier
from models import DebtRegistry, AuditLog

logger = structlog.get_logger(__name__)

async def process_cfo_debt_check():
    logger.info("cfo_debt_check_started")
    ms_client = MoySkladClient()
    bx_client = BitrixClient()
    notifier = TelegramNotifier()
    
    # 1. Fetch counterparty reports (dummy URL for report, adapt to real MS report API)
    report_data = await ms_client.get('report/counterparty')
    rows = report_data.get('rows', [])
    
    now = datetime.now(timezone.utc)
    
    async with async_session_factory() as session:
        for row in rows:
            balance = row.get('balance', 0) / 100
            if balance >= 0:
                continue
                
            counterparty_meta = row.get('counterparty', {}).get('meta', {})
            counterparty_href = counterparty_meta.get('href', '')
            counterparty_id = counterparty_href.split('/')[-1]
            counterparty_name = row.get('counterparty', {}).get('name', 'Unknown')
            
            # Fetch demands (shipments)
            demands = await ms_client.get('entity/demand', params={'filter': f'agent={counterparty_href}'})
            
            debt_0_30 = 0.0
            debt_31_60 = 0.0
            debt_60_plus = 0.0
            
            for demand in demands.get('rows', []):
                # Assuming not fully paid, calculate age
                moment_str = demand.get('moment') # "2023-10-15 12:00:00"
                if not moment_str: continue
                
                try:
                    demand_date = datetime.strptime(moment_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    age_days = (now - demand_date).days
                    
                    unpaid_amount = (demand.get('sum', 0) - demand.get('payedSum', 0)) / 100
                    if unpaid_amount <= 0:
                        continue
                        
                    if age_days <= 30:
                        debt_0_30 += unpaid_amount
                    elif age_days <= 60:
                        debt_31_60 += unpaid_amount
                    else:
                        debt_60_plus += unpaid_amount
                except Exception as e:
                    logger.warning("cfo_demand_date_parse_error", error=str(e), moment=moment_str)
                    
            total_debt = debt_0_30 + debt_31_60 + debt_60_plus
            
            # Update DB
            stmt = select(DebtRegistry).where(DebtRegistry.counterparty_ms_id == counterparty_id)
            result = await session.execute(stmt)
            registry = result.scalars().first()
            
            if not registry:
                registry = DebtRegistry(counterparty_ms_id=counterparty_id)
                session.add(registry)
                
            registry.debt_0_30 = debt_0_30
            registry.debt_31_60 = debt_31_60
            registry.debt_60_plus = debt_60_plus
            registry.total_debt = total_debt
            registry.last_checked_at = now
            
            # Block logic if 60+ days
            if debt_60_plus > 0:
                # Find Bitrix Company
                companies = await bx_client.call_api('crm.company.list', {
                    'filter': {'UF_MS_ID': counterparty_id},
                    'select': ['ID', 'TITLE', 'UF_OVERRIDE_ACTIVE']
                })
                
                if companies:
                    company = companies[0]
                    bx_id = company.get('ID')
                    override = company.get('UF_OVERRIDE_ACTIVE')
                    
                    if override:
                        logger.info("cfo_debt_block_bypassed", company_id=bx_id)
                    else:
                        await bx_client.call_api('crm.company.update', {
                            'id': bx_id,
                            'fields': {
                                'UF_SHIPMENT_BLOCKED': True,
                                'UF_DEBT_TOTAL': total_debt,
                                'UF_DEBT_OVERDUE_60': debt_60_plus
                            }
                        })
                        registry.is_blocked = True
                        
                        await notifier.notify_debt_block(company_name=company.get('TITLE', counterparty_name), debt_amount=debt_60_plus)
                        
                        log = AuditLog(
                            entity_type='company',
                            entity_id=str(bx_id),
                            action='SHIPMENT_BLOCKED',
                            details={'debt_60_plus': debt_60_plus}
                        )
                        session.add(log)
            
        await session.commit()
    logger.info("cfo_debt_check_completed")

@celery_app.task(bind=True)
def run_daily_debt_check(self):
    asyncio.run(process_cfo_debt_check())
