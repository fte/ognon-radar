"""
Search endpoint for .onion site crawling.
Submits search jobs to the async queue and returns immediately.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import ValidationError

from config import settings
from core.crawler import is_valid_onion_url
from core.job_manager import job_manager
from models.schemas import SearchRequest, JobCreatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search", response_model=JobCreatedResponse, status_code=202)
async def search_onion_sites(
    request: Request,
    x_client_id: Optional[str] = Header(None, description="Client identifier for job tracking"),
) -> JobCreatedResponse:
    """
    Submit a search job for .onion sites containing a specific term.

    Returns immediately with a job ID. Poll GET /api/v1/jobs/{job_id}
    for status and results.

    Args:
        request: HTTP request containing search parameters as JSON
        x_client_id: Optional client identifier header

    Returns:
        JobCreatedResponse with job_id and poll URL (202 Accepted)
    """
    # Accept JSON payloads even when clients omit Content-Type (e.g. curl -d)
    try:
        payload = await request.json()
    except (JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Request body must be valid JSON",
        )

    try:
        search_request = SearchRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False))

    # Pydantic already validates user-supplied start_url; only seed fallback needs runtime validation
    start_url = search_request.start_url
    if not start_url:
        if not settings.seed_urls:
            raise HTTPException(
                status_code=400,
                detail="No start_url provided and no seed URLs configured in config.yaml. "
                       "Please provide a start_url in the request body or add seed_urls to config.yaml.",
            )
        start_url = settings.seed_urls[0]
        if not is_valid_onion_url(start_url):
            raise HTTPException(
                status_code=500,
                detail=f"Misconfigured seed URL in config.yaml: {start_url}",
            )

    request_data = search_request.model_dump()
    request_data["start_url"] = start_url

    client_id = x_client_id or ""
    job_id = job_manager.submit_job(request_data, client_id=client_id)

    now = datetime.now(timezone.utc).isoformat()
    logger.info(f"Search job {job_id} queued for term='{search_request.term}' client='{client_id}'")

    return JobCreatedResponse(
        job_id=job_id,
        status="queued",
        created_at=now,
        poll_url=f"/api/v1/jobs/{job_id}",
    )
