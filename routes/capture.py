"""
Capture endpoint — archive full .onion sites to WARC files.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import settings
from core.job_manager import job_manager
from models.schemas import CaptureRequest, JobCreatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["capture"])


@router.post("/capture", response_model=JobCreatedResponse, status_code=202)
async def capture_onion_site(request: CaptureRequest) -> JobCreatedResponse:
    """
    Submit a capture job for a .onion site.

    Downloads all pages and assets (images, CSS, JS) into a .warc.gz archive.
    Returns immediately with a job_id. Poll GET /api/v1/jobs/{job_id} for status,
    then download via GET /api/v1/captures/{job_id}/download.
    """
    job_id = job_manager.submit_capture_job(request.model_dump())
    now = datetime.now(timezone.utc).isoformat()
    logger.info(f"Capture job {job_id} queued for {request.start_url}")
    return JobCreatedResponse(
        job_id=job_id,
        client_id="",
        status="queued",
        created_at=now,
        poll_url=f"/api/v1/jobs/{job_id}",
    )


@router.get("/captures/{job_id}/download")
async def download_capture(job_id: str) -> FileResponse:
    """
    Download the .warc.gz archive produced by a completed capture job.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Job is {job['status']}, not completed")

    output_dir = Path(settings.capture_output_dir).resolve()
    resolved = (output_dir / f"{job_id}.warc.gz").resolve()
    if not resolved.is_relative_to(output_dir):
        raise HTTPException(status_code=403, detail="Invalid archive path")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Archive file not found")

    return FileResponse(
        path=str(resolved),
        media_type="application/gzip",
        filename=f"{job_id}.warc.gz",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.warc.gz"'},
    )
