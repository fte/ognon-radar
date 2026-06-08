"""
Shared SQLite connection management for thread-safe singleton managers.
Each thread gets its own connection; all connections are tracked for clean shutdown.
"""
import os
import sqlite3
import threading


class SqliteMixin:
    """Thread-safe SQLite connection management. Subclasses must set self.db_path."""

    db_path: str

    def __init_sqlite__(self) -> None:
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._conn_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            with self._conn_lock:
                self._connections.append(conn)
            self._local.conn = conn
        return self._local.conn

    def _close_all_connections(self) -> None:
        with self._conn_lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
        self._local = threading.local()

    def _ensure_db_dir(self) -> None:
        dir_ = os.path.dirname(self.db_path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)
