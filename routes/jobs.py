"""
Job management endpoints — list, get, cancel, delete search jobs.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Header, Query
from fastapi.responses import StreamingResponse

from core.job_manager import job_manager, JobStatus
from models.schemas import JobResponse, JobListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["jobs"])


@router.get("/jobs", response_model=JobListResponse, status_code=200)
async def list_jobs(
    status: Optional[JobStatus] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100, description="Max jobs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    x_client_id: Optional[str] = Header(None, description="Filter jobs by client ID"),
) -> JobListResponse:
    """List jobs, optionally filtered by client ID and/or status."""
    jobs, total = job_manager.list_jobs(
        client_id=x_client_id,
        status=status.value if status else None,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(
        jobs=[JobResponse(**j) for j in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse, status_code=200)
async def get_job(
    job_id: str,
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
    limit: int = Query(20, ge=1, le=200, description="Max results to return"),
) -> JobResponse:
    """Get job details. Paginate search results with ?offset=N&limit=N."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.get("result") and "results" in job["result"]:
        all_results = job["result"]["results"]
        job["result"]["results"] = all_results[offset: offset + limit]
        job["result"]["offset"] = offset
        job["result"]["limit"] = limit
    return JobResponse(**job)


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    interval: float = Query(2.0, ge=0.5, le=30, description="Poll interval in seconds"),
) -> StreamingResponse:
    """Stream job status as Server-Sent Events. Closes automatically on terminal state."""

    async def _events() -> AsyncGenerator[str, None]:
        last_status = None
        while True:
            job = await asyncio.to_thread(job_manager.get_job, job_id)
            if not job:
                yield f"event: error\ndata: {json.dumps({'detail': f'Job {job_id} not found'})}\n\n"
                return

            status = job["status"]
            if status != last_status:
                last_status = status
                yield f"data: {json.dumps(job, default=str)}\n\n"

            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                return

            await asyncio.sleep(interval)

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/cancel", status_code=200)
def cancel_job(job_id: str):
    """Cancel a queued job. Running jobs cannot be cancelled."""
    if job_manager.cancel_job(job_id):
        return {"job_id": job_id, "status": "cancelled"}
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    raise HTTPException(
        status_code=409,
        detail=f"Job {job_id} is {job['status']} and cannot be cancelled (only queued jobs can be cancelled)",
    )


@router.delete("/jobs", status_code=200)
def delete_all_jobs(
    status: Optional[JobStatus] = Query(None, description="Filter by status (default: all terminal)"),
) -> dict:
    """Delete all completed/failed/cancelled jobs. Pass ?status= to restrict."""
    count = job_manager.delete_all_jobs(
        status.value if status else None,
    )
    return {"deleted": count}


@router.delete("/jobs/{job_id}", status_code=200)
def delete_job(job_id: str):
    """Delete a completed, failed, or cancelled job."""
    if job_manager.delete_job(job_id):
        return {"job_id": job_id, "deleted": True}
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    raise HTTPException(
        status_code=409,
        detail=f"Job {job_id} is {job['status']} — only completed/failed/cancelled jobs can be deleted",
    )
