"""
Health check endpoint.
"""
from fastapi import APIRouter
from datetime import datetime, timezone

from models.schemas import HealthResponse
from core.tor_client import tor_client

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", response_model=HealthResponse, status_code=200)
def health_check() -> HealthResponse:
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
