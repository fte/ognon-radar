"""
Job management endpoints — list, get, cancel, delete search jobs.
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from core.auth import (
    get_is_admin,
    require_api_key,
    require_client_id,
    require_client_id_sse,
    _resolve_client_id,
)
from core.exporters import export_filename, results_to_csv, results_to_xls
from core.job_manager import job_manager, JobStatus
from core.rate_limiter import limiter
from models.schemas import JobResponse, JobListResponse, JobType, PermalinkResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def _check_ownership(job: dict, client_id: str, is_admin: bool) -> None:
    if not is_admin and job["client_id"] != client_id:
        raise HTTPException(status_code=403, detail="Access denied")


def _search_results_publicly_readable(job: dict) -> bool:
    """True when the job is a completed search whose result rows can be read
    without credentials (permalink / export reuse this rule)."""
    return (
        job.get("status") == JobStatus.COMPLETED
        and job.get("type") == "search"
        and isinstance(job.get("result"), dict)
        and isinstance(job["result"].get("results"), list)
    )


def _ensure_read_access(job: dict, x_client_id: Optional[str], x_api_key: Optional[str]) -> None:
    """Credential-free read for completed search results (shareable permalinks);
    ownership check otherwise. Kept deliberately next to _check_ownership."""
    if _search_results_publicly_readable(job):
        return
    if not x_client_id and not x_api_key:
        raise HTTPException(
            status_code=403,
            detail="This job's results are not publicly readable — "
            "send X-Client-ID or X-API-Key",
        )
    client_id = _resolve_client_id(x_client_id, x_api_key)
    _check_ownership(job, client_id, get_is_admin(x_api_key))


@router.get("/jobs", response_model=JobListResponse, status_code=200)
@limiter.limit("60/minute")
async def list_jobs(
    request: Request,
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


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    status_code=200,
    responses={
        422: {
            "model": JobResponse,
            "description": "The job failed — the response body is the full job object, "
            "including the 'error' message.",
        }
    },
)
@limiter.limit("30/minute")
async def get_job(
    request: Request,
    job_id: str,
    client_id: str = Depends(require_client_id),
    is_admin: bool = Depends(get_is_admin),
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
    limit: int = Query(20, ge=1, le=200, description="Max results to return"),
) -> Union[JobResponse, JSONResponse]:
    """Get job details. Paginate search results with ?offset=N&limit=N.

    Terminal failure is surfaced with a non-2xx status so polling clients can
    react without parsing the body: a ``failed`` job returns ``422`` with the
    full job object (including ``error``) as the body. ``queued``, ``running``
    and ``completed`` jobs keep returning ``200``.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _check_ownership(job, client_id, is_admin)
    if job.get("result") and "results" in job["result"]:
        all_results = job["result"]["results"]
        job["result"]["results"] = all_results[offset: offset + limit]
        job["result"]["offset"] = offset
        job["result"]["limit"] = limit
    response = JobResponse(**job)
    if job["status"] == JobStatus.FAILED:
        return JSONResponse(status_code=422, content=response.model_dump())
    return response


@router.get(
    "/jobs/{job_id}/permalink",
    response_model=PermalinkResponse,
    status_code=200,
)
@limiter.limit("30/minute")
async def get_job_permalink(
    request: Request,
    job_id: str,
    x_client_id: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
    limit: int = Query(20, ge=1, le=200, description="Max results to return"),
) -> PermalinkResponse:
    """Shareable read-only view of a job's results.

    Uses the human-readable job id as the permalink key. The results of a
    **completed search** job are public on purpose — a permalink must be
    openable outside the owner's browser. Everything else (queued jobs,
    captures, screenshots, failed jobs) still requires ownership, via the
    same X-Client-ID / X-API-Key headers as GET /jobs/{job_id}.
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _ensure_read_access(job, x_client_id, x_api_key)

    all_results = (job.get("result") or {}).get("results", []) or []
    term = (job.get("request") or {}).get("term", "")
    return PermalinkResponse(
        job_id=job["id"],
        term=term if job.get("status") == JobStatus.COMPLETED else "",
        status=job["status"],
        created_at=job.get("created_at"),
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        summary={
            "total": (job.get("result") or {}).get("total", len(all_results)),
            "crawled_pages": (job.get("result") or {}).get("crawled_pages", 0),
            "duration_seconds": (job.get("result") or {}).get("duration_seconds"),
        },
        results=all_results[offset: offset + limit],
        offset=offset,
        limit=limit,
        total_results=len(all_results),
    )


@router.get("/jobs/{job_id}/export")
@limiter.limit("30/minute")
async def export_job_results(
    request: Request,
    job_id: str,
    x_client_id: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None),
    format: str = Query("csv", pattern="^(csv|xls)$", description="Export format: csv or xls"),
) -> Response:
    """Export all results of a completed search job as CSV or SpreadsheetML .xls.

    Same read rule as the permalink: public for completed search results,
    ownership-checked otherwise — the export buttons must work from a shared
    permalink too. Exports are unpaginated (the full result list).
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _ensure_read_access(job, x_client_id, x_api_key)
    if not _search_results_publicly_readable(job):
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is {job['status']} ({job.get('type')}) — "
            "only completed search jobs have results to export",
        )

    term = (job.get("request") or {}).get("term", "")
    filename = export_filename(job_id, str(term), format)
    if format == "xls":
        content = results_to_xls(job).encode("utf-8")
        media_type = "application/vnd.ms-excel"
    else:
        content = results_to_csv(job["result"]["results"]).encode("utf-8")
        media_type = "text/csv; charset=utf-8"

    caller = x_api_key if x_api_key else (x_client_id or "anonymous")
    logger.info(f"Export job {job_id} as {format} ({len(content)} bytes, client '{caller}')")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Result-Count": str(len(job["result"]["results"])),
        },
    )


@router.get("/jobs/{job_id}/stream")
# 30/minute et non 5/minute : ce budget est par IP réelle (voir
# core.rate_limiter.get_remote_address) et doit absorber les reconnexions
# legitimes d'un EventSource sur reseau mobile, pas seulement l'ouverture
# initiale du flux.
@limiter.limit("30/minute")
async def stream_job(
    request: Request,
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
            else:
                yield ": ping\n\n"

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
# Un jeton par tentative de connexion : le client en remint un a chaque
# reconnexion SSE (voir openJobStream dans clients/www/app.js).
@limiter.limit("20/minute")
async def create_stream_token(
    request: Request,
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
@limiter.limit("10/minute")
async def cancel_job(
    request: Request,
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
@limiter.limit("10/minute")
async def delete_all_jobs(
    request: Request,
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
@limiter.limit("10/minute")
async def delete_job(
    request: Request,
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
