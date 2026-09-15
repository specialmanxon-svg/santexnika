from fastapi import APIRouter
from api.v1.bitrix import router as bitrix_router
from api.v1.moysklad import router as moysklad_router
from api.v1.management import router as management_router
from api.v1.compatibility import router as compatibility_router
from api.v1.field_visits import router as visits_router
from api.v1.social import router as social_router
from api.v1.campaigns import router as campaigns_router
from api.v1.analytics import router as analytics_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(bitrix_router)
api_router.include_router(moysklad_router)
api_router.include_router(management_router)
api_router.include_router(compatibility_router)
api_router.include_router(visits_router)
api_router.include_router(social_router)
api_router.include_router(campaigns_router)
api_router.include_router(analytics_router)
