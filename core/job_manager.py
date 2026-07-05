"""
Job manager using SQLite for persistence and ThreadPoolExecutor for workers.
Zero external dependencies — uses only Python stdlib.

Upgrade path:
  - Phase 1 (current): SQLite + threads (single container)
  - Phase 2: Swap SQLite for Redis (add persistence + pub/sub)
  - Phase 3: Swap ThreadPoolExecutor for Celery workers (multi-container scaling)
"""
import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from core.auth import generate_job_id, generate_search_job_id, generate_capture_job_id, generate_screenshot_job_id
from core.db_mixin import SqliteMixin
from core.tor_client import tor_client
from core.crawler import OnionCrawler, resolve_search_url
from core.webhook_manager import webhook_manager

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobManager(SqliteMixin):
    """
    Manages search jobs with SQLite persistence and a thread pool.

    Thread-safe: each thread gets its own SQLite connection via _get_conn().
    The ThreadPoolExecutor runs sync crawl_and_search() without blocking
    the FastAPI event loop.
    """

    def __init__(self, max_workers: int = 2, db_path: str = "/app/data/jobs.db"):
        self.db_path = db_path
        self.max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._capture_progress: Dict[str, Dict[str, int]] = {}
        self._search_progress: Dict[str, Dict[str, int]] = {}
        self.__init_sqlite__()
        self._init_db()

    def _init_db(self) -> None:
        """Create jobs table if it doesn't exist."""
        self._ensure_db_dir()

        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'search',
                client_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                request TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        """)
        # Migrate existing DBs that lack the type column
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN type TEXT NOT NULL DEFAULT 'search'")
        except Exception:
            pass  # column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_client_status
            ON jobs(client_id, status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_type
            ON jobs(type)
        """)
        conn.commit()

    # ── Lifecycle ───────────────────────────────────────────────────

    def startup(self) -> None:
        """Start the thread pool and recover interrupted jobs."""
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="crawl-worker"
        )
        # Mark jobs that were running when the process died as failed
        conn = self._get_conn()
        conn.execute(
            "UPDATE jobs SET status = ?, error = ? WHERE status = ?",
            (JobStatus.FAILED, "Server restarted during execution", JobStatus.RUNNING),
        )
        # Re-queue jobs that were queued but never started
        rows = conn.execute(
            "SELECT id, request, client_id FROM jobs WHERE status = ?",
            (JobStatus.QUEUED,),
        ).fetchall()
        conn.commit()

        for row in rows:
            logger.info(f"Re-queuing job {row['id']} from previous session")
            self._submit_to_pool(row["id"])

        logger.info(
            f"JobManager started: {self.max_workers} workers, "
            f"{len(rows)} jobs re-queued"
        )

    def shutdown(self) -> None:
        """Graceful shutdown: finish running jobs, close all connections."""
        if self._executor:
            logger.info("Shutting down job workers (waiting for running jobs)...")
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        self._close_all_connections()

    # ── Public API ──────────────────────────────────────────────────

    def submit_job(self, request_data: dict, client_id: str = "") -> str:
        """
        Enqueue a new search job.

        Args:
            request_data: Validated SearchRequest as dict
            client_id: Optional client identifier

        Returns:
            job_id (UUID string)
        """
        job_id = generate_search_job_id()
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO jobs (id, type, client_id, status, request, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, "search", client_id, JobStatus.QUEUED, json.dumps(request_data), now),
        )
        conn.commit()

        self._submit_to_pool(job_id)
        logger.info(f"Job {job_id} queued for client '{client_id}'")
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by ID. Returns None if not found."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = self._row_to_dict(row)
        if d.get("status") == JobStatus.RUNNING:
            if job_id in self._capture_progress:
                d["progress"] = self._capture_progress[job_id]
            elif job_id in self._search_progress:
                d["progress"] = self._search_progress[job_id]
        return d

    def list_jobs(
        self,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List jobs with optional filtering. Returns (jobs, total_matching)."""
        where = "WHERE 1=1"
        params: list = []

        if client_id is not None:
            where += " AND client_id = ?"
            params.append(client_id)
        if status is not None:
            where += " AND status = ?"
            params.append(status)
        if job_type is not None:
            where += " AND type = ?"
            params.append(job_type)

        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_dict(row) for row in rows], total

    def get_job_by_storage_key(self, storage_key: str, job_type: str) -> Optional[Dict[str, Any]]:
        """Find a completed job by its result.storage_key. Returns None if not found."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE type = ? AND status = ? AND result LIKE ?",
            (job_type, JobStatus.COMPLETED, f'%"{storage_key}"%'),
        ).fetchall()
        for row in rows:
            d = self._row_to_dict(row)
            if d.get("result", {}).get("storage_key") == storage_key:
                return d
        return None

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a queued job. Running jobs cannot be cancelled (limitation of
        sync crawl_and_search — no cancellation token yet).

        Returns:
            True if job was cancelled, False otherwise.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "UPDATE jobs SET status = ? WHERE id = ? AND status = ?",
            (JobStatus.CANCELLED, job_id, JobStatus.QUEUED),
        )
        conn.commit()
        cancelled = cursor.rowcount > 0
        if cancelled:
            logger.info(f"Job {job_id} cancelled")
        return cancelled

    def delete_job(self, job_id: str) -> bool:
        """Delete a completed/failed/cancelled job."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM jobs WHERE id = ? AND status IN (?, ?, ?)",
            (job_id, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_all_jobs(self, status: Optional[str] = None, client_id: Optional[str] = None) -> int:
        """Delete all terminal jobs, optionally filtered by status and/or client_id. Returns deleted count."""
        conn = self._get_conn()
        terminal = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
        clauses: list = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append(f"status IN ({','.join('?' * len(terminal))})")
            params.extend(terminal)
        if client_id:
            clauses.append("client_id = ?")
            params.append(client_id)
        cursor = conn.execute(f"DELETE FROM jobs WHERE {' AND '.join(clauses)}", params)
        conn.commit()
        return cursor.rowcount

    def submit_capture_job(self, request_data: dict, client_id: str = "") -> str:
        """Enqueue a new capture job. Returns job_id."""
        job_id = generate_capture_job_id()
        now = datetime.now(timezone.utc).isoformat()
        request_data = {
            **request_data,
            "_job_type": "capture",
            "max_pages": min(request_data.get("max_pages", 20), settings.capture_max_pages),
            "max_size_mb": min(request_data.get("max_size_mb", settings.capture_max_size_mb), settings.capture_max_size_mb),
            "max_depth": min(request_data.get("max_depth", 2), 5),
            "timeout": min(max(request_data.get("timeout", settings.default_timeout), 10), 120),
        }
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO jobs (id, type, client_id, status, request, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, "capture", client_id, JobStatus.QUEUED, json.dumps(request_data), now),
        )
        conn.commit()
        self._submit_to_pool(job_id)
        logger.info(f"Capture job {job_id} queued for client '{client_id}'")
        return job_id

    def submit_screenshot_job(self, request_data: dict, client_id: str = "") -> str:
        """Enqueue a new screenshot job. Returns job_id."""
        job_id = generate_screenshot_job_id()
        now = datetime.now(timezone.utc).isoformat()
        request_data = {
            **request_data,
            "_job_type": "screenshot",
            "timeout": min(max(request_data.get("timeout", settings.default_timeout), 10), 120),
        }
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO jobs (id, type, client_id, status, request, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, "screenshot", client_id, JobStatus.QUEUED, json.dumps(request_data), now),
        )
        conn.commit()
        self._submit_to_pool(job_id)
        logger.info(f"Screenshot job {job_id} queued for client '{client_id}'")
        return job_id

    # ── Internal ────────────────────────────────────────────────────

    def _submit_to_pool(self, job_id: str) -> None:
        """Submit a job to the thread pool for execution."""
        if not self._executor:
            logger.error("Cannot submit job: executor not started")
            return
        self._executor.submit(self._execute_job, job_id)

    def _execute_job(self, job_id: str) -> None:
        """
        Worker function: runs in a thread.
        Fetches job from DB, runs crawl_and_search, stores result.
        """
        conn = self._get_conn()

        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row or row["status"] != JobStatus.QUEUED:
            return

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
            (JobStatus.RUNNING, now, job_id),
        )
        conn.commit()

        job = self._row_to_dict(row)
        request_data = job["request"]
        start_time = time.time()

        try:
            job_type = request_data.get("_job_type")
            if job_type == "capture":
                result_payload = self._run_capture(job_id, request_data)
            elif job_type == "screenshot":
                result_payload = self._run_screenshot(job_id, request_data)
            else:
                result_payload = self._run_search(job_id, request_data, start_time)

            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET status = ?, result = ?, completed_at = ? WHERE id = ?",
                (JobStatus.COMPLETED, json.dumps(result_payload, default=str), completed_at, job_id),
            )
            conn.commit()
            logger.info(f"Job {job_id} completed")

            job_dict = self.get_job(job_id)
            webhook_manager.send_webhook(
                job_id=job_id,
                client_id=job["client_id"],
                status=JobStatus.COMPLETED,
                job_data=job_dict or {},
                result=result_payload,
            )

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
                (JobStatus.FAILED, str(e), completed_at, job_id),
            )
            conn.commit()

            job_dict = self.get_job(job_id)
            webhook_manager.send_webhook(
                job_id=job_id,
                client_id=job["client_id"],
                status=JobStatus.FAILED,
                job_data=job_dict or {},
                error=str(e),
            )

    def _run_search(self, job_id: str, request_data: dict, start_time: float) -> dict:
        import hashlib
        from pathlib import Path

        term = request_data["term"]
        start_url = request_data["start_url"]
        include_screenshots = request_data.get("include_screenshots", False)
        actual_url = resolve_search_url(start_url, term, tor_client)
        if actual_url != start_url:
            logger.info(f"Job {job_id}: search engine detected, crawling {actual_url}")

        screenshots_dir: Optional[Path] = None
        if include_screenshots:
            screenshots_dir = Path(settings.capture_output_dir) / "screenshots" / job_id
            screenshots_dir.mkdir(parents=True, exist_ok=True)

        self._search_progress[job_id] = {"pages": 0, "results": 0}

        def _on_search_progress(pages: int, results_found: int) -> None:
            self._search_progress[job_id] = {"pages": pages, "results": results_found}

        crawler = OnionCrawler(tor_client)
        try:
            results, total_crawled = crawler.crawl_and_search(
                start_url=actual_url,
                search_term=term,
                max_depth=request_data.get("max_depth", settings.default_max_depth),
                max_pages=request_data.get("max_pages", settings.default_max_pages),
                max_results=request_data.get("max_results", settings.default_max_results),
                timeout=request_data.get("timeout", settings.default_timeout),
                progress_cb=_on_search_progress,
            )
        finally:
            self._search_progress.pop(job_id, None)

        if include_screenshots and screenshots_dir is not None:
            from core.screenshot import ScreenshotSession
            with ScreenshotSession() as session:
                for result in results:
                    url_hash = hashlib.sha256(result["url"].encode()).hexdigest()[:16]
                    output_path = screenshots_dir / f"{url_hash}.png"
                    if session.take(result["url"], output_path):
                        result["screenshot_path"] = f"/api/v1/jobs/{job_id}/screenshots/{url_hash}.png"

        duration = round(time.time() - start_time, 2)
        return {
            "term": term,
            "results": results,
            "total": len(results),
            "crawled_pages": total_crawled,
            "duration_seconds": duration,
            "tor_connected": True,
            "start_url": actual_url,
        }

    def _run_screenshot(self, job_id: str, request_data: dict) -> dict:
        from pathlib import Path
        from urllib.parse import urlparse
        from core.screenshot import take_screenshot

        start_url = request_data["start_url"]
        onion_prefix = urlparse(start_url).hostname.replace(".onion", "")[:10]
        storage_key = f"{onion_prefix}-{job_id}"

        screenshots_dir = Path(settings.capture_output_dir) / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        output_path = screenshots_dir / f"{storage_key}.png"

        if not tor_client.check_reachable(start_url):
            raise RuntimeError(f"Target unreachable via Tor: {start_url}")

        timeout_ms = request_data.get("timeout", settings.default_timeout) * 1000
        if not take_screenshot(start_url, output_path, timeout_ms=timeout_ms):
            raise RuntimeError(f"Screenshot failed for {start_url}")

        size_bytes = output_path.stat().st_size
        download_url = f"/api/v1/screenshots/{storage_key}/download"
        return {
            "url": start_url,
            "storage_key": storage_key,
            "download_url": download_url,
            "size_bytes": size_bytes,
        }

    def _run_capture(self, job_id: str, request_data: dict) -> dict:
        from core.capture import get_capture_provider
        from urllib.parse import urlparse

        self._capture_progress[job_id] = {"pages": 0, "assets": 0, "size_bytes": 0}

        def _on_progress(pages: int, assets: int, size_bytes: int) -> None:
            self._capture_progress[job_id] = {"pages": pages, "assets": assets, "size_bytes": size_bytes}

        start_url = request_data["start_url"]
        onion_prefix = urlparse(start_url).hostname.replace(".onion", "")[:10]
        label = request_data.get("label")
        archive_name = f"{label}-{onion_prefix}-{job_id}" if label else f"{onion_prefix}-{job_id}"

        if not tor_client.check_reachable(start_url):
            raise RuntimeError(f"Target unreachable via Tor: {start_url}")

        provider = get_capture_provider()
        try:
            result = provider.capture(
                job_id=job_id,
                start_url=start_url,
                max_pages=request_data.get("max_pages", 20),
                max_depth=request_data.get("max_depth", 2),
                timeout=request_data.get("timeout", settings.default_timeout),
                max_size_mb=request_data.get("max_size_mb", settings.capture_max_size_mb),
                archive_name=archive_name,
                progress_cb=_on_progress,
            )
        finally:
            self._capture_progress.pop(job_id, None)

        download_url = provider.get_download_url(result.storage_key)
        return {
            "url": result.url,
            "pages_captured": result.pages_captured,
            "assets_captured": result.assets_captured,
            "size_bytes": result.size_bytes,
            "storage_key": result.storage_key,
            "download_url": download_url,
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a SQLite Row to a clean dict, parsing JSON fields."""
        d = dict(row)
        if d.get("request"):
            d["request"] = json.loads(d["request"])
        if d.get("result"):
            d["result"] = json.loads(d["result"])
        return d


# Singleton — initialized in main.py lifespan
job_manager = JobManager(
    max_workers=settings.job_max_workers,
    db_path=settings.job_db_path,
)
