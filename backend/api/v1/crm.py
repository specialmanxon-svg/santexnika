"""FastAPI endpoints for Diyorgroup Native CRM (diyorgroup.uz/crm)."""
import logging
from typing import List, Optional
from uuid import UUID, uuid4
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select, desc

from core.database import async_session_factory
from models.crm import CrmDeal, CrmLead, CrmTask, CrmPipelineType, CrmStageType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crm", tags=["Diyorgroup CRM (diyorgroup.uz/crm)"])

# --- Pydantic Schemas ---

class DealStageChangeRequest(BaseModel):
    deal_id: str
    new_stage: str
    previous_stage: Optional[str] = None
    comment: Optional[str] = None
    phone: Optional[str] = None

class DealCreateRequest(BaseModel):
    title: str
    pipeline_type: str = "B2C_SHOWROOM"
    counterparty_name: str
    phone: Optional[str] = None
    counterparty_phone: Optional[str] = None
    total_amount: float = 0.0
    designer_username: Optional[str] = None
    referral: Optional[str] = None
    items: Optional[List[dict]] = None

class DealResponse(BaseModel):
    id: str
    title: str
    pipeline_type: str
    stage: str
    counterparty_name: str
    counterparty_phone: Optional[str] = None
    total_amount: float
    moysklad_order_id: Optional[str] = None
    designer_username: Optional[str] = None
    referral: Optional[str] = None
    designer_commission: float = 0.0
    items: Optional[List[dict]] = None

class LeadCreateRequest(BaseModel):
    contact_name: str
    phone: str
    source: str
    intent_notes: Optional[str] = None
    pipeline_type: str = "B2C_SHOWROOM"

class LeadResponse(BaseModel):
    id: str
    contact_name: str
    phone: str
    source: str
    pipeline_type: str
    stage: str

class PipelineResponse(BaseModel):
    id: str
    name: str
    type: str
    description: str

# --- Endpoints ---

@router.post("/deals/stage-change", summary="Хук перехода этапа сделки")
async def deal_stage_change(request: DealStageChangeRequest):
    """
    Хук перехода этапа сделки.
    - При переходе в INVOICE_ISSUED: инициируется резерв в МойСклад.
    - При переходе в PAID: рассчитывается 5% комиссия дизайнеру.
    """
    logger.info(f"Received stage change for deal {request.deal_id} to {request.new_stage}")
    
    async with async_session_factory() as session:
        try:
            deal_uuid = UUID(request.deal_id)
            stmt = select(CrmDeal).where(CrmDeal.id == deal_uuid)
        except ValueError:
            stmt = select(CrmDeal).where(CrmDeal.title.ilike(f"%{request.deal_id}%"))
            
        result = await session.execute(stmt)
        deal = result.scalars().first()
        
        if deal:
            try:
                deal.stage = CrmStageType(request.new_stage)
            except ValueError:
                pass
            
            # Scenario 4.1: Reservation trigger
            if request.new_stage == "INVOICE_ISSUED" and not deal.moysklad_order_id:
                from services.moysklad_client import MoySkladClient
                from config import settings
                ms_client = MoySkladClient()
                
                try:
                    client_phone = request.phone or getattr(deal, "counterparty_phone", None)
                    agent_meta = await ms_client.get_or_create_counterparty(deal.counterparty_name, phone=client_phone)
                    
                    positions = []
                    for item in deal.items or []:
                        ms_id = item.get("moysklad_id")
                        qty = item.get("quantity", 1)
                        # MoySklad expects price in minor units (tiyin)
                        price = int(item.get("price", 0.0) * 100)
                        
                        if ms_id:
                            positions.append({
                                "quantity": qty,
                                "price": price,
                                "reserve": qty,
                                "assortment": {
                                    "meta": {
                                        "href": f"{settings.moysklad_api_url.rstrip('/')}/entity/product/{ms_id}",
                                        "type": "product",
                                        "mediaType": "application/json"
                                    }
                                }
                            })
                    
                    order_data = {
                        "organization": {
                            "meta": {
                                "href": f"{settings.moysklad_api_url.rstrip('/')}/entity/organization/{settings.moysklad_organization_id}",
                                "type": "organization",
                                "mediaType": "application/json"
                            }
                        },
                        "agent": {
                            "meta": agent_meta
                        }
                    }
                    order_desc = f"Заказ от {deal.counterparty_name}"
                    if client_phone:
                        order_desc += f" (Тел: {client_phone})"
                    if deal.designer_username:
                        order_desc += f". Реферал: {deal.designer_username}"
                    order_data["description"] = order_desc

                    if positions:
                        order_data["positions"] = positions
                        
                    order_resp = await ms_client.create_customer_order(order_data)
                    ms_order_uuid = order_resp.get("id")
                    ms_order_name = order_resp.get("name")
                    deal.moysklad_order_id = ms_order_uuid or ms_order_name
                    ms_ui_link = f"https://online.moysklad.ru/app/#customerorder/edit?id={ms_order_uuid or ms_order_name}"
                    logger.info(f"Successfully created real MoySklad order: {ms_ui_link}")
                    
                except Exception as e:
                    logger.error(f"Failed to create MoySklad order: {str(e)}")
                    raise HTTPException(status_code=400, detail=f"MoySklad API Error: {str(e)}")
            
            # Scenario 4.2: Referral 5% bonus trigger  
            if request.new_stage == "PAID" and deal.designer_username:
                try:
                    from services.commission import calculate_designer_commission
                    commission_result = await calculate_designer_commission(str(deal.id), session)
                    if commission_result:
                        deal.designer_commission = commission_result["commission"]
                        logger.info(f"Calculated 5% referral bonus: {deal.designer_commission} for {deal.designer_username}")
                except Exception as e:
                    # Fallback to simple calculation (5% bonus)
                    deal.designer_commission = round(deal.total_amount * 0.05, 2)
                    logger.warning(f"Commission service failed, using fallback: {e}")
                
            commission_data = locals().get("commission_result") or {}
            await session.commit()
            return {
                "status": "success",
                "deal_id": str(deal.id),
                "stage": deal.stage.value,
                "moysklad_order_id": deal.moysklad_order_id,
                "moysklad_order_name": locals().get("ms_order_name") or deal.moysklad_order_id,
                "designer_commission": deal.designer_commission,
                "gross_margin": commission_data.get("gross_margin"),
                "referral": deal.designer_username
            }

    return {"status": "success", "message": f"Stage updated to {request.new_stage}"}

@router.get("/deals", response_model=List[DealResponse], summary="Список сделок")
async def get_deals(skip: int = 0, limit: int = 100):
    """Возвращает список всех сделок из Diyorgroup CRM."""
    async with async_session_factory() as session:
        stmt = select(CrmDeal).order_by(desc(CrmDeal.created_at)).offset(skip).limit(limit)
        result = await session.execute(stmt)
        deals = result.scalars().all()
        return [
            DealResponse(
                id=str(d.id),
                title=d.title,
                pipeline_type=d.pipeline_type.value if hasattr(d.pipeline_type, "value") else str(d.pipeline_type),
                stage=d.stage.value if hasattr(d.stage, "value") else str(d.stage),
                counterparty_name=d.counterparty_name,
                counterparty_phone=getattr(d, "counterparty_phone", None),
                total_amount=d.total_amount,
                moysklad_order_id=d.moysklad_order_id,
                designer_username=d.designer_username,
                referral=d.designer_username,
                designer_commission=d.designer_commission,
                items=d.items
            )
            for d in deals
        ]

@router.post("/deals", response_model=DealResponse, status_code=status.HTTP_201_CREATED, summary="Создание сделки")
async def create_deal(request: DealCreateRequest):
    """Создает новую сделку в Diyorgroup CRM."""
    logger.info(f"Creating deal: {request.title}")
    phone_val = request.counterparty_phone or request.phone
    referral_val = (request.referral or request.designer_username or "").strip() or None
    async with async_session_factory() as session:
        try:
            ptype = CrmPipelineType(request.pipeline_type)
        except ValueError:
            ptype = CrmPipelineType.B2C_SHOWROOM
            
        new_deal = CrmDeal(
            id=uuid4(),
            title=request.title,
            pipeline_type=ptype,
            stage=CrmStageType.NEW,
            counterparty_name=request.counterparty_name,
            counterparty_phone=phone_val,
            total_amount=request.total_amount,
            designer_username=referral_val,
            items=request.items
        )
        session.add(new_deal)
        await session.commit()
        await session.refresh(new_deal)
        
        return DealResponse(
            id=str(new_deal.id),
            title=new_deal.title,
            pipeline_type=new_deal.pipeline_type.value,
            stage=new_deal.stage.value,
            counterparty_name=new_deal.counterparty_name,
            counterparty_phone=new_deal.counterparty_phone,
            total_amount=new_deal.total_amount,
            moysklad_order_id=new_deal.moysklad_order_id,
            designer_username=new_deal.designer_username,
            referral=new_deal.designer_username,
            designer_commission=new_deal.designer_commission,
            items=new_deal.items
        )

@router.get("/leads", response_model=List[LeadResponse], summary="Список лидов")
async def get_leads(skip: int = 0, limit: int = 100):
    """Возвращает список всех лидов из воронки Diyorgroup CRM."""
    async with async_session_factory() as session:
        stmt = select(CrmLead).order_by(desc(CrmLead.created_at)).offset(skip).limit(limit)
        result = await session.execute(stmt)
        leads = result.scalars().all()
        return [
            LeadResponse(
                id=str(l.id),
                contact_name=l.contact_name,
                phone=l.phone,
                source=l.source,
                pipeline_type=l.pipeline_type.value if hasattr(l.pipeline_type, "value") else str(l.pipeline_type),
                stage=l.stage.value if hasattr(l.stage, "value") else str(l.stage)
            )
            for l in leads
        ]

@router.post("/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED, summary="Создание лида")
async def create_lead(request: LeadCreateRequest):
    """Прием лида из соцсетей / Telegram / сайта."""
    logger.info(f"Received new lead from {request.source}: {request.contact_name}")
    async with async_session_factory() as session:
        try:
            ptype = CrmPipelineType(request.pipeline_type)
        except ValueError:
            ptype = CrmPipelineType.B2C_SHOWROOM
            
        lead = CrmLead(
            id=uuid4(),
            contact_name=request.contact_name,
            phone=request.phone,
            source=request.source,
            intent_notes=request.intent_notes,
            pipeline_type=ptype,
            stage=CrmStageType.NEW
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)
        
        return LeadResponse(
            id=str(lead.id),
            contact_name=lead.contact_name,
            phone=lead.phone,
            source=lead.source,
            pipeline_type=lead.pipeline_type.value,
            stage=lead.stage.value
        )

@router.get("/pipelines", response_model=List[PipelineResponse], summary="Воронки продаж")
async def get_pipelines():
    """Возвращает доступные воронки продаж."""
    return [
        PipelineResponse(
            id="pipe-1",
            name="Шоурум B2C",
            type="B2C_SHOWROOM",
            description="Шоурум сантехники (дизайнеры 5% комиссионных)"
        ),
        PipelineResponse(
            id="pipe-2",
            name="Объектное снабжение B2B",
            type="B2B_OBJECT",
            description="Объектное снабжение (жесткий контроль долгов >60 дней)"
        )
    ]

@router.post("/seed-demo", summary="Наполнение тестовыми данными")
async def seed_demo_data():
    """Наполняет базу реалистичными сделками и лидами Diyorgroup."""
    async with async_session_factory() as session:
        # Check if already seeded
        result = await session.execute(select(CrmDeal))
        existing_deals = result.scalars().all()
        if existing_deals:
            return {"status": "ok", "message": f"Already seeded ({len(existing_deals)} deals exist)"}

        demo_deals = [
            CrmDeal(
                id=uuid4(),
                title="Вилла Мирзо-Улугбек — Комплект Grohe & Villeroy",
                pipeline_type=CrmPipelineType.B2C_SHOWROOM,
                stage=CrmStageType.PAID,
                counterparty_name="Шерзод Каримов (Дизайнер: @dizayn_tashkent)",
                total_amount=48500000.0,
                moysklad_order_id="MS-ORD-9201",
                designer_username="@dizayn_tashkent",
                designer_commission=3395000.0,
                compat_status="COMPATIBLE_VERIFIED"
            ),
            CrmDeal(
                id=uuid4(),
                title="ЖК 'Infinity Plaza' — Поставка 40 скрытых инсталляций Geberit",
                pipeline_type=CrmPipelineType.B2B_OBJECT,
                stage=CrmStageType.INVOICE_ISSUED,
                counterparty_name="ООО 'Golden House City'",
                total_amount=164000000.0,
                moysklad_order_id="MS-ORD-9204",
                compat_status="COMPATIBLE_VERIFIED"
            ),
            CrmDeal(
                id=uuid4(),
                title="Пентхаус 'Tashkent City' — Hansgrohe Raindance & Ванна Duravit",
                pipeline_type=CrmPipelineType.B2C_SHOWROOM,
                stage=CrmStageType.ESTIMATE_CALCULATED,
                counterparty_name="Азиза Рустамова",
                total_amount=32400000.0,
                designer_username="@interior_aziz",
                compat_status="COMPATIBLE_VERIFIED"
            )
        ]
        
        demo_leads = [
            CrmLead(
                id=uuid4(),
                contact_name="Жасур Махмудов",
                phone="+998 90 123 45 67",
                source="Meta Ads (Instagram)",
                intent_notes="Интересуется смесителями скрытого монтажа Hansgrohe",
                pipeline_type=CrmPipelineType.B2C_SHOWROOM,
                stage=CrmStageType.NEW
            ),
            CrmLead(
                id=uuid4(),
                contact_name="ООО 'Modern Building Samarkand'",
                phone="+998 93 987 65 43",
                source="Telegram B2B",
                intent_notes="Запрос коммерческого предложения на 120 инсталляций для гостиницы",
                pipeline_type=CrmPipelineType.B2B_OBJECT,
                stage=CrmStageType.QUALIFIED
            )
        ]
        
        session.add_all(demo_deals)
        session.add_all(demo_leads)
        await session.commit()
        
        return {
            "status": "success",
            "deals_seeded": len(demo_deals),
            "leads_seeded": len(demo_leads),
            "message": "Demo data populated successfully"
        }
