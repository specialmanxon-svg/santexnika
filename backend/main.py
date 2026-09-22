import sys
import asyncio
from pathlib import Path

# Ensure backend directory is in sys.path for Render and local deployments
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

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
    # Create tables if they don't exist (development only)
    if settings.app_debug:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("database_tables_created")
        except Exception as e:
            logger.warning("database_init_skipped", error=str(e), hint="Running in local mode without Docker DB")

    # Start background demand monitor task
    monitor_task = asyncio.create_task(_demand_hard_lock_monitor())

    # Start task reminder background worker (Топшириқлар муддат назорати)
    from workers.task_worker import start_task_reminder_loop
    task_reminder_task = asyncio.create_task(start_task_reminder_loop())

    yield

    logger.info("application_shutting_down")
    monitor_task.cancel()
    task_reminder_task.cancel()
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
