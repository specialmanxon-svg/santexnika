"""Security module for webhooks, OTP, and API authentication."""
import hmac
import hashlib
import structlog
import pyotp
from fastapi import HTTPException, Security, Depends
from fastapi.security import OAuth2PasswordBearer

from config import settings

logger = structlog.get_logger()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


def verify_bitrix_webhook(payload_auth_token: str) -> bool:
    """Verify application token matches Bitrix24 token in settings."""
    return hmac.compare_digest(
        payload_auth_token.encode('utf-8'),
        settings.bitrix24_application_token.encode('utf-8')
    )


def verify_moysklad_webhook(signature: str, payload_body: bytes) -> bool:
    """HMAC-SHA256 signature verification for MoySklad webhooks."""
    expected = hmac.new(
        settings.moysklad_webhook_secret.encode('utf-8'),
        msg=payload_body,
        digestmod=hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_otp(code: str) -> bool:
    """Verify TOTP OTP code using pyotp and settings.otp_secret."""
    totp = pyotp.TOTP(settings.otp_secret)
    is_valid = totp.verify(code)
    if not is_valid:
        logger.warning("invalid_otp_attempt")
    return is_valid


def get_current_superuser(token: str = Security(oauth2_scheme)) -> str:
    """FastAPI dependency to validate bearer token against superuser_token."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
        
    if not hmac.compare_digest(token, settings.superuser_token):
        logger.warning("invalid_superuser_token_attempt")
        raise HTTPException(status_code=401, detail="Invalid token")
        
    return "superuser"
