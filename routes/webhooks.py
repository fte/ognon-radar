"""
Webhook configuration and delivery history endpoints.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from core.auth import require_api_key, require_client_id
from core.webhook_manager import webhook_manager
from models.schemas import WebhookConfig, WebhookConfigResponse

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _check_ssrf(url: str) -> None:
    """Raise HTTPException if the URL resolves to a private/internal address."""
    host = urlparse(url).hostname.rstrip(".").lower()
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                raise HTTPException(status_code=422, detail="Webhook host resolves to a disallowed address")
    except socket.gaierror as exc:
        raise HTTPException(status_code=422, detail=f"Webhook URL validation failed: {exc}") from exc


def _to_response(d: dict) -> WebhookConfigResponse:
    return WebhookConfigResponse(
        client_id=d["client_id"],
        url=d["url"],
        events=d["events"],
        has_secret=bool(d.get("secret")),
        active=d["active"],
        created_at=d["created_at"],
    )


# ── Config endpoints ────────────────────────────────────────────────


@router.put("/config", response_model=WebhookConfigResponse, status_code=200)
async def set_webhook_config(
    body: WebhookConfig,
    client_id: str = Depends(require_client_id),
    _: None = Depends(require_api_key),
):
    """Register or update webhook configuration for a client."""
    await asyncio.to_thread(_check_ssrf, body.url)
    saved = webhook_manager.set_webhook_config(
        client_id=client_id,
        url=body.url,
        events=body.events,
        secret=body.secret,
        active=body.active,
    )
    return _to_response(saved)


@router.get("/config", response_model=WebhookConfigResponse)
async def get_webhook_config(client_id: str = Depends(require_client_id)):
    """Get webhook configuration for a client."""
    config = webhook_manager.get_webhook_config(client_id)
    if not config:
        raise HTTPException(status_code=404, detail="No webhook configured for this client")
    return _to_response(config)


@router.delete("/config", status_code=204)
async def delete_webhook_config(
    client_id: str = Depends(require_client_id),
    _: None = Depends(require_api_key),
):
    """Delete webhook configuration for a client."""
    if not webhook_manager.delete_webhook_config(client_id):
        raise HTTPException(status_code=404, detail="No webhook configured for this client")


# ── Delivery history ────────────────────────────────────────────────


@router.get("/deliveries")
async def list_deliveries(
    client_id: str = Depends(require_client_id),
    job_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List webhook delivery attempts, optionally filtered by job_id or status."""
    deliveries, total = webhook_manager.get_delivery_attempts(
        job_id=job_id,
        client_id=client_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"deliveries": deliveries, "total": total, "limit": limit, "offset": offset}


@router.post("/deliveries/retry", status_code=200)
async def retry_failed_deliveries(
    client_id: str = Depends(require_client_id),
    _: None = Depends(require_api_key),
):
    """Manually trigger a retry of all failed webhook deliveries for this client."""
    retried = await asyncio.to_thread(webhook_manager.retry_failed_deliveries, client_id)
    return {"retried": retried}
