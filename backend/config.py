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
    telegram_bot_token: str = Field(default="test_telegram_bot_token")
    telegram_alert_chat_id: str = Field(default="-1001234567890")
    telegram_ceo_chat_id: str = Field(default="123456789")
    
    # Security
    secret_key: str = Field(default="super-secret-diyorgroup-key-32chars!")
    otp_secret: str = Field(default="JBSWY3DPEHPK3PXP")
    superuser_token: str = Field(default="diyor-admin-superuser-token")
    
    # App
    app_env: str = "development"
    app_debug: bool = True
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"

settings = Settings()
