"""Shared-secret checks. Constant-time comparison; secrets come from the environment only."""
import hmac

from fastapi import Header

from app.config import get_settings
from app.errors import APIError


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """REST API guard. If API_KEY is unset the API is open (dev / reviewer convenience)."""
    expected = get_settings().api_key
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise APIError(401, "unauthorized", "Missing or invalid X-API-Key header.")


def require_vapi_secret(x_vapi_secret: str | None = Header(default=None)) -> None:
    """Webhook guard: Vapi echoes the `server.secret` we configured on the assistant."""
    expected = get_settings().vapi_webhook_secret
    if expected and not (x_vapi_secret and hmac.compare_digest(x_vapi_secret, expected)):
        raise APIError(401, "unauthorized", "Invalid webhook secret.")
