"""
Health check endpoint.
"""
from fastapi import APIRouter, Request
from datetime import datetime, timezone

from models.schemas import HealthResponse
from core.tor_client import tor_client
from core.rate_limiter import limiter

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", response_model=HealthResponse, status_code=200)
@limiter.exempt
def health_check(request: Request) -> HealthResponse:
    """
    Health check endpoint to verify API and Tor connectivity.
    
    Returns:
        HealthResponse with status and Tor connection info
    """
    tor_connected = tor_client.test_connection()
    
    return HealthResponse(
        status="ok",
        tor_connected=tor_connected,
        timestamp=datetime.now(timezone.utc)
    )
