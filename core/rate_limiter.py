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
    _SLOWAPI_AVAILABLE = True
except ImportError:
    _SLOWAPI_AVAILABLE = False
    logger.info("slowapi not installed — rate limiting disabled")


def _is_trusted_proxy(host: str) -> bool:
    """True when the direct peer is a loopback/private address (our own nginx).

    Only then may we believe X-Forwarded-For / X-Real-IP. Trusting those
    headers unconditionally would let any client forge its IP and escape its
    rate-limit bucket.
    """
    return host.startswith("127.") or host in ("::1", "localhost") or host.startswith("10.") \
        or host.startswith("192.168.") or host.startswith("169.254.")


def get_remote_address(request: Request) -> str:
    """Resolve the *real* client IP for rate-limit bucketing.

    This is a deliberate override of ``slowapi.util.get_remote_address``, which
    returns ``request.client.host``. Behind the nginx reverse proxy that value
    is always ``127.0.0.1``: uvicorn is started without ``--proxy-headers``
    (see ``ExecStart`` in ``scripts/deploy_live.sh``), so it never rewrites
    ``request.client``.

    Consequence: every header-less request landed in a single shared bucket.
    EventSource cannot send ``X-Client-ID``, so *all* SSE streams on the whole
    internet shared the ``5/minute`` budget of ``GET /jobs/{id}/stream``. A few
    mobile reconnects exhausted it, the API answered 429, and per the HTML
    spec the browser then fails the EventSource permanently — which surfaced as
    "error / reconnect..." lines in the web client and a job that froze forever.

    Reading the real IP from X-Forwarded-For gives each client its own bucket.
    """
    peer = request.client.host if request.client else None
    if not peer or not _is_trusted_proxy(peer):
        return peer or "127.0.0.1"

    # X-Forwarded-For is a chain appended left-to-right by each hop; the
    # leftmost entry is the original client. X-Real-IP is nginx's own single
    # value and is used as a fallback when no chain is present.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or peer
    real_ip = request.headers.get("X-Real-IP", "")
    return real_ip.strip() or peer


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
