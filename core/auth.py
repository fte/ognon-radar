"""
Authentication and client-identity dependencies.
API key auth is disabled when settings.api_key is empty (dev default).
"""
import secrets
from fastapi import Header, HTTPException
from typing import Optional

from config import settings

_ADJECTIVES = [
    "swift", "brave", "dark", "silent", "ghost", "sharp", "cold", "wild",
    "iron", "storm", "frost", "amber", "jade", "cobalt", "onyx", "ash",
    "crimson", "silver", "hollow", "quiet",
]
_NOUNS = [
    "falcon", "wolf", "raven", "cipher", "spectre", "node", "proxy", "byte",
    "signal", "relay", "vault", "echo", "drift", "torch", "nexus", "pulse",
    "shard", "arc", "lens", "trace",
]


def _readable_id(prefix: str) -> str:
    adj = secrets.choice(_ADJECTIVES)
    noun = secrets.choice(_NOUNS)
    suffix = secrets.token_urlsafe(6)  # ~36 bits of entropy, 8 url-safe chars
    return f"{prefix}-{adj}-{noun}-{suffix}"


def generate_client_id() -> str:
    """Return a memorable client ID, e.g. 'ognu-swift-falcon-aB3xY9'."""
    return _readable_id("ognu")


def generate_job_id() -> str:
    """Kept for backward compatibility. Use typed variants below."""
    return generate_search_job_id()


def generate_search_job_id() -> str:
    """Return a search job ID, e.g. 'ognse-dark-raven-kZ9mXp'."""
    return _readable_id("ognse")


def generate_capture_job_id() -> str:
    """Return a capture job ID, e.g. 'ognc-swift-falcon-aB3xY9'."""
    return _readable_id("ognc")


def generate_screenshot_job_id() -> str:
    """Return a screenshot job ID, e.g. 'ognss-bold-fox-xT7pQw'."""
    return _readable_id("ognss")


def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def get_is_admin(x_api_key: Optional[str] = Header(None)) -> bool:
    """Admin = holder of the API key. Returns False if no key is configured."""
    if not settings.api_key:
        return False
    return x_api_key == settings.api_key


def _resolve_client_id(client_id: Optional[str], api_key: Optional[str]) -> str:
    if api_key:
        if settings.api_key and api_key == settings.api_key:
            return client_id or ""
        from core.client_keys import client_key_store
        resolved = client_key_store.get_client_id(api_key)
        if not resolved:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return resolved
    if not client_id:
        raise HTTPException(status_code=400, detail="X-Client-ID header is required")
    return client_id


def require_client_id_sse(
    job_id: str,  # path param, injected by FastAPI
    x_client_id: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    token: Optional[str] = None,  # ephemeral stream token (query param)
) -> str:
    """
    Auth for SSE stream endpoints. Accepts either:
    - Normal headers (X-Client-ID / X-API-Key) for programmatic clients
    - ?token= ephemeral token minted by POST /jobs/{id}/stream-token (for EventSource)

    Never accepts raw api_key or client_id as plain query params — they would
    appear in server logs and browser history.
    """
    if token:
        from core.stream_tokens import redeem
        client_id = redeem(token, job_id)
        if not client_id:
            raise HTTPException(status_code=401, detail="Stream token invalid or expired")
        return client_id
    return _resolve_client_id(x_client_id, x_api_key)


def require_client_id(
    x_client_id: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
) -> str:
    return _resolve_client_id(x_client_id, x_api_key)
