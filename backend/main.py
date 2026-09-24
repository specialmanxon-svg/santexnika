import os
import sys
import asyncio
from pathlib import Path

# Ensure backend directory and root directory are in sys.path for Render and local deployments
_BACKEND_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _BACKEND_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from core.database import engine, Base
from core.idempotency import IdempotencyMiddleware
from core.exceptions import (
    IntegrationError, 
    BitrixAPIError, 
    MoySkladAPIError,
    AuthorizationError,
    ShipmentBlockedError
)
import models
from api.v1.router import api_router

logger = structlog.get_logger()

import logging

# Configure structlog
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)


async def _demand_hard_lock_monitor():
    """Background real-time monitor for blocked counterparty shipments (4s polling)."""
    logger.info("hard_lock_demand_monitor_started")
    await asyncio.sleep(2)  # Initial grace delay
    while True:
        try:
            from api.v1.webhook import check_recent_demands
            await check_recent_demands(limit=25)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("hard_lock_demand_monitor_error", error=str(e))
        await asyncio.sleep(4)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    logger.info("application_starting", env=settings.app_env)
    # Create tables and auto-migrate columns if missing
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            def _migrate_tasks_cols(sync_conn):
                try:
                    cursor = sync_conn.connection.cursor()
                    cursor.execute("PRAGMA table_info(tasks);")
                    cols = {row[1] for row in cursor.fetchall()}
                    if cols:
                        if "creator_name" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN creator_name VARCHAR(255);")
                        if "creator_chat_id" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN creator_chat_id BIGINT;")
                        if "voice_file_id" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN voice_file_id VARCHAR(255);")
                        if "voice_url" not in cols:
                            cursor.execute("ALTER TABLE tasks ADD COLUMN voice_url VARCHAR(500);")
                except Exception as ex:
                    logger.warning("tasks_columns_migration_warning", error=str(ex))
            await conn.run_sync(_migrate_tasks_cols)
        logger.info("database_tables_and_columns_verified")
    except Exception as e:
        logger.warning("database_init_skipped", error=str(e), hint="Running in local mode without Docker DB")

    # Ensure authorized employees with verified Telegram IDs exist in DB
    try:
        from core.database import AsyncSessionLocal
        from models.hr import AuthorizedEmployee
        from sqlalchemy import select
        initial_employees = [
            {"employee_name": "Latipov F. F.", "phone_number": "+998888700070", "telegram_id": 5950380558, "telegram_username": "Diyor_manager", "moysklad_id": "c58dd132-b1a3-11ed-0a80-0bcd0004341a", "role": "Ходим"},
            {"employee_name": "Рахманова С.", "phone_number": "+998500520091", "telegram_id": 6489232626, "telegram_username": "Diyor_2004_hr", "moysklad_id": "934c5d44-f751-11f0-0a80-05450002d64d", "role": "Ходим"},
            {"employee_name": "Каххоров Ф. К.", "phone_number": "+998900806050", "telegram_id": 528729628, "telegram_username": "Kakharov_6050", "moysklad_id": "37302cba-6304-11f0-0a80-18b4000da6b1", "role": "Ходим"},
            {"employee_name": "Джумаева С. О.", "phone_number": "+998933830370", "telegram_id": 78997993, "telegram_username": "diyor_consult", "moysklad_id": "f21c37f7-df61-11ee-0a80-015a00389b7d", "role": "Ходим"},
            {"employee_name": "Зоиров А. А.", "phone_number": "+998907104449", "telegram_id": 6554491054, "telegram_username": "Diyor_Menedjer", "moysklad_id": "45ed0736-887c-11f0-0a80-14c400024c43", "role": "Ходим"},
            {"employee_name": "Фармонова Н.", "phone_number": "+998902989360", "telegram_id": 453818188, "telegram_username": "Nodira_Farmanova", "moysklad_id": "31c48cb8-b67f-11f1-0a80-1f360017b025", "role": "Ходим"},
        ]
        async with AsyncSessionLocal() as session:
            for emp_data in initial_employees:
                stmt = select(AuthorizedEmployee).where(
                    (AuthorizedEmployee.telegram_id == emp_data["telegram_id"]) |
                    (AuthorizedEmployee.employee_name == emp_data["employee_name"])
                )
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()
                if not existing:
                    new_emp = AuthorizedEmployee(
                        employee_name=emp_data["employee_name"],
                        phone_number=emp_data["phone_number"],
                        telegram_id=emp_data["telegram_id"],
                        telegram_username=emp_data["telegram_username"],
                        moysklad_id=emp_data["moysklad_id"],
                        role=emp_data["role"],
                        is_active=1,
                    )
                    session.add(new_emp)
                else:
                    if not existing.telegram_id:
                        existing.telegram_id = emp_data["telegram_id"]
                    if not existing.moysklad_id:
                        existing.moysklad_id = emp_data["moysklad_id"]
                    existing.is_active = 1
            await session.commit()
            logger.info("authorized_employees_seeded")
    except Exception as se:
        logger.warning("seed_authorized_employees_failed", error=str(se))

    # Start background demand monitor task
    monitor_task = asyncio.create_task(_demand_hard_lock_monitor())

    # Start task reminder background worker (Топшириқлар муддат назорати)
    from workers.task_worker import start_task_reminder_loop
    task_reminder_task = asyncio.create_task(start_task_reminder_loop())

    # Start Telegram Bot polling in background (Render & 24/7 cloud support)
    bot_task = None
    run_bot_env = os.getenv("RUN_TELEGRAM_BOT", "true").lower()
    if run_bot_env in ("true", "1", "yes") and settings.telegram_bot_token and not settings.telegram_bot_token.startswith("test_"):
        try:
            from bot import run_bot_polling
            bot_task = asyncio.create_task(run_bot_polling())
            logger.info("telegram_bot_background_task_started")
        except Exception as be:
            logger.warning("telegram_bot_start_failed", error=str(be))

    yield

    logger.info("application_shutting_down")
    monitor_task.cancel()
    task_reminder_task.cancel()
    if bot_task:
        bot_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    try:
        await task_reminder_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    if bot_task:
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    try:
        await engine.dispose()
    except Exception:
        pass


app = FastAPI(
    title="Diyorgroup Native CRM & MoySklad ERP Integration Platform",
    description="Diyorgroup CRM (diyorgroup.uz/crm) ↔ FastAPI Middleware ↔ МойСклад ↔ ИИ-Агенты",
    version="2.4.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://diyorgroup.uz",
        "http://diyorgroup.uz",
        "https://www.diyorgroup.uz",
        "http://www.diyorgroup.uz",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:5500",
        "*"
    ],
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Idempotency middleware
app.add_middleware(IdempotencyMiddleware)

# Include API router
app.include_router(api_router)


# Exception handlers
@app.exception_handler(IntegrationError)
async def integration_error_handler(request: Request, exc: IntegrationError):
    logger.error("integration_error", error=str(exc), path=request.url.path)
    return JSONResponse(status_code=502, content={"detail": str(exc)})

@app.exception_handler(AuthorizationError)
async def auth_error_handler(request: Request, exc: AuthorizationError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})

@app.exception_handler(ShipmentBlockedError)
async def shipment_blocked_handler(request: Request, exc: ShipmentBlockedError):
    return JSONResponse(status_code=423, content={"detail": str(exc)})


DASHBOARD_HTML_PATH = Path(__file__).resolve().parent.parent / "dashboard.html"


@app.get("/dashboard", response_class=FileResponse, summary="Diyor Group — 5 Модулли Бошқарув Панели")
async def serve_dashboard():
    """Diyor Group тизимининг 5 та асосий модулдан иборат бошқарув панели."""
    if not DASHBOARD_HTML_PATH.exists():
        return JSONResponse(status_code=404, content={"detail": f"Дашборд топилмади: {DASHBOARD_HTML_PATH}"})
    return FileResponse(DASHBOARD_HTML_PATH, media_type="text/html; charset=utf-8")


GEOFENCES_JSON_PATH = Path(__file__).resolve().parent / "data" / "geofences.json"
GEOFENCES_ALT_PATH = Path(__file__).resolve().parent.parent / "data" / "geofences.json"

@app.get("/data/geofences.json", response_class=FileResponse, summary="Ҳақиқий geofences JSON файли")
async def serve_geofences_json():
    """Доимий сақланган geofences JSON файлини қайтариш."""
    if GEOFENCES_JSON_PATH.exists():
        return FileResponse(GEOFENCES_JSON_PATH, media_type="application/json")
    if GEOFENCES_ALT_PATH.exists():
        return FileResponse(GEOFENCES_ALT_PATH, media_type="application/json")
    return JSONResponse(status_code=404, content={"detail": "geofences.json топилмади"})



_ms_status_cache = {"ts": 0.0, "data": None}


@app.get("/health")
@app.get("/api/v1/status")
async def health_check():
    """Health check endpoint with live MoySklad authentication verification."""
    import time
    now = time.time()
    if _ms_status_cache["data"] and (now - _ms_status_cache["ts"] < 60.0):
        return _ms_status_cache["data"]

    from services.moysklad_client import MoySkladClient
    ms_info = {"connected": False, "organization": "МойСклад (Diyor Group)", "auth_type": "None", "error": None}
    try:
        client = MoySkladClient()
        if await client.is_configured():
            conn = await client.test_connection()
            if conn.get("success"):
                ms_info["connected"] = True
                ms_info["organization"] = conn.get("organization_name", "Diyor Group MoySklad")
                ms_info["auth_type"] = conn.get("auth_type", "Basic")
            else:
                ms_info["error"] = conn.get("error")
        else:
            ms_info["error"] = "МойСклад логин ва пароли созланмаган"
        await client.close()
    except Exception as e:
        ms_info["error"] = str(e)

    res = {
        "status": "healthy",
        "service": "diyorgroup-erp",
        "version": "3.0.0",
        "moysklad": ms_info
    }
    _ms_status_cache["ts"] = now
    _ms_status_cache["data"] = res
    return res


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "uploads" / "voice_tasks").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")
