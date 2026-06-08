"""
Pydantic schemas for request/response validation.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from core.constants import ONION_URL_REGEX


class SearchRequest(BaseModel):
    """Request model for search endpoint."""
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "term": "cybersec",
                    "max_results": 10,
                    "max_depth": 2,
                    "max_pages": 50,
                    "timeout": 30
                }
            ]
        }
    }
    
    term: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Search term to find in .onion sites"
    )
    start_url: Optional[str] = Field(
        None,
        description="Starting .onion URL for crawling. If not provided, will use default seed URL from config.yaml"
    )
    max_results: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum number of results to return"
    )
    max_depth: int = Field(
        2,
        ge=1,
        le=5,
        description="Maximum crawl depth"
    )
    max_pages: int = Field(
        50,
        ge=1,
        le=200,
        description="Maximum pages to crawl"
    )
    timeout: int = Field(
        30,
        ge=10,
        le=120,
        description="Request timeout in seconds"
    )
    
    @field_validator('start_url')
    @classmethod
    def validate_onion_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not ONION_URL_REGEX.match(v):
            raise ValueError('Invalid .onion URL format. Must be a valid Tor v3 address.')
        return v.lower()


class SearchResult(BaseModel):
    """A single search result from a crawled or SERP page."""

    url: str = Field(..., description="URL of the matching .onion page")
    title: str = Field(..., description="Page title")
    snippet: str = Field(..., description="Text excerpt around the match")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp when page was crawled")
    seed: str = Field(..., description="Search engine or seed URL that led to this result")
    depth: int = Field(..., description="Crawl depth from the seed URL")
    term_count: int = Field(..., description="Number of times the search term appeared")


class SearchResultsPayload(BaseModel):
    """Payload stored in result field when a job completes."""

    term: str
    results: List[SearchResult]
    total: int
    crawled_pages: int
    duration_seconds: float
    tor_connected: bool
    start_url: str


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    
    status: str = Field(..., description="API health status")
    tor_connected: bool = Field(..., description="Tor connectivity status")
    timestamp: datetime = Field(..., description="Current server timestamp")


# ── Job models ──────────────────────────────────────────────────────


class JobCreatedResponse(BaseModel):
    """Response returned when a search job is submitted (202 Accepted)."""

    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field("queued", description="Initial job status")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    poll_url: str = Field(..., description="URL to poll for job status")


class JobResponse(BaseModel):
    """Full job detail returned by GET /jobs/{id}."""

    id: str = Field(...)
    client_id: str = Field("", description="Client identifier from X-Client-ID header")
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = Field(...)
    request: Dict[str, Any] = Field(..., description="Original search request parameters")
    result: Optional[SearchResultsPayload] = Field(None, description="Search results (when completed)")
    error: Optional[str] = Field(None, description="Error message (when failed)")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")
    started_at: Optional[str] = Field(None, description="ISO 8601 start timestamp")
    completed_at: Optional[str] = Field(None, description="ISO 8601 completion timestamp")

    model_config = {"populate_by_name": True}


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    jobs: List[JobResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total jobs matching filter")
    limit: int = Field(20)
    offset: int = Field(0)


# ── Webhook models ──────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    """Payload sent to webhook URL when a job completes or fails."""

    event: str = Field(..., description="Event type: 'job.completed' or 'job.failed'")
    job_id: str = Field(..., description="Unique job identifier")
    status: Literal["completed", "failed", "cancelled"] = Field(..., description="Job status")
    client_id: str = Field(..., description="Client identifier")
    created_at: str = Field(..., description="Job creation timestamp")
    completed_at: Optional[str] = Field(None, description="Job completion timestamp")
    result: Optional[Dict[str, Any]] = Field(None, description="Search results (for completed jobs)")
    error: Optional[str] = Field(None, description="Error message (for failed jobs)")
    request: Dict[str, Any] = Field(..., description="Original search request parameters")
    timestamp: str = Field(..., description="Webhook send timestamp")


class WebhookConfig(BaseModel):
    """Webhook configuration for a client."""

    url: str = Field(..., description="Webhook URL to call (must be HTTPS in production)")
    events: List[str] = Field(
        default=["job.completed", "job.failed"],
        description="Events to subscribe to"
    )
    secret: Optional[str] = Field(
        None,
        description="Optional secret for HMAC signature verification"
    )
    active: bool = Field(default=True, description="Whether webhook is active")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        from urllib.parse import urlparse
        from config import settings
        parsed = urlparse(v)
        if not all([parsed.scheme, parsed.netloc]):
            raise ValueError("Invalid webhook URL")
        if not settings.webhook_allow_insecure_urls and parsed.scheme != "https":
            raise ValueError("Webhook URL must be HTTPS in production")
        if not parsed.hostname:
            raise ValueError("Invalid webhook URL: missing host")
        return v


class WebhookConfigResponse(BaseModel):
    """Response for webhook configuration endpoints."""

    client_id: str = Field(..., description="Client identifier")
    url: str = Field(..., description="Webhook URL")
    events: List[str] = Field(..., description="Subscribed events")
    has_secret: bool = Field(..., description="Whether an HMAC secret is configured")
    active: bool = Field(..., description="Whether webhook is active")
    created_at: str = Field(..., description="Configuration creation timestamp")


class WebhookDelivery(BaseModel):
    """Webhook delivery attempt record."""

    id: str = Field(..., description="Delivery attempt ID")
    job_id: str = Field(..., description="Job ID that triggered the webhook")
    client_id: str = Field(..., description="Client identifier")
    url: str = Field(..., description="Webhook URL")
    event: str = Field(..., description="Event type")
    status: Literal["success", "failed", "retrying"] = Field(..., description="Delivery status")
    attempt: int = Field(..., description="Attempt number")
    response_status: Optional[int] = Field(None, description="HTTP response status code")
    response_text: Optional[str] = Field(None, description="HTTP response text")
    error: Optional[str] = Field(None, description="Error message if failed")
    sent_at: str = Field(..., description="Timestamp when webhook was sent")
