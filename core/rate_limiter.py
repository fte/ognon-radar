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
import ipaddress
import logging
from typing import Optional, Tuple, Union

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


# Networks whose forwarded headers we trust by default: loopback, the full
# RFC1918 set, IPv6 loopback / unique-local / link-local. This covers the
# deployments we actually run — nginx on the same host (peer 127.0.0.1) and
# nginx as a sibling container on a Docker bridge (peer 172.17-31.x.x, which
# the old startswith check missed entirely and silently collapsed every client
# into one bucket again).
#
# Deliberately NOT included: 0.0.0.0/8, multicast, and the RFC 5737 / RFC 6598
# carrier-grade/documentation ranges that Python's ipaddress.is_private also
# reports as private. Those are never a legitimate proxy peer.
_DEFAULT_TRUSTED_NETWORKS: Tuple[Union[ipaddress.IPv4Network, ipaddress.IPv6Network], ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",     # IPv4 loopback
        "10.0.0.0/8",      # RFC1918
        "172.16.0.0/12",   # RFC1918 (Docker bridge default lives here)
        "192.168.0.0/16",  # RFC1918
        "169.254.0.0/16",  # IPv4 link-local (kept from the previous check)
        "::1/128",         # IPv6 loopback
        "fc00::/7",        # IPv6 unique-local (Docker's default IPv6 pool)
        "fe80::/10",       # IPv6 link-local
    )
)

# Populated lazily from config so an operator can narrow or widen the boundary.
# This module global is the single memo — do not add an lru_cache on top, or the
# cache and this variable diverge and config changes stop taking effect.
_trusted_networks: Optional[Tuple[Union[ipaddress.IPv4Network, ipaddress.IPv6Network], ...]] = None


def _get_trusted_networks() -> Tuple[Union[ipaddress.IPv4Network, ipaddress.IPv6Network], ...]:
    """Resolve the configured trust boundary.

    ``security.trusted_proxies`` in the active config file, when set, *replaces*
    the built-in default entirely — that is the escape hatch for an operator
    who wants loopback-only, or who runs the proxy at a routable address.
    Invalid entries are logged and skipped rather than failing startup, and an
    entry that parses to nothing valid falls back to the default.
    """
    global _trusted_networks
    if _trusted_networks is not None:
        return _trusted_networks

    configured = getattr(settings, "trusted_proxies", None) or []
    networks = []
    for raw in configured:
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            logger.warning("security.trusted_proxies: ignoring invalid entry %r", raw)

    _trusted_networks = tuple(networks) if networks else _DEFAULT_TRUSTED_NETWORKS
    if configured and not networks:
        logger.warning("security.trusted_proxies produced no valid network; using defaults")
    return _trusted_networks


def _is_trusted_proxy(host: str) -> bool:
    """True when the direct peer is one of our own reverse proxies.

    Only then may we believe X-Real-IP / X-Forwarded-For. Trusting those
    headers unconditionally would let any client forge its IP and escape its
    rate-limit bucket, so the peer address is the gate.

    Handles the IPv4-mapped IPv6 form (``::ffff:127.0.0.1``) that uvicorn
    reports when it binds a dual-stack socket — otherwise a perfectly local
    proxy looks untrusted and the collapse comes back.
    """
    # No peer at all: nothing to attribute the request to, so headers are the
    # only thing left. Accept them rather than collapsing everything onto a
    # single bucket. A bare "localhost" is accepted as loopback.
    if not host:
        return True
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal (a hostname, a Unix-socket path): do not trust it.
        return False
    mapped = getattr(addr, "ipv4_mapped", None) or addr
    return any(mapped in net for net in _get_trusted_networks())


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

    Security note: the client IP must be *unspoofable*, otherwise the limiter
    it feeds is trivially bypassed. See the ordering below.
    """
    peer = request.client.host if request.client else None
    if not peer or not _is_trusted_proxy(peer):
        return peer or "127.0.0.1"

    # 1. X-Real-IP. nginx sets it from $remote_addr (the socket peer) and
    #    overwrites anything the client sent, so this is the trustworthy value.
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip

    # 2. X-Forwarded-For, reading the RIGHTMOST entry only.
    #    nginx's $proxy_add_x_forwarded_for builds
    #    "<whatever the client sent>, $remote_addr", so the rightmost entry is
    #    always the true peer and a forged prefix cannot win. Taking the
    #    leftmost entry instead — the naive "original client" reading — would
    #    let any caller pick their own rate-limit bucket with a one-line curl.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip() or peer

    return peer


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
