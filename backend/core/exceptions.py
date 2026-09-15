class IntegrationError(Exception):
    """Base class for external API integration errors."""
    pass

class BitrixAPIError(IntegrationError):
    """Raised when Bitrix24 API returns an error or fails."""
    pass

class MoySkladAPIError(IntegrationError):
    """Raised when MoySklad API returns an error or fails."""
    pass

class IdempotencyConflict(Exception):
    """Raised when an operation is blocked by an idempotency key."""
    pass

class ValidationError(Exception):
    """Raised for custom data validation errors."""
    pass

class AuthorizationError(Exception):
    """Raised when authentication or authorization fails."""
    pass

class ShipmentBlockedError(Exception):
    """Raised when a shipment creation is blocked (e.g., due to debt)."""
    pass
