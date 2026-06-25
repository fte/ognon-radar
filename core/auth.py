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


def generate_client_id() -> str:
    """Return a human-readable client ID with CSPRNG entropy, e.g. 'swift-falcon-aB3xY9kZ'."""
    adj = secrets.choice(_ADJECTIVES)
    noun = secrets.choice(_NOUNS)
    suffix = secrets.token_urlsafe(8)  # ~48 bits of unguessable entropy
    return f"{adj}-{noun}-{suffix}"


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


def require_client_id(
    x_client_id: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
) -> str:
    # If a key is presented, validate it strictly — never fall back silently.
    if x_api_key:
        if x_api_key != settings.api_key:
            # Not the admin key: must be a valid client key or it's rejected.
            from core.client_keys import client_key_store
            resolved = client_key_store.get_client_id(x_api_key)
            if not resolved:
                raise HTTPException(status_code=401, detail="Invalid API key")
            return resolved
        # Admin key: fall through and require X-Client-ID to scope the operation.
    if not x_client_id:
        raise HTTPException(status_code=400, detail="X-Client-ID header is required")
    return x_client_id
