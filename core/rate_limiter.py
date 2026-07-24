"""
Custom rate-limiter key function and shared Limiter for the DarkWeb API.

Identification precedence for rate-limit buckets:
  1. X-API-Key header  (persistent server-side credential)
  2. X-Client-ID header (session-level token)
  3. Remote IP          (fallback for unauthenticated requests)

Routes should use @limiter.limit("N/period") decorator — see the route
handlers for per-endpoint limits. The default_limits below act as a
safety net for routes that don't specify their own limit.

If the slowapi package is not installed (e.g. in CI/test containers),
rate limiting is silently disabled via a no-op stub and the slowapi
imports are skipped entirely.
"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings

logger = logging.getLogger(__name__)

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler as _slowapi_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    _SLOWAPI_AVAILABLE = True
except ImportError:
    _SLOWAPI_AVAILABLE = False
    logger.info("slowapi not installed — rate limiting disabled")

    def get_remote_address(request: Request) -> str:
        """Fallback: extract client IP from request headers (as slowapi does)."""
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "127.0.0.1"


def _rate_limit_key(request: Request) -> str:
    """Return a stable identifier for the caller.

    Precedence: X-API-Key → X-Client-ID → remote IP.
    Each bucket is scoped so an API key user and an IP user never collide.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"ak:{api_key}"
    client_id = request.headers.get("X-Client-ID")
    if client_id:
        return f"cid:{client_id}"
    return get_remote_address(request)


if _SLOWAPI_AVAILABLE:
    limiter = Limiter(
        key_func=_rate_limit_key,
        default_limits=settings.rate_limit_default,
        storage_uri=settings.rate_limit_storage_uri,
    )
    rate_limit_exceeded_handler = _slowapi_handler
    RateLimitExceededError = RateLimitExceeded
else:
    # No-op stub — all decorators become pass-through
    class _NoopLimiter:
        """Stub that absorbs @limiter.limit() and @limiter.exempt decorators."""
        def limit(self, *_, **__):
            def deco(f):
                return f
            return deco
        def exempt(self, f):
            return f

    limiter = _NoopLimiter()

    async def rate_limit_exceeded_handler(request, exc):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    class RateLimitExceededError(Exception):
        pass
