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
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from core.tor_client import tor_client
from core.crawler import OnionCrawler, effective_start_url, resolve_search_url

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobManager:
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
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._conn_lock = threading.Lock()
        self._init_db()

    # ── SQLite helpers ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            with self._conn_lock:
                self._connections.append(conn)
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        """Create jobs table if it doesn't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_client_status
            ON jobs(client_id, status)
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
        # Close all thread-local connections
        with self._conn_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
        self._local = threading.local()

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
        job_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO jobs (id, client_id, status, request, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, client_id, JobStatus.QUEUED, json.dumps(request_data), now),
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
        return self._row_to_dict(row)

    def list_jobs(
        self,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
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

        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_dict(row) for row in rows], total

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

    def delete_all_jobs(self, status: Optional[str] = None) -> int:
        """Delete all terminal jobs, optionally filtered by status. Returns deleted count."""
        conn = self._get_conn()
        if status:
            cursor = conn.execute("DELETE FROM jobs WHERE status = ?", (status,))
        else:
            cursor = conn.execute(
                "DELETE FROM jobs WHERE status IN (?, ?, ?)",
                (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED),
            )
        conn.commit()
        return cursor.rowcount

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
            term = request_data["term"]
            start_url = request_data["start_url"]
            actual_url = resolve_search_url(start_url, term, tor_client)
            if actual_url != start_url:
                logger.info(f"Job {job_id}: search engine detected, crawling {actual_url}")
            tor_connected = tor_client.test_connection()
            crawler = OnionCrawler(tor_client)
            results, total_crawled = crawler.crawl_and_search(
                start_url=actual_url,
                search_term=term,
                max_depth=request_data.get("max_depth", settings.default_max_depth),
                max_pages=request_data.get("max_pages", settings.default_max_pages),
                max_results=request_data.get("max_results", settings.default_max_results),
                timeout=request_data.get("timeout", settings.default_timeout),
            )

            duration = round(time.time() - start_time, 2)

            result_payload = {
                "term": term,
                "results": results,
                "total": len(results),
                "crawled_pages": total_crawled,
                "duration_seconds": duration,
                "tor_connected": tor_connected,
                "start_url": actual_url,
            }

            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET status = ?, result = ?, completed_at = ? WHERE id = ?",
                (JobStatus.COMPLETED, json.dumps(result_payload, default=str), completed_at, job_id),
            )
            conn.commit()
            logger.info(f"Job {job_id} completed: {len(results)} results in {duration}s")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
                (JobStatus.FAILED, str(e), completed_at, job_id),
            )
            conn.commit()

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
