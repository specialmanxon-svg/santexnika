"""Diyor Group — 5 та асосий модул роутерлари."""
from fastapi import APIRouter

from api.v1.warehouse import router as warehouse_router
from api.v1.debts import router as debts_router
from api.v1.cashflow import router as cashflow_router
from api.v1.finance import router as finance_router
from api.v1.hr import router as hr_router
from api.v1.referrals import router as referrals_router
from api.v1.partners import router as partners_router
from api.v1.moysklad import router as moysklad_router
from api.v1.campaigns import router as campaigns_router
from api.v1.analytics import router as analytics_router
from api.v1.counterparties import router as counterparties_router
from api.v1.crm import router as crm_router
from api.v1.management import router as management_router

api_router = APIRouter(prefix="/api/v1")

# === 5 та асосий модул ===
api_router.include_router(warehouse_router)      # 1. Омбор ва товар аудити
api_router.include_router(debts_router)           # 2. Дебитор ва кредитор назорати
api_router.include_router(cashflow_router)        # 3. Пул айланмаси (Cash Flow)
api_router.include_router(finance_router)         # 3.1 Молия ва Пул айланмаси (Finance & Cashflow)
api_router.include_router(hr_router)              # 4. Ходимлар ва KPI
api_router.include_router(referrals_router)       # 5. Маркетинг ва ҳамкорлик (5% бонус)
api_router.include_router(partners_router)        # 5.1 Партнеры ва 5% бонус рефераллари (МойСклад)

# === Ёрдамчи роутерлар ===
api_router.include_router(moysklad_router)        # МойСклад интеграция
api_router.include_router(counterparties_router)   # Контрагентлар
api_router.include_router(campaigns_router)        # Рассылкалар
api_router.include_router(analytics_router)        # Аналитика
api_router.include_router(crm_router)              # CRM
api_router.include_router(management_router)       # Бошқарув
