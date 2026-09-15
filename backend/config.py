from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    
    # Database
    database_url: str = Field(..., description="Async PostgreSQL connection string")
    database_url_sync: str = Field(..., description="Sync PostgreSQL connection string for Alembic")
    
    # Redis
    redis_url: str = Field(default="redis://redis:6379/0")
    celery_broker_url: str = Field(default="redis://redis:6379/1")
    celery_result_backend: str = Field(default="redis://redis:6379/2")
    
    # Bitrix24
    bitrix24_domain: str
    bitrix24_webhook_user_id: int = 1
    bitrix24_webhook_secret: str
    bitrix24_application_token: str
    
    @property
    def bitrix24_webhook_url(self) -> str:
        return f"{self.bitrix24_domain}/rest/{self.bitrix24_webhook_user_id}/{self.bitrix24_webhook_secret}"
    
    # MoySklad
    moysklad_api_url: str = Field(default="https://api.moysklad.ru/api/remap/1.2")
    moysklad_token: str
    moysklad_organization_id: str
    moysklad_reserve_state_id: str
    
    # OpenAI
    openai_api_key: str
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o"
    
    # Telegram
    telegram_bot_token: str
    telegram_alert_chat_id: str
    telegram_ceo_chat_id: str
    
    # Security
    secret_key: str
    otp_secret: str
    superuser_token: str
    
    # App
    app_env: str = "production"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

settings = Settings()
