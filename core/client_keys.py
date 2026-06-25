"""
Per-client API key store backed by SQLite.
Keys are generated server-side (secrets.token_urlsafe) and map to a client_id.
"""
import secrets
from datetime import datetime, timezone
from typing import Optional

from config import settings
from core.db_mixin import SqliteMixin


class ClientKeyStore(SqliteMixin):
    def __init__(self, db_path: str = ""):
        self.db_path = db_path or settings.job_db_path
        self.__init_sqlite__()

    def startup(self) -> None:
        self._ensure_db_dir()
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_keys (
                api_key   TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

    def create_key(self, client_id: str) -> str:
        api_key = secrets.token_urlsafe(32)
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO client_keys (api_key, client_id, created_at) VALUES (?, ?, ?)",
            (api_key, client_id, created_at),
        )
        conn.commit()
        return api_key

    def get_client_id(self, api_key: str) -> Optional[str]:
        row = self._get_conn().execute(
            "SELECT client_id FROM client_keys WHERE api_key = ?", (api_key,)
        ).fetchone()
        return row["client_id"] if row else None


client_key_store = ClientKeyStore()
