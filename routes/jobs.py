"""
Job management endpoints — list, get, cancel, delete search jobs.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.auth import get_is_admin, require_api_key, require_client_id, require_client_id_sse
from core.job_manager import job_manager, JobStatus
from models.schemas import JobResponse, JobListResponse, JobType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _check_ownership(job: dict, client_id: str, is_admin: bool) -> None:
    if not is_admin and job["client_id"] != client_id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/jobs", response_model=JobListResponse, status_code=200)
async def list_jobs(
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
    status: Optional[JobStatus] = Query(None, description="Filter by status"),
    type: Optional[JobType] = Query(None, description="Filter by job type: search, capture, screenshot"),
    limit: int = Query(20, ge=1, le=100, description="Max jobs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> JobListResponse:
    """List jobs scoped to the requesting client. API key holders see all jobs."""
    effective_client_id = None if is_admin else client_id
    jobs, total = job_manager.list_jobs(
        client_id=effective_client_id,
        status=status.value if status else None,
        job_type=type.value if type else None,
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
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
    limit: int = Query(20, ge=1, le=200, description="Max results to return"),
) -> JobResponse:
    """Get job details. Paginate search results with ?offset=N&limit=N."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _check_ownership(job, client_id, is_admin)
    if job.get("result") and "results" in job["result"]:
        all_results = job["result"]["results"]
        job["result"]["results"] = all_results[offset: offset + limit]
        job["result"]["offset"] = offset
        job["result"]["limit"] = limit
    return JobResponse(**job)


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    client_id: str = Depends(require_client_id_sse),
    is_admin: bool = Depends(get_is_admin),
    interval: float = Query(2.0, ge=0.5, le=30, description="Poll interval in seconds"),
) -> StreamingResponse:
    """Stream job status as Server-Sent Events. Closes automatically on terminal state."""

    async def _events() -> AsyncGenerator[str, None]:
        job = await asyncio.to_thread(job_manager.get_job, job_id)
        if not job:
            yield f"event: error\ndata: {json.dumps({'detail': f'Job {job_id} not found'})}\n\n"
            return
        try:
            _check_ownership(job, client_id, is_admin)
        except HTTPException as exc:
            yield f"event: error\ndata: {json.dumps({'detail': exc.detail})}\n\n"
            return

        last_state = (None, None)
        while True:
            status = job["status"]
            current_state = (status, job.get("progress"))
            if current_state != last_state:
                last_state = current_state
                yield f"data: {json.dumps(job, default=str)}\n\n"

            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                return

            await asyncio.sleep(interval)
            job = await asyncio.to_thread(job_manager.get_job, job_id)
            if not job:
                return

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/stream-token", status_code=200)
async def create_stream_token(
    job_id: str,
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
) -> dict:
    """
    Mint a short-lived (60s) single-use token for SSE streaming.
    EventSource cannot send headers, so the token is passed as ?token= in the URL.
    """
    from core.stream_tokens import mint
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _check_ownership(job, client_id, is_admin)
    token = mint(client_id, job_id)
    return {"token": token, "expires_in": 60}


@router.post("/jobs/{job_id}/cancel", status_code=200)
async def cancel_job(
    job_id: str,
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
    _: None = Depends(require_api_key),
):
    """Cancel a queued job. Running jobs cannot be cancelled."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _check_ownership(job, client_id, is_admin)
    if job_manager.cancel_job(job_id):
        return {"job_id": job_id, "status": "cancelled"}
    raise HTTPException(
        status_code=409,
        detail=f"Job {job_id} is {job['status']} and cannot be cancelled (only queued jobs can be cancelled)",
    )


@router.delete("/jobs", status_code=200)
async def delete_all_jobs(
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
    status: Optional[JobStatus] = Query(None, description="Filter by status (default: all terminal)"),
    _: None = Depends(require_api_key),
) -> dict:
    """Delete completed/failed/cancelled jobs scoped to the requesting client. API key holders delete across all clients."""
    effective_client_id = None if is_admin else client_id
    count = job_manager.delete_all_jobs(
        status=status.value if status else None,
        client_id=effective_client_id,
    )
    return {"deleted": count}


@router.delete("/jobs/{job_id}", status_code=200)
async def delete_job(
    job_id: str,
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
    _: None = Depends(require_api_key),
):
    """Delete a completed, failed, or cancelled job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _check_ownership(job, client_id, is_admin)
    if job_manager.delete_job(job_id):
        return {"job_id": job_id, "deleted": True}
    raise HTTPException(
        status_code=409,
        detail=f"Job {job_id} is {job['status']} — only completed/failed/cancelled jobs can be deleted",
    )
