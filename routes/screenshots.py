"""
Screenshot endpoints — take viewport screenshots of .onion pages via Playwright + Tor.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from config import settings
from core.auth import get_is_admin, require_client_id
from core.job_manager import job_manager
from core.rate_limiter import limiter
from models.schemas import ScreenshotRequest, JobCreatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["screenshots"])

_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-]+\.png$")


@router.post("/screenshots", response_model=JobCreatedResponse, status_code=202)
@limiter.limit("10/minute;60/hour")
async def screenshot_onion_site(
    body: ScreenshotRequest,
    request: Request,
    client_id: str = Depends(require_client_id),
) -> JobCreatedResponse:
    """
    Submit a screenshot job for a .onion page.

    Returns immediately with a job_id. Poll GET /api/v1/jobs/{job_id} for status,
    then download via GET /api/v1/screenshots/{storage_key}/download.
    """
    job_id = job_manager.submit_screenshot_job(body.model_dump(), client_id=client_id)
    now = datetime.now(timezone.utc).isoformat()
    logger.info(f"Screenshot job {job_id} queued for {body.start_url} (client={client_id})")
    return JobCreatedResponse(
        job_id=job_id,
        client_id=client_id,
        status="queued",
        created_at=now,
        poll_url=f"/api/v1/jobs/{job_id}",
    )


@router.get("/screenshots/{storage_key}/download")
@limiter.limit("20/minute")
async def download_screenshot(
    request: Request,
    storage_key: str,
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
) -> FileResponse:
    """Download the PNG produced by a completed screenshot job."""
    if not re.match(r"^[a-zA-Z0-9_\-]+$", storage_key):
        raise HTTPException(status_code=404, detail="Screenshot not found")

    job = job_manager.get_job_by_storage_key(storage_key, "screenshot")
    if not job or (not is_admin and job["client_id"] != client_id):
        raise HTTPException(status_code=404, detail="Screenshot not found")

    screenshots_dir = (Path(settings.capture_output_dir) / "screenshots").resolve()
    resolved = (screenshots_dir / f"{storage_key}.png").resolve()
    if not resolved.is_relative_to(screenshots_dir) or not resolved.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")

    return FileResponse(
        path=str(resolved),
        media_type="image/png",
        filename=f"{storage_key}.png",
    )


@router.get("/jobs/{job_id}/screenshots/{filename}")
@limiter.limit("20/minute")
async def get_search_screenshot(
    request: Request,
    job_id: str,
    filename: str,
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
) -> FileResponse:
    """Serve a per-page screenshot from a search job."""
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=404, detail="Screenshot not found")

    job = job_manager.get_job(job_id)
    if not job or (not is_admin and job["client_id"] != client_id):
        raise HTTPException(status_code=404, detail="Screenshot not found")

    base = (Path(settings.capture_output_dir) / "screenshots").resolve()
    resolved = (base / job_id / filename).resolve()
    if not resolved.is_relative_to(base) or not resolved.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")

    return FileResponse(path=str(resolved), media_type="image/png")
