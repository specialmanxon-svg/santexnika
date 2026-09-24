import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "diyorgroup.db").replace("\\", "/")

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # App Settings
    app_env: str = Field(default="production")
    app_debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Database
    database_url: str = Field(default=f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}", description="Async database connection string")
    database_url_sync: str = Field(default=f"sqlite:///{DEFAULT_DB_PATH}", description="Sync database connection string")

    def model_post_init(self, __context):
        if self.database_url.startswith("sqlite+aiosqlite:///.") or self.database_url == "sqlite+aiosqlite:///diyorgroup.db":
            self.database_url = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"
        if self.database_url_sync.startswith("sqlite:///.") or self.database_url_sync == "sqlite:///diyorgroup.db":
            self.database_url_sync = f"sqlite:///{DEFAULT_DB_PATH}"
        if not self.openai_api_key or not self.openai_api_key.strip():
            self.openai_api_key = "sk-placeholder-diyor-ai-ecosystem-2026"
    
    # Redis
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")
    celery_broker_url: str = Field(default="redis://127.0.0.1:6379/1")
    celery_result_backend: str = Field(default="redis://127.0.0.1:6379/2")
    
    # Diyorgroup Native CRM (https://diyorgroup.uz/crm/)
    diyorgroup_crm_url: str = Field(default="https://diyorgroup.uz/crm")
    diyorgroup_crm_api_key: str = Field(default="diyor_crm_live_api_key_2026")
    diyorgroup_crm_webhook_secret: str = Field(default="diyor_crm_webhook_secret_key")
    
    # MoySklad
    moysklad_api_url: str = Field(default="https://api.moysklad.ru/api/remap/1.2")
    moysklad_token: str = Field(default="")
    moysklad_login: str = Field(default="")
    moysklad_password: str = Field(default="")
    moysklad_organization_id: str = Field(default="")
    moysklad_reserve_state_id: str = Field(default="")
    
    # OpenAI
    openai_api_key: str = Field(default="sk-test-openai-key")
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o"
    openai_model: str = "gpt-4o"
    
    # Telegram
    telegram_bot_token: str = Field(default=os.getenv("TELEGRAM_BOT_TOKEN", "8859657582:AAE6oCILrzGUOydYSPNdpDckuUy5pcv4gIc"))
    telegram_alert_chat_id: str = Field(default=os.getenv("TELEGRAM_ALERT_CHAT_ID", "5950380558"))
    telegram_ceo_chat_id: str = Field(default=os.getenv("TELEGRAM_CEO_CHAT_ID", "5950380558"))
    admin_chat_id: str = Field(default=os.getenv("ADMIN_CHAT_ID", "5950380558"))
    management_group_id: str = Field(default=os.getenv("MANAGEMENT_GROUP_ID", ""))
    
    # Security
    secret_key: str = Field(default="super-secret-diyorgroup-key-32chars!")
    otp_secret: str = Field(default="JBSWY3DPEHPK3PXP")
    superuser_token: str = Field(default="diyor-admin-superuser-token")
    
    # HR & GPS Store Location (Bukhara Central Store / Warehouse)
    store_latitude: float = Field(default=39.748992, description="Bukhara central store latitude")
    store_longitude: float = Field(default=64.432118, description="Bukhara central store longitude")
    store_radius_meters: float = Field(default=150.0, description="Allowed check-in radius in meters")
    store_work_start_hour: int = Field(default=9, description="Work shift start hour (e.g. 9 for 09:00)")
    kpi_bonus_percent: float = Field(default=2.0, description="Default sales KPI bonus percent")
    telegram_group_id: str = Field(default="", description="Telegram management group ID")
    management_group_id: str = Field(default="", description="Telegram management group ID")
    admin_chat_id: str = Field(default="", description="Telegram admin chat ID")

    # Aliases requested for Telegram bot GPS check
    @property
    def STORE_LAT(self) -> float:
        return self.store_latitude

    @property
    def STORE_LON(self) -> float:
        return self.store_longitude

    @property
    def MAX_DISTANCE_METERS(self) -> float:
        return self.store_radius_meters

    @property
    def MANAGEMENT_GROUP_ID(self) -> str:
        return self.management_group_id or self.telegram_group_id or os.getenv("MANAGEMENT_GROUP_ID") or os.getenv("TELEGRAM_GROUP_ID") or ""

    @property
    def ADMIN_CHAT_ID(self) -> str:
        return self.admin_chat_id or os.getenv("ADMIN_CHAT_ID") or self.telegram_ceo_chat_id or "5950380558"

    @property
    def TELEGRAM_GROUP_ID(self) -> str:
        return self.management_group_id or self.telegram_group_id or self.telegram_alert_chat_id or self.telegram_ceo_chat_id or "5950380558"

settings = Settings()
