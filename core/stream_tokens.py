"""
Short-lived, single-use stream tokens for SSE endpoints.

EventSource cannot send custom headers, so we mint a token via a normal
header-authenticated request and pass it as a query param to the stream URL.
"""
import secrets
import time
from typing import Dict, Optional, Tuple

_TTL = 600  # seconds — covers the full duration of long capture/screenshot jobs

# {token: (client_id, job_id, expires_at)}
_store: Dict[str, Tuple[str, str, float]] = {}


def mint(client_id: str, job_id: str) -> str:
    _purge_expired()
    token = secrets.token_urlsafe(24)
    _store[token] = (client_id, job_id, time.monotonic() + _TTL)
    return token


def redeem(token: str, job_id: str) -> Optional[str]:
    """Validate token against job_id and return client_id, or None if invalid/expired."""
    entry = _store.get(token)
    if not entry:
        return None
    stored_client_id, stored_job_id, expires_at = entry
    if time.monotonic() > expires_at or stored_job_id != job_id:
        _store.pop(token, None)
        return None
    # Reusable within TTL — EventSource reconnects reuse the same token
    return stored_client_id


def _purge_expired() -> None:
    now = time.monotonic()
    expired = [t for t, (_, _, exp) in _store.items() if now > exp]
    for t in expired:
        _store.pop(t, None)
