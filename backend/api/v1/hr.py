"""HR & KPI API router with GPS Geolocation validation and MoySklad KPI."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from core.database import get_db

from services.hr_service import hr_service

router = APIRouter(prefix="/hr", tags=["Ходимлар ва KPI"])


class CheckinRequest(BaseModel):
    employee_id: int = Field(..., description="Ходим ID рақами")
    employee_name: str = Field(..., description="Ходим исм-шарифи")
    latitude: float = Field(..., description="Браузер GPS кенглиги (latitude)")
    longitude: float = Field(..., description="Браузер GPS узунлиги (longitude)")
    device_info: Optional[str] = Field(default="Web Browser", description="Қурилма ёки браузер маълумоти")


class CheckoutRequest(BaseModel):
    timesheet_id: Optional[str] = Field(default=None, description="Давомад ёзуви UUID идентификатори")
    employee_id: Optional[int] = Field(default=None, description="Ходим ID рақами")


class LocationCreateRequest(BaseModel):
    name: str = Field(..., description="Объект номи (масалан: «Асосий дўкон», «Комил Қодиров отель объекти»)")
    address: Optional[str] = Field(default="", description="Объект манзили")
    latitude: float = Field(..., description="GPS кенглик (latitude)")
    longitude: float = Field(..., description="GPS узунлик (longitude)")
    radius_meters: float = Field(default=100.0, description="Рухсат этилган радиус (метр)")
    is_active: bool = Field(default=True, description="Фаол ёки тугатилган объект")


class LocationUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, description="Объект номи")
    address: Optional[str] = Field(default=None, description="Объект манзили")
    latitude: Optional[float] = Field(default=None, description="GPS кенглик (latitude)")
    longitude: Optional[float] = Field(default=None, description="GPS узунлик (longitude)")
    radius_meters: Optional[float] = Field(default=None, description="Рухсат этилган радиус (метр)")
    is_active: Optional[bool] = Field(default=None, description="Фаол ёки тугатилган объект")



@router.post("/check-in", summary="GPS орқали ишга келишни қайд этиш (100м радиус текшируви)")
@router.post("/checkin", summary="GPS орқали ишга келишни қайд этиш (муқобил)")
async def checkin(request: CheckinRequest, session: AsyncSession = Depends(get_db)):
    """
    Ходимнинг ишга келишини браузер GPS координатаси ва Haversine формуласи орқали текшириш.
    Агар масофа <= 100 метр бўлса: қабул қилинади (Ўз вақтида / Кечикди).
    Агар масофа > 100 метр бўлса: 400 хатолик ва масофа кўрсатилиб рад этилади.
    """
    try:
        res = await hr_service.checkin(
            session=session,
            employee_id=request.employee_id,
            employee_name=request.employee_name,
            latitude=request.latitude,
            longitude=request.longitude,
            device_info=request.device_info
        )
        return res
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/check-out", summary="Ишдан кетишни қайд этиш ва соатларни ҳисоблаш")
@router.post("/checkout", summary="Ишдан кетишни қайд этиш (муқобил)")
@router.post("/checkout/{timesheet_id}", summary="UUID орқали ишдан кетиш")
async def checkout(
    timesheet_id: Optional[str] = None,
    request: Optional[CheckoutRequest] = None,
    session: AsyncSession = Depends(get_db)
):
    """
    Ходимнинг ишдан кетишини қайд этиш, ишланган соатларни автоматик ҳисоблаш.
    """
    ts_id = timesheet_id or (request.timesheet_id if request else None)
    emp_id = request.employee_id if request else None
    try:
        return await hr_service.checkout(session, timesheet_id=ts_id, employee_id=emp_id)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/overview", summary="Ходимлар давомади ва Сотувчилар KPI умумий ҳисоботи")
async def get_overview(
    period: str = Query("monthly", description="Ҳисобот даври: monthly, weekly, today"),
    from_date: Optional[str] = Query(None, description="Бошланиш санаси (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Тугаш санаси (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
    session: AsyncSession = Depends(get_db)
):
    """
    Давомад жадвали (attendance_list) ва Сотувчиларнинг МойСклад савдо KPI (sales_kpi) жадвали.
    """
    df = from_date or date_from
    dt = to_date or date_to
    try:
        return await hr_service.get_overview(session, period=period, from_date=df, to_date=dt)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/timesheet", summary="Давомад ёзувлари рўйхати")
async def get_timesheet(
    date_from: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
    from_date: Optional[str] = Query(None, description="Санадан (YYYY-MM-DD)"),
    to_date: Optional[str] = Query(None, description="Санагача (YYYY-MM-DD)"),
    employee_id: Optional[int] = Query(None, description="Ходим ID"),
    session: AsyncSession = Depends(get_db)
):
    """Timesheet list with GPS verification details."""
    df = from_date or date_from
    dt = to_date or date_to
    try:
        return await hr_service.get_timesheets(session, df, dt, employee_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/sales-kpi", summary="МойСклад савдолари асосида сотувчиларнинг KPI бонуси")
async def get_sales_kpi(
    period: str = Query("monthly", description="Давр: monthly, weekly, today"),
    session: AsyncSession = Depends(get_db)
):
    """Сотувчилар савдо ҳажми, чеклар сони ва 2% KPI бонуси."""
    try:
        return await hr_service.get_sales_kpi(period)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/employees", summary="Фаол ходимлар рўйхати")
async def get_employees():
    """МойСклад ва тизимдаги фаол ходимлар рўйхати (интерфейсда танлаш учун)."""
    try:
        return await hr_service.get_employees()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/store-location", summary="Дўкон/Омбор GPS координаталари ва радиуси")
async def get_store_location():
    """Дўкон/Омбор координатаси ва рухсат этилган 100м радиус."""
    return hr_service.get_store_location()


@router.get("/locations", summary="Барча мавжуд дўкон, омбор ва объектлар рўйхати")
async def get_locations(
    active_only: bool = Query(False, description="Фақат фаол объектларни олиш"),
    session: AsyncSession = Depends(get_db)
):
    """
    Тизимдаги барча дўконлар, омборлар ва қурилиш/монтаж объектлари (geofences) рўйхатини қайтаради.
    """
    try:
        return await hr_service.get_locations(session, active_only=active_only)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/locations", summary="Янги дўкон, омбор ёки объект қўшиш")
async def create_location(
    request: LocationCreateRequest,
    session: AsyncSession = Depends(get_db)
):
    """
    Янги объектни координаталари ва радиуси билан базага рўйхатга олиш.
    """
    try:
        return await hr_service.create_location(session, request.model_dump())
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.put("/locations/{location_id}", summary="Объект маълумотларини ёки координатасини ўзгартириш")
async def update_location(
    location_id: int,
    request: LocationUpdateRequest,
    session: AsyncSession = Depends(get_db)
):
    """
    Мавжуд объектнинг номи, манзили, координаталари ёки радиусини янгилаш.
    """
    try:
        return await hr_service.update_location(session, location_id, request.model_dump(exclude_unset=True))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.delete("/locations/{location_id}", summary="Иши тугаган объектни ўчириш ёки архивга олиш")
async def delete_location(
    location_id: int,
    soft: bool = Query(False, description="true бўлса архивга олади (is_active=0), false бўлса ўчиради"),
    session: AsyncSession = Depends(get_db)
):
    """
    Объектни ўчириш ёки архивга олиш (is_active=0 қилиш).
    """
    try:
        return await hr_service.delete_location(session, location_id, soft_delete=soft)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))



# Lazy-loaded bot and dispatcher for webhook handling
_bot_instance = None
_dp_instance = None

def _get_bot_dp():
    global _bot_instance, _dp_instance
    if _bot_instance is None:
        from services.telegram_bot_service import create_bot_and_dispatcher
        _bot_instance, _dp_instance = create_bot_and_dispatcher()
    return _bot_instance, _dp_instance


@router.post("/telegram-webhook", summary="Telegram Bot Webhook қабул қилиш эндпоинти")
async def telegram_webhook(request: Request):
    """
    Telegram бот орқали юборилган хабарлар ва GPS геолокация маълумотларини
    FastAPI орқали тўғридан-тўғри қабул қилиш ва давомад базасига ёзиш.
    """
    bot, dp = _get_bot_dp()
    try:
        data = await request.json()
        from aiogram.types import Update
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

