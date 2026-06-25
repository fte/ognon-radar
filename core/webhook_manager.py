"""
Webhook manager for sending job completion notifications.
Handles outgoing webhook calls with retries and HMAC signing.
"""
import hmac
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import settings
from core.db_mixin import SqliteMixin

logger = logging.getLogger(__name__)


class WebhookManager(SqliteMixin):
    """Manages outgoing webhooks for job completion notifications."""

    def __init__(self, db_path: str = "/app/data/webhooks.db"):
        self.db_path = db_path
        self.__init_sqlite__()
        self._init_db()

    def _init_db(self) -> None:
        """Create webhook tables if they don't exist."""
        self._ensure_db_dir()

        conn = self._get_conn()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_configs (
                client_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                events TEXT NOT NULL DEFAULT '[]',
                secret TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                url TEXT NOT NULL,
                event TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt INTEGER NOT NULL DEFAULT 0,
                response_status INTEGER,
                response_text TEXT,
                error TEXT,
                sent_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_job
            ON webhook_deliveries(job_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_client
            ON webhook_deliveries(client_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status
            ON webhook_deliveries(status)
        """)
        conn.commit()

    # ── Lifecycle ───────────────────────────────────────────────────

    def startup(self) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE webhook_deliveries SET status = 'failed', error = 'Server restarted during delivery' "
            "WHERE status IN ('pending', 'retrying')"
        )
        conn.commit()
        logger.info("WebhookManager started")

    def shutdown(self) -> None:
        self._close_all_connections()
        logger.info("WebhookManager shut down")

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _classify_http_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"{type(exc).__name__}: HTTP {exc.response.status_code}"
        return type(exc).__name__

    # ── Row helpers ─────────────────────────────────────────────────

    @staticmethod
    def _config_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "client_id": row["client_id"],
            "url": row["url"],
            "events": json.loads(row["events"]),
            "secret": row["secret"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _delivery_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "client_id": row["client_id"],
            "url": row["url"],
            "event": row["event"],
            "status": row["status"],
            "attempt": row["attempt"],
            "response_status": row["response_status"],
            "response_text": row["response_text"],
            "error": row["error"],
            "sent_at": row["sent_at"],
        }

    # ── Configuration Management ────────────────────────────────────

    def set_webhook_config(
        self,
        client_id: str,
        url: str,
        events: Optional[List[str]] = None,
        secret: Optional[str] = None,
        active: bool = True,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        resolved_events = events or ["job.completed", "job.failed"]

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO webhook_configs
               (client_id, url, events, secret, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (client_id, url, json.dumps(resolved_events), secret, 1 if active else 0, now)
        )
        conn.commit()

        return {
            "client_id": client_id,
            "url": url,
            "events": resolved_events,
            "secret": secret,
            "active": active,
            "created_at": now,
        }

    def get_webhook_config(self, client_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM webhook_configs WHERE client_id = ?", (client_id,)
        ).fetchone()
        return self._config_row_to_dict(row) if row else None

    def delete_webhook_config(self, client_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM webhook_configs WHERE client_id = ?", (client_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    # ── Delivery Management ─────────────────────────────────────────

    def _generate_signature(self, payload: str, secret: str) -> str:
        return hmac.new(
            secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _should_notify(self, client_id: str, event: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Returns (should_notify, webhook_url, secret)."""
        config = self.get_webhook_config(client_id)
        if not config or not config["active"]:
            return False, None, None
        if event in config["events"]:
            return True, config["url"], config["secret"]
        return False, None, None

    def _record_delivery_attempt(
        self,
        delivery_id: str,
        job_id: str,
        client_id: str,
        url: str,
        event: str,
        status: str,
        attempt: int,
        response_status: Optional[int] = None,
        response_text: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO webhook_deliveries
               (id, job_id, client_id, url, event, status, attempt,
                response_status, response_text, error, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (delivery_id, job_id, client_id, url, event, status, attempt,
             response_status, response_text, error, now)
        )
        conn.commit()

    def _update_delivery_status(
        self,
        delivery_id: str,
        status: str,
        attempt: int,
        response_status: Optional[int] = None,
        response_text: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """UPDATE webhook_deliveries
               SET status = ?, attempt = ?, response_status = ?, response_text = ?, error = ?
               WHERE id = ?""",
            (status, attempt, response_status, response_text, error, delivery_id)
        )
        conn.commit()

    def send_webhook(
        self,
        job_id: str,
        client_id: str,
        status: str,
        job_data: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> bool:
        event = f"job.{status}"
        should_notify, url, secret = self._should_notify(client_id, event)

        if not should_notify or not url:
            logger.debug(f"No webhook configured for client {client_id} event {event}")
            return False

        payload_data = {
            "event": event,
            "job_id": job_id,
            "status": status,
            "client_id": client_id,
            "created_at": job_data.get("created_at", ""),
            "completed_at": job_data.get("completed_at"),
            "request": job_data.get("request", {}),
            "result": result,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload_json = json.dumps(payload_data, default=str)

        delivery_id = f"wh_{uuid.uuid4().hex[:12]}"
        self._record_delivery_attempt(
            delivery_id=delivery_id,
            job_id=job_id,
            client_id=client_id,
            url=url,
            event=event,
            status="pending",
            attempt=0,
        )

        max_attempts = settings.webhook_max_attempts
        timeout = httpx.Timeout(settings.webhook_timeout, connect=10.0)
        with httpx.Client(timeout=timeout) as http_client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = self._send_webhook_request(
                        url=url,
                        payload=payload_json,
                        client=http_client,
                        secret=secret,
                        delivery_id=delivery_id,
                        attempt=attempt,
                    )
                    self._update_delivery_status(
                        delivery_id=delivery_id,
                        status="success",
                        attempt=attempt,
                        response_status=response.status_code,
                    )
                    logger.info(f"Webhook sent successfully for job {job_id} to {url}")
                    return True

                except Exception as e:
                    error_msg = self._classify_http_error(e)
                    logger.warning(
                        f"Webhook attempt {attempt}/{max_attempts} failed for job {job_id}: {error_msg}"
                    )
                    self._update_delivery_status(
                        delivery_id=delivery_id,
                        status="retrying" if attempt < max_attempts else "failed",
                        attempt=attempt,
                        error=error_msg,
                    )
                    if attempt < max_attempts:
                        time.sleep(settings.webhook_retry_delay * (2 ** (attempt - 1)))

        logger.error(f"Webhook failed after {max_attempts} attempts for job {job_id}")
        return False

    def _send_webhook_request(
        self,
        url: str,
        payload: str,
        client: httpx.Client,
        secret: Optional[str] = None,
        delivery_id: Optional[str] = None,
        attempt: int = 1,
    ) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"DarkWeb-API-Webhook/{settings.api_version}",
            "X-Webhook-Event": "job.status-change",
            "X-Webhook-Delivery": delivery_id or "",
            "X-Webhook-Attempt": str(attempt),
        }

        if secret:
            signature = self._generate_signature(payload, secret)
            headers["X-Webhook-Signature"] = f"sha256={signature}"

        response = client.post(url, content=payload, headers=headers)
        response.raise_for_status()
        return response

    def get_delivery_attempts(
        self,
        job_id: Optional[str] = None,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        where = "WHERE 1=1"
        params: list = []

        if job_id:
            where += " AND job_id = ?"
            params.append(job_id)
        if client_id:
            where += " AND client_id = ?"
            params.append(client_id)
        if status:
            where += " AND status = ?"
            params.append(status)

        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM webhook_deliveries {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM webhook_deliveries {where} ORDER BY sent_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        return [self._delivery_row_to_dict(row) for row in rows], total

    def retry_failed_deliveries(self, client_id: str) -> int:
        from core.job_manager import job_manager  # late import — avoids circular dep

        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM webhook_deliveries WHERE client_id = ? AND status IN ('failed', 'retrying')",
            (client_id,)
        ).fetchall()

        retried_count = 0
        timeout = httpx.Timeout(settings.webhook_timeout, connect=10.0)
        with httpx.Client(timeout=timeout) as http_client:
            for row in rows:
                delivery = self._delivery_row_to_dict(row)
                config = self.get_webhook_config(delivery["client_id"])
                secret = config["secret"] if config else None
                new_attempt = delivery["attempt"] + 1

                # Reconstruct full payload from original job data
                job_dict = job_manager.get_job(delivery["job_id"]) or {}
                payload_data = {
                    "event": delivery["event"],
                    "job_id": delivery["job_id"],
                    "status": delivery["event"].split(".")[-1],
                    "client_id": delivery["client_id"],
                    "created_at": job_dict.get("created_at", ""),
                    "completed_at": job_dict.get("completed_at"),
                    "request": job_dict.get("request", {}),
                    "result": job_dict.get("result"),
                    "error": job_dict.get("error"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                payload_json = json.dumps(payload_data, default=str)

                try:
                    response = self._send_webhook_request(
                        url=delivery["url"],
                        payload=payload_json,
                        client=http_client,
                        secret=secret,
                        delivery_id=delivery["id"],
                        attempt=new_attempt,
                    )
                    self._update_delivery_status(
                        delivery_id=delivery["id"],
                        status="success",
                        attempt=new_attempt,
                        response_status=response.status_code,
                    )
                    retried_count += 1
                    logger.info(f"Retried webhook delivery {delivery['id']} successfully")

                except Exception as e:
                    self._update_delivery_status(
                        delivery_id=delivery["id"],
                        status="retrying" if new_attempt <= settings.webhook_max_attempts else "failed",
                        attempt=new_attempt,
                        error=self._classify_http_error(e),
                    )

        return retried_count


# Singleton — initialized in main.py lifespan
webhook_manager = WebhookManager(
    db_path=settings.webhook_db_path,
)
