"""Diyorgroup Integration Middleware Service.

FastAPI application connecting Bitrix24 CRM with MoySklad ERP,
AI agents (CFO + Sales), and Telegram Mini App for field visits.
"""
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    logger.info("application_starting", env=settings.app_env)
    # Create tables if they don't exist (development only)
    # In production, use Alembic migrations
    if settings.app_debug:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("database_tables_created")
        except Exception as e:
            logger.warning("database_init_skipped", error=str(e), hint="Running in local mode without Docker DB")
    yield
    logger.info("application_shutting_down")
    try:
        await engine.dispose()
    except Exception:
        pass


app = FastAPI(
    title="Diyorgroup Integration Middleware",
    description="Битрикс24 ↔ FastAPI Middleware ↔ МойСклад ↔ ИИ-Агенты",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Telegram Mini App needs this
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


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "diyorgroup-middleware", "version": "1.0.0"}


@app.get("/")
async def root():
    return {"message": "Diyorgroup Integration Middleware", "docs": "/docs"}
