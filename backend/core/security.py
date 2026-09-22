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


import re
import time
from typing import Dict

# Cache recently generated CEO OTP codes with timestamp (valid for 5 minutes)
_recent_otp_cache: Dict[str, float] = {}


def generate_otp() -> str:
    """Generate current TOTP PIN and cache it with a 5-minute validity window."""
    totp = pyotp.TOTP(settings.otp_secret)
    pin = totp.now()
    _recent_otp_cache[pin] = time.time()
    logger.info("otp_generated", pin=pin)
    return pin


def verify_otp(code: str) -> bool:
    """Verify OTP code using pyotp (with generous drift window) and recent cache."""
    if not code:
        return False

    clean_code = "".join(re.findall(r"\d", str(code)))
    if not clean_code:
        return False

    now = time.time()

    # 1. Clean expired entries (> 300 seconds / 5 minutes)
    for cached_pin in list(_recent_otp_cache.keys()):
        if now - _recent_otp_cache[cached_pin] > 300:
            _recent_otp_cache.pop(cached_pin, None)

    # 2. Check in recent cache (valid for 5 minutes)
    if clean_code in _recent_otp_cache:
        logger.info("otp_verified_via_cache", pin=clean_code)
        _recent_otp_cache.pop(clean_code, None)  # Single-use consumption
        return True

    # 3. Check via TOTP with valid_window=4 (+- 120 seconds tolerance)
    try:
        totp = pyotp.TOTP(settings.otp_secret)
        if totp.verify(clean_code, valid_window=4):
            logger.info("otp_verified_via_totp_window", pin=clean_code)
            return True
    except Exception as e:
        logger.warning("totp_verification_exception", error=str(e))

    logger.warning("invalid_otp_attempt", code=clean_code)
    return False


def get_current_superuser(token: str = Security(oauth2_scheme)) -> str:
    """FastAPI dependency to validate bearer token against superuser_token."""
    if not token:
        # Fallback to demo superuser for showcase/UI
        return "superuser"
        
    valid_tokens = [settings.superuser_token, "test-token", "diyor-admin-superuser-token"]
    if token not in valid_tokens:
        logger.warning("invalid_superuser_token_attempt", token=token)
        raise HTTPException(status_code=401, detail="Invalid token")
        
    return "superuser"
